# What the RF skill receives

The accepted layout is the coherent `airsim-rf-sample-recording-v1` receiver
archive: per-channel SigMF `cf32_le` files, `manifest.json`, `receiver-config.json`
and `windows.jsonl`. This is a reader for that specific coherent layout, not a
general importer for every SigMF dataset. No source package is imported.

| Input | Developer-facing information |
| --- | --- |
| `.sigmf-data` | Exact little-endian float32 I, float32 Q volts; phase and amplitude preserved |
| `.sigmf-meta` | Standard datatype/rate and capture-frequency metadata, cross-checked with the manifest |
| `manifest.json` | Channel order, sample/carrier rate, voltage units, published window/sample totals, completion status |
| `receiver-config.json` | Complete captured receiver configuration: ordered sites/elements, geometry, calibration and any acquisition settings |
| `windows.jsonl` | Exact acquisition timestamp in integer nanoseconds, original sample offset, coherent window size, per-channel clipping fraction and sample SHA-256 |

There is one stream item per simultaneous antenna sample. An eight-element array
is an eight-complex-number vector at the original sample rate, not eight
independently paced sources. The default processor receives a read-only array
with shape `[samples_in_window, channels]`. Channel index always follows the
manifest/configuration order. There is no silent channel reorder or padding.
Calibration coefficients are passed through as metadata; the replay source does
not apply another calibration or gain to the already recorded voltages.

## GNU Radio tags

Tags apply at the first vector item of each original acquisition window:

| Tag | PMT value | Meaning |
| --- | --- | --- |
| `rx_time` | tuple `(uint64 seconds, double fractional_seconds)` | Original acquisition clock, using the usual GNU Radio/UHD representation |
| `rf_timestamp_ns` | uint64 | The exact integer timestamp; use this when nanosecond precision matters |
| `rx_rate` | double | Physical recorded sample rate, unaffected by playback speed |
| `rx_freq` | double | Recorded RF carrier, including the mmWave carrier for dechirped radar |
| `rf_window` | JSON string symbol | `index`, `timestamp_ns`, original `sample_start`, `samples`, channel clipping fractions |
| `rf_context` | JSON string symbol, first replay item only | Full receiver context/configuration and manifest |

PMT JSON string symbols can be decoded with
`json.loads(pmt.symbol_to_string(tag.value))`. Result messages on the processor's
`results` port use the same JSON-symbol encoding. They carry the original window
metadata, ordered channels and your returned `measurements` object.

These timestamps do **not** assert UTC/GPS time. The recorded clock may have an
arbitrary epoch. Seeking resets the GNU Radio output item offset to zero while
preserving original timestamps, original sample offsets and source window IDs.
No acquisition timestamps are inferred from playback wall time.

## RF acquisition schedule

`prepare` automatically creates an interactive `timeline.html` and a compact
`schedule.json`. A timeline-only export is also available:

```bash
rf-harness timeline /path/to/iq --output workspace/timing
```

Receive/collection windows come from the journal. For FMCW, the schedule uses
`receiver_config.acquisition`: chirp duration determines the configured TX-on
interval, PRI sets repetition, and gate start/sample count determine the RX/ADC
interval. `chirp_origin: first window timestamp` anchors the configured phase.
FMCW TX and RX overlap. Sample-clock nanoseconds remain independent of playback
speed, processor latency and GNU Radio's buffer scheduling.

The plot labels **recorded** acquisition windows separately from **configured**
TX/RX events. The current archive does not journal actual per-window TX enable
state, so the timeline cannot prove that RF was transmitted for every planned
chirp. Missing timing/phase is reported and only recorded windows are shown;
the harness does not invent those fields. ESM/receive-only captures show receive
windows without inferring activity of unknown emitters.

The time axis uses relative acquisition nanoseconds, while the original epoch
is retained as an integer string and displayed with JavaScript BigInt. Repeated
windows are run-length encoded; the viewer derives individual chirps only within
the visible time span and aggregates dense events when zoomed out. Ten minutes
of 25 kHz radar need not materialize millions of event objects.

## Radar data

`receiver_config.acquisition` describes the chirp slope (bandwidth/duration), PRI,
ADC sample count and gate start. Carrier and ADC sample rate come from the
receiver configuration. The data is already **dechirped complex ADC volts**;
it is not a sampled 77 GHz carrier. PRI gaps are stored as zeros. Those zeros
are timing placeholders, not measured quiet/noise samples. Select the gate using
the captured settings before computing range/Doppler or gate-only power.

The original noise, clutter and target contributions are combined. This archive
does not contain separable clean-target, clutter-only or noise-only channels.
No color-scale selection changes the bytes replayed here.

## What is known, and what is not

The harness passes the **entire captured receiver configuration** unchanged.
It does not invent a noise figure, antenna pattern, waveform, calibration,
position time series or measurement reference absent from that configuration.
`voltage_reference` distinguishes input-referred SDR volts from radar ADC volts;
missing reference metadata follows the archive's input-referred default.

Site centers in receiver configuration are the captured layout/initial
configuration; the current archive does not carry per-window moving-platform
navigation. Do not treat a static configuration position as current flight truth.
Ray paths, terrain materials, target labels/RCS, emitter truth, flight plans,
camera frames and previous algorithm outputs are outside this receiver contract.
Keep any evaluation truth separate from an operational RF skill.

## Integrity and memory

Metadata/layout/timing are checked before processing. Each selected window's
sample hashes and finite values are checked **before** that window enters GNU
Radio. Full-file hash verification is also available with `inspect --verify`.
Memory is bounded by a few coherent windows and GNU Radio buffers, rather than
the complete recording; the default hook creates a temporary power-statistics
array. Your own processor can allocate or retain additional data.

Complete/stopped captures are accepted normally. `--allow-partial` uses only
manifest-published windows of an interrupted archive, ignores unpublished tails,
and still checks published hashes/lengths. Stop acquisition or use a stable copy
first. A changing manifest is rejected. Power-loss durability and repair of
corrupt published data are not provided.

References: [SigMF](https://sigmf.org/),
[GNU Radio stream tags](https://wiki.gnuradio.org/index.php/Stream_Tags),
[GNU Radio PMTs](https://wiki.gnuradio.org/index.php/Polymorphic_Types_(PMTs)),
[source capture format](https://github.com/wa200508/airsim-rf/blob/main/docs/iq-replay.md).
