#!/usr/bin/env python3
"""Open a prepared project with the selected GNU Radio Python and receiver library."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import webbrowser

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("graph", type=Path)
args = parser.parse_args()
try:
    from gnuradio import gr
except ImportError:
    parser.error("Selected Python lacks GNU Radio; use RF_HARNESS_PYTHON=/usr/bin/python3 or a --system-site-packages virtual environment")
companion = shutil.which("gnuradio-companion")
if not companion:
    parser.error("Install GNU Radio Companion 3.10 on the host")
graph = args.graph.expanduser().resolve()
if not graph.is_file():
    parser.error("Graph does not exist; run prepare first")
os.chdir(graph.parent)
if (graph.parent / "timeline.html").is_file():
    webbrowser.open((graph.parent / "timeline.html").as_uri())
raise SystemExit(subprocess.call([sys.executable, companion, str(graph)]))
