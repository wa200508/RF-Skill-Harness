"""Copy this file into your project and replace process_window with your RF skill."""
import numpy as np


def process_window(iq, metadata, context):
    """INSERT SIGNAL PROCESSING HERE.

    iq: read-only complex64 volts, shape [samples, antenna channels].
    metadata: original timestamp_ns, sample_start, index and per-channel clipping.
    context: sample rate/carrier, ordered channels and complete receiver_config.

    State can live on a processor object: export any callable with this signature.
    Return a JSON-compatible dict. No simulator, ray path or target truth is used.
    Radar gate/chirp parameters are in context['receiver_config']['acquisition'].
    The statistics below include every stored sample, including zero-filled gaps.
    """
    power = np.mean(np.abs(iq.astype(np.complex128)) ** 2, axis=0)
    return {"algorithm": "insert-signal-processing-here",
            "rms_volts": {name: float(np.sqrt(p)) for name, p in zip(context["channels"], power)},
            "peak_volts": {name: float(np.max(np.abs(iq[:, i])))
                           for i, name in enumerate(context["channels"])}}
