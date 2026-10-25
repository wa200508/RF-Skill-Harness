"""Headless GNU Radio execution with explicit completion and failure reporting."""
import math
import threading
import time

from gnuradio import blocks, gr

from .archive import Archive
from .gr_blocks import ArchiveSource, WindowSkillSink
from .results import RunState


def run(directory, output, *, processor=None, speed=0., start_window=0,
        stop_window=None, allow_partial=False):
    if not math.isfinite(speed) or speed < 0:
        raise ValueError("Speed must be finite and nonnegative; 0 is unpaced")
    archive = Archive(directory, allow_partial=allow_partial)
    stop = archive.manifest["windows"] if stop_window is None else stop_window
    if not 0 <= start_window < stop <= archive.manifest["windows"]:
        raise ValueError("Invalid replay window selection")
    state = RunState()
    channels = len(archive.channels)
    source = ArchiveSource(str(archive.path), channels, start_window, stop, allow_partial, state)
    sink = WindowSkillSink(str(archive.path), channels, processor or "", str(output), allow_partial, state)
    flow = gr.top_block("Recorded RF skill development")
    if speed:
        pacer = blocks.throttle(gr.sizeof_gr_complex * channels, archive.rate * speed, True)
        flow.connect(source, pacer, sink)
    else:
        flow.connect(source, sink)
    started = time.monotonic()
    status = "failed"
    waiter = None
    try:
        flow.start()
        waiter = threading.Thread(target=flow.wait, daemon=True)
        waiter.start()
        # No total mission deadline. Only EOF, a genuine processing failure or
        # operator interruption stops a healthy run, regardless of wall time.
        while waiter.is_alive():
            if state.error is not None:
                raise RuntimeError(f"RF replay failed: {type(state.error).__name__}: {state.error}") from state.error
            waiter.join(.05)
        if state.error is not None:
            raise RuntimeError(f"RF replay failed: {type(state.error).__name__}: {state.error}") from state.error
        if state.windows != stop - start_window:
            raise RuntimeError("GNU Radio ended before every selected coherent window was processed")
        status = "complete"
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except BaseException as error:
        state.fail(error)
        raise
    finally:
        flow.stop()
        if waiter:
            waiter.join()
        details = {"wall_seconds": time.monotonic() - started, "playback_speed": speed,
                   "start_window": start_window, "stop_window": stop,
                   "gnuradio_version": gr.version()}
        if state.error is not None:
            details["error"] = f"{type(state.error).__name__}: {state.error}"
        sink.close(status, **details)
        # GNU Radio may have called stop() before we wrote final run details.
        if sink.journal:
            sink.journal.status(status, **details)
    return {"status": status, "windows": state.windows,
            "samples_per_channel": state.samples, **details}
