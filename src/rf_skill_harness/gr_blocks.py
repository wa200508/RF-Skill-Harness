"""GNU Radio vector streams keep antenna samples aligned under backpressure.

Stream item = one simultaneous complex voltage from every configured channel.
There is one clock and optional pacer. Tags preserve the acquisition clock.
"""
import json
import traceback

from gnuradio import gr
import numpy as np
import pmt

from .archive import Archive
from .results import ResultJournal, RunState, load_processor


class ArchiveSource(gr.sync_block):
    def __init__(self, recording_dir, channels, start_window=0, stop_window=-1,
                 allow_partial=False, state=None):
        gr.sync_block.__init__(self, name="Coherent recorded receiver I/Q",
                               in_sig=None, out_sig=[(np.complex64, (channels,))])
        self.recording_dir = recording_dir
        self.channels = channels
        self.start_window = start_window
        self.stop_window = stop_window
        self.allow_partial = allow_partial
        self.state = state or RunState()
        self.message_port_register_out(pmt.intern("errors"))
        self.archive = None
        self.iterator = None
        self.window = None
        self.cursor = 0
        self.first = True

    def start(self):
        # Constructors only declare signatures: Companion inspects embedded
        # blocks before a recording path or processor has been configured.
        try:
            self.archive = Archive(self.recording_dir, allow_partial=self.allow_partial)
            if self.channels != len(self.archive.channels):
                raise ValueError("Channel count differs from archive; regenerate the GRC project")
            self.iterator = iter(self.archive.windows(start_window=self.start_window,
                stop_window=None if self.stop_window < 0 else self.stop_window))
            stop = self.archive.manifest["windows"] if self.stop_window < 0 else self.stop_window
            self.state.expected_windows = stop - self.start_window
            return True
        except Exception as error:
            self.state.fail(error)
            return False

    def _tags(self, window):
        at = self.nitems_written(0)
        seconds, nanos = divmod(window.timestamp_ns, 1_000_000_000)
        tags = {
            "rx_time": pmt.make_tuple(pmt.from_uint64(seconds), pmt.from_double(nanos / 1e9)),
            "rx_rate": pmt.from_double(float(self.archive.rate)),
            "rx_freq": pmt.from_double(float(self.archive.carrier)),
            "rf_timestamp_ns": pmt.from_uint64(window.timestamp_ns),
            "rf_window": pmt.intern(json.dumps(window.metadata(), allow_nan=False)),
        }
        if self.first:
            tags["rf_context"] = pmt.intern(json.dumps(self.archive.context(), allow_nan=False))
            self.first = False
        for name, value in tags.items():
            self.add_item_tag(0, at, pmt.intern(name), value)

    def work(self, input_items, output_items):
        try:
            if self.window is None:
                self.window = next(self.iterator, None)
                self.cursor = 0
                if self.window is None:
                    self.state.source_done = True
                    return -1  # GNU Radio Python gateway expects an integer, not work_return_t.
                self._tags(self.window)
            count = min(len(output_items[0]), len(self.window.samples) - self.cursor)
            output_items[0][:count] = self.window.samples[self.cursor:self.cursor + count]
            self.cursor += count
            if self.cursor == len(self.window.samples):
                self.window = None
            return count
        except Exception as error:
            self.state.fail(error)
            self.message_port_pub(pmt.intern("errors"), pmt.intern(traceback.format_exc()))
            return -1  # GNU Radio Python gateway expects an integer, not work_return_t.

    def stop(self):
        if self.iterator is not None:
            self.iterator.close()
        self.window = None
        return True


