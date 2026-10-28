"""Edit this visualization independently of processor.py, or plot in the notebook."""
import json
import matplotlib.pyplot as plt


def plot_output(iq, metadata, context, measurements):
    """Replace with your range–Doppler, MUSIC, constellation, or other figure."""
    fig, ax = plt.subplots(figsize=(9, 4), constrained_layout=True)
    rms = measurements.get("rms_volts")
    if isinstance(rms, dict) and rms:
        ax.bar(list(rms), list(rms.values()))
        ax.set(ylabel="RMS voltage (V)", xlabel="Antenna channel")
        ax.tick_params(axis="x", rotation=35)
    else:
        ax.axis("off")
        ax.text(0, 1, json.dumps(measurements, indent=2)[:3000],
                va="top", family="monospace", transform=ax.transAxes)
    ax.set_title(f"Processor output · window {metadata['index']} · edit plot_output.py")
    return fig
