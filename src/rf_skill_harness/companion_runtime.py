"""Portable GNU Radio acquisition boundary, copied to a skill as skill_io.py.

No recording reader or simulator belongs here. The caller binds voltage arrays
and acquisition metadata before constructing the generated GNU Radio graph.
This first profile executes independent finite acquisition windows.
"""
from contextvars import ContextVar
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import threading

import numpy as np
from gnuradio import gr
import pmt

_binding = ContextVar('rf_skill_window', default=None)
_qt_app = None


class Session:
    def __init__(self, iq, metadata, context, tx_samples, measure):
        self.iq, self.metadata, self.context = iq, metadata, context
        self.tx_samples, self.measure = tx_samples, measure
        self.measurements, self.tx, self.error = [], [], None


def session():
    value = _binding.get()
    if value is None:
        raise RuntimeError('RF endpoints require an acquisition binding; use the Companion launcher or native RF MEL host')
    return value


class RxSource(gr.sync_block):
    def __init__(self, channels=1):
        self.bound = session()
        self.offset = 0
        self.channels = channels
        if channels != self.bound.iq.shape[1]:
            raise ValueError('Graph channel_count differs from the bound receiver configuration')
        gr.sync_block.__init__(self, 'RF receive endpoint', [], [(np.complex64, channels)])

    def work(self, inputs, outputs):
        try:
            if not self.bound.measure:
                return -1  # TX preparation never invents a received voltage window.
            remaining = len(self.bound.iq) - self.offset
            if not remaining:
                return -1
            count = min(remaining, len(outputs[0]))
            if self.offset == 0:
                meta, ctx = self.bound.metadata, self.bound.context
                for key, value in [('rf_window', json.dumps(meta)), ('rf_context', json.dumps(ctx))]:
                    self.add_item_tag(0, self.nitems_written(0), pmt.intern(key), pmt.intern(value))
                stamp = int(meta['timestamp_ns'])
                self.add_item_tag(0, self.nitems_written(0), pmt.intern('rf_timestamp_ns'), pmt.from_uint64(stamp))
                seconds, remainder = divmod(stamp, 10**9)
                self.add_item_tag(0, self.nitems_written(0), pmt.intern('rx_time'),
                    pmt.make_tuple(pmt.from_uint64(seconds), pmt.from_double(remainder / 10**9)))
                self.add_item_tag(0, self.nitems_written(0), pmt.intern('rx_rate'), pmt.from_double(float(ctx['sample_rate_hz'])))
                self.add_item_tag(0, self.nitems_written(0), pmt.intern('rx_freq'), pmt.from_double(float(ctx['carrier_hz'])))
            outputs[0].reshape(-1, self.channels)[:count] = self.bound.iq[self.offset:self.offset + count]
            self.offset += count
            return count
        except Exception as error:
            self.bound.error = error
            return -1


def _hook(spec):
    filename, _, name = spec.partition(':')
    path = (Path(__file__).parent / filename).resolve()
    if not path.is_relative_to(Path(__file__).parent.resolve()) or not name.isidentifier():
        raise ValueError('Window hook must be a local design file:function')
    description = importlib.util.spec_from_file_location('_grc_window_hook', path)
    module = importlib.util.module_from_spec(description)
    sys.modules[description.name] = module
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    return getattr(module, name)


class WindowSink(gr.sync_block):
    def __init__(self, channels=1, processor='processor.py:process_window'):
        self.bound = session()
        self.channels = channels
        self.parts, self.count = [], 0
        self.process = _hook(processor)
        gr.sync_block.__init__(self, 'RF window measurements', [(np.complex64, channels)], [])

    def work(self, inputs, outputs):
        try:
            values = inputs[0].reshape(-1, self.channels)
            self.parts.append(values.copy())
            self.count += len(values)
            if self.count > len(self.bound.iq):
                raise ValueError('The finite-window profile requires sample-count-preserving RX processing')
            if self.count == len(self.bound.iq) and self.bound.measure:
                iq = np.concatenate(self.parts).astype(np.complex64, copy=False)
                if not np.isfinite(iq).all():
                    raise ValueError('Nonfinite processed I/Q')
                iq.flags.writeable = False
                result = self.process(iq, dict(self.bound.metadata), self.bound.context)
                if not isinstance(result, dict):
                    raise ValueError('Window measurements must be a dictionary')
                json.dumps(result, allow_nan=False)
                self.bound.measurements.append(result)
            return len(values)
        except Exception as error:
            self.bound.error = error
            return -1


class TxSink(gr.sync_block):
    def __init__(self):
        self.bound = session()
        self.count = 0
        gr.sync_block.__init__(self, 'RF transmit waveform', [np.complex64], [])

    def work(self, inputs, outputs):
        try:
            values = inputs[0].copy()
            self.count += len(values)
            if self.count > self.bound.tx_samples or not np.isfinite(values).all():
                raise ValueError('TX must be finite and bounded by tx_samples; insert a Head before TX taps')
            # The waveform contract is normalized complex INT16 full scale.
            if np.any(np.abs(values.real) > 1) or np.any(np.abs(values.imag) > 1):
                raise ValueError('TX components exceed normalized full scale [-1, 1]')
            self.bound.tx.append(values)
            return len(values)
        except Exception as error:
            self.bound.error = error
            return -1


def run_graph(iq, metadata, context, *, tx_samples=256, measure=True, graph_module='skill_flowgraph', hold_plots=False):
    """Run the same generated DSP for replay and native acquisition.

    Native streaming-block history is reset at each acquisition window. A DSP
    needing cross-window history must use an explicitly stateful adapter instead.
    """
    if iq.ndim != 2 or not 0 < len(iq) <= 2_000_000:
        raise ValueError('Expected a finite coherent [sample, channel] window')
    global _qt_app
    bound = Session(iq, metadata, context, tx_samples, measure)
    token = _binding.set(bound)
    flow = None
    application = None
    try:
        module = importlib.import_module(graph_module)
        declared = inspect.signature(module.skill_flowgraph).parameters['channel_count'].default
        if declared != iq.shape[1]:
            raise ValueError(f'This compiled graph expects {declared} coherent channels; acquisition has {iq.shape[1]}')
        if hasattr(module, 'Qt'):
            _qt_app = module.Qt.QApplication.instance() or module.Qt.QApplication([])
            application = _qt_app
        flow = module.skill_flowgraph(channel_count=iq.shape[1],
            samp_rate=context['sample_rate_hz'], carrier_hz=context['carrier_hz'], tx_samples=tx_samples)
        if application:
            flow.show()
        flow.start()
        waiter = threading.Thread(target=flow.wait, daemon=True)
        waiter.start()
        while waiter.is_alive():
            if application:
                application.processEvents()
            if bound.error:
                raise RuntimeError(str(bound.error)) from bound.error
            waiter.join(.01)
        if bound.error:
            raise RuntimeError(str(bound.error)) from bound.error
        if measure and len(bound.measurements) != 1:
            raise ValueError('Expected exactly one complete RF window measurement; check RX sample counts and endpoints')
        tx = np.concatenate(bound.tx) if bound.tx else np.empty(0, np.complex64)
        if len(tx) and len(tx) != tx_samples:
            raise ValueError('TX waveform length differs from configured tx_samples')
        if application and hold_plots:
            flow.setWindowTitle('RF Skill — completed window; close to return to editing')
            while flow.isVisible():
                application.processEvents()
                threading.Event().wait(.02)
        return (bound.measurements[0] if measure else {}), tx
    finally:
        if flow is not None:
            flow.stop()
            flow.wait()
            if application:
                flow.close()
                application.processEvents()
        _binding.reset(token)