class WindowSkillSink(gr.sync_block):
    """Editable hook receives entire coherent windows, independent of work chunks.

    Outputs JSON results as GNU Radio PMT messages and an optional JSONL journal.
    Replacing this sink with native GNU Radio DSP is also supported: keep channel
    vector alignment and preserve acquisition tags through your flowgraph.
    """
    def __init__(self, recording_dir, channels, processor="", output_dir="",
                 allow_partial=False, state=None):
        gr.sync_block.__init__(self, name="INSERT SIGNAL PROCESSING HERE",
                               in_sig=[(np.complex64, (channels,))], out_sig=None)
        self.recording_dir = recording_dir
        self.channels = channels
        self.processor_spec = processor
        self.output_dir = output_dir
        self.allow_partial = allow_partial
        self.context = None
        self.process = None
        self.journal = None
        self.state = state or RunState()
        self.message_port_register_out(pmt.intern("results"))
        self.message_port_register_out(pmt.intern("errors"))
        self.pending = None
        self.metadata = None
        self.filled = 0
        self.closed = False

    def start(self):
        try:
            self.context = Archive(self.recording_dir, allow_partial=self.allow_partial).context()
            if self.channels != len(self.context["channels"]):
                raise ValueError("Processor channel count differs from receiver configuration")
            self.process = load_processor(self.processor_spec or None)
            if self.output_dir:
                self.journal = ResultJournal(self.output_dir, self.context, processor=self.processor_spec or None)
            return True
        except Exception as error:
            self.state.fail(error)
            return False

    def _finish_window(self):
        self.pending.flags.writeable = False
        value = self.process(self.pending, dict(self.metadata), self.context)
        if not isinstance(value, dict):
            raise ValueError("process_window must return a JSON-compatible dict")
        encoded = json.dumps({"schema_version": 1, **self.metadata,
                              "channels": self.context["channels"], "measurements": value}, allow_nan=False)
        if self.journal:
            self.journal.write(self.metadata, self.context["channels"], value)
        self.message_port_pub(pmt.intern("results"), pmt.intern(encoded))
        self.state.windows += 1
        self.state.samples += len(self.pending)
        self.pending = None
        self.metadata = None
        self.filled = 0

    def work(self, input_items, output_items):
        data = input_items[0]
        base = self.nitems_read(0)
        tags = self.get_tags_in_range(0, base, base + len(data), pmt.intern("rf_window"))
        try:
            starts = {int(t.offset - base): json.loads(pmt.symbol_to_string(t.value)) for t in tags}
            cursor = 0
            while cursor < len(data):
                if cursor in starts:
                    if self.pending is not None:
                        raise ValueError("Next window tag arrived before the coherent window completed")
                    self.metadata = starts[cursor]
                    self.pending = np.empty((self.metadata["samples"], len(self.context["channels"])), dtype=np.complex64)
                    self.filled = 0
                if self.pending is None:
                    raise ValueError("Missing acquisition-window tag; preserve tags in inserted DSP")
                count = min(len(data) - cursor, len(self.pending) - self.filled)
                next_start = min((x for x in starts if x > cursor), default=len(data))
                count = min(count, next_start - cursor)
                self.pending[self.filled:self.filled + count] = data[cursor:cursor + count]
                self.filled += count
                cursor += count
                if self.filled == len(self.pending):
                    self._finish_window()
            return len(data)
        except Exception as error:
            self.state.fail(error)
            self.message_port_pub(pmt.intern("errors"), pmt.intern(traceback.format_exc()))
            return -1  # GNU Radio Python gateway expects an integer, not work_return_t.

    def close(self, status="complete", **details):
        if self.closed:
            return
        self.closed = True
        if self.pending is not None and status == "complete":
            status = "failed"
            details["error"] = "Stream ended with an incomplete coherent window"
            self.state.fail(ValueError(details["error"]))
        if self.journal:
            self.journal.close(status, **details)

    def stop(self):
        # GRC calls this even without the CLI's explicit close. A custom CLI run
        # closes first with its more precise complete/stopped/failed status.
        error = self.state.error
        complete = self.state.source_done and self.state.windows == self.state.expected_windows
        self.close("failed" if error else "complete" if complete else "stopped",
                   **({"error": str(error)} if error else {}))
        return True
