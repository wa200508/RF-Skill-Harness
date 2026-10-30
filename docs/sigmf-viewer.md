# SigMF Viewer inside GNU Radio World

The main interface remains **GNU Radio World**: the graph editor, standard DSP,
Embedded Python editor, browser runtime and native AMS-GRA export are unchanged.
A peer **SigMF Viewer** tab embeds Brian Day's actual
[briday1/sigmf-viewer](https://github.com/briday1/sigmf-viewer), hosted by
[briday1/sigvue](https://github.com/briday1/sigvue), on the same server and port.
It is not a screenshot of a desktop application or a reimplementation of its plots.

## Inspect recordings

Click **SigMF Viewer** for the catalog. It includes a workspace for each capture
supplied with `--recordings`, plus **Signal inspections** for saved development
snapshots. Original receiver metadata is read-only: annotation writing is disabled
for the source-capture workspaces. Batch PNG/tiled-viewer outputs go under the
skill's `inspections/batch-results/`, not into mounted recordings.

Click **View I/Q** in the RF toolbar to inspect the selected acquisition window.
This verifies the source window, writes an exact interleaved SigMF pair with
channel names and acquisition provenance, and opens it directly in the viewer.
The source files remain unchanged. Multi-channel vectors retain sample/channel
order; the viewer provides its normal channel selector.

Brian's viewer supplies spectrum/waterfall views, exact-window scrubbing, zoom,
FFT/overlap controls, colormaps, annotations for inspection copies, and durable
whole-recording PNG/tiled HTML batch outputs. Its control layout and processing
are upstream SigMF Viewer/Sigvue features. Existing Qt plotting sinks in World
remain available for designs that use them.

## Inspect an intermediate processing output

1. Choose a **Signal inspection tap** in the RF toolbar. The list is derived from
   the edited graph's enabled complex64 and float32 output ports. Refresh it by
   focusing the dropdown after graph/port changes.
2. Set **Tap sample rate Hz** when the stage changes sample rate. Blank means the
   selected input rate; decimation/interpolation in arbitrary user code cannot be
   inferred reliably.
3. Click **Run tap**. The selected graph runs in GNU Radio World's normal browser
   runtime. A temporary SigMF Sink records the chosen output. Vector outputs are
   flattened in interleaved sample/channel order.
4. On finite-window completion, the exact output is saved and opens automatically
   in **SigMF Viewer**. **Finish & view** can stop/finalize a running recording or
   reopen the last inspection. Finishing early records only the samples actually
   produced; it does not claim the complete input was processed.

These are the actual full-resolution stream samples from World's SigMF Sink,
not reconstructed points from a plotting sink. No FFT-bin interpolation or plot
raster is used to create the saved I/Q. Completed samples are sent as a binary
payload to the local server. The bounded in-memory snapshot limit is **256 MiB**;
this development feature is not a continuous indefinite recorder.

Files persist as:

```text
my-rf-skill/
  skill.grc
  inspections/
    <inspection-id>/
      signal.sigmf-data
      signal.sigmf-meta
    batch-results/
```

The metadata preserves sample rate, RF center frequency, channel labels, input
window metadata, receiver configuration, exact parent timestamp in nanoseconds,
parent sample offset, tap block/port and declared units. It does not invent a UTC
epoch. A processed output's group delay or changed sample clock is not inferred:
its parent acquisition time is provenance, not a claim about output timing.

The upstream viewer labels floating-point power **dBFS**, with unit amplitude as
its reference. Input-referred voltage data is not automatically normalized; for
those samples this is a unit-voltage reference, not calibrated dBm or dBm/Hz.
Derived-stage units are developer-defined. Set common display limits when
comparing absolute levels across recordings whose automatic ranges differ.

## Separation from the exported skill

The extra recording sink is inserted only in the run-time graph copy. It never
enters saved `skill.grc`. Inspections, viewer packages, data paths and replay
selection are excluded from native RF MEL/UCI exports. The same edited processing
can therefore be tested on another collect and deployed independently.

The browser/native installation includes pinned upstream Python dependencies;
no separate viewer process or second port is launched. Pip uses pinned public
source archives, so the installation does not require git. Browser assets and
normal scientific dependencies remain part of the existing release/install flow.

## Code and provenance

- `world/rf-sigmf.ts`: peer tab, output-port selection and exact browser recording.
- `scripts/build_world.py`: checked hooks into World's existing tab controller and
  SigMF Sink output callback; GNU Radio's compiled DSP is unchanged.
- `src/rf_skill_harness/sigmf_inspector.py`: SigMF artifacts, Brian's workspace,
  same-origin HTTP handler integration and read-only source boundary.
- `src/rf_skill_harness/world_server.py`: acquisition metadata and inspection API.
- `tests/test_sigmf_inspector.py`: exact multi-channel samples, provenance and
  source immutability.
- Browser qualification tests the real upstream UI, actual output sample payloads
  after an edited gain, and the independent native export.

Pinned upstreams: SigMF Viewer **0.3.4**, commit
`3d4f90a3a8dbe59d005baeff57c4a3593489aec3`; Sigvue **2026.81**, commit
`53c28586587ff6e0cff52775375d9c207a4ad761`. They are installed from their own
repositories as dependencies. Their original provenance is retained; this
project does not assign its own license to those upstream packages.
