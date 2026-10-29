"""Exported native skill: no harness, recording reader, notebook or simulator."""
from collections import deque
from fractions import Fraction
import importlib.util
import json
import math
from pathlib import Path
import sys
import os
import tempfile

import numpy as np


def load_hook(root, spec):
    filename, _, name = spec.partition(':')
    path = (root / 'design' / filename).resolve()
    if not path.is_relative_to((root / 'design').resolve()):
        raise ValueError('Hook must be inside design/')
    sys.path.insert(0, str(path.parent))
    module_spec = importlib.util.spec_from_file_location('skill_' + name, path)
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    hook = getattr(module, name)
    if not callable(hook):
        raise ValueError('Skill hook must be callable')
    return hook


class Engine:
    def __init__(self, config_json):
        self.config = json.loads(config_json)
        self.root = Path(self.config['skill_root']).resolve()
        self.design = json.loads((self.root / 'skill.json').read_text())
        self.receiver = json.loads(Path(self.config['receiver_config']).read_text())
        self.channels = [n for site in self.receiver['sites'] for n in site['channels']]
        if not self.channels or len(set(self.channels)) != len(self.channels):
            raise ValueError('Unique ordered receiver channels required')
        self.mapping = self.config['stream_channels']
        self.scales = self.config['volts_per_count']
        if set(self.mapping.values()) != set(self.channels) or len(self.mapping) != len(self.channels):
            raise ValueError('Map exactly one native stream ID to each receiver channel')
        if set(self.scales) != set(self.channels) or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in self.scales.values()):
            raise ValueError('Positive calibrated volts_per_count required for every channel')
        self.size = self.config['window_samples']
        if type(self.size) is not int or not 1 <= self.size <= 2_000_000:
            raise ValueError('window_samples must be in 1..2000000')
        self.rate = self.receiver['sample_rate_hz']
        self.carrier = self.receiver['carrier_hz']
        if self.config['iq_sample_rate_hz'] != self.rate or self.config['center_frequency_hz'] != self.carrier:
            raise ValueError('Native scheduled rate/carrier must match receiver configuration')
        self.results = Path(self.config['results_dir']) if self.config.get('results_dir') else None
        if self.results is not None:
            if self.results.exists() and any(self.results.iterdir()):
                raise FileExistsError('Use a new or empty native result directory')
            self.results.mkdir(parents=True, exist_ok=True)
        self.process = load_hook(self.root, self.design['processor'])
        self.report = load_hook(self.root, self.design['reporter'])
        self.parts = {n: deque() for n in self.channels}
        self.count = {n: 0 for n in self.channels}
        self.expected = {}
        self.starts = {}
        self.first_start = {}
        self.last_interval = {}
        self.index = 0
        self.context = {'schema_version': 1, 'channels': self.channels,
            'sample_rate_hz': self.rate, 'carrier_hz': self.carrier,
            'units': 'volts', 'voltage_reference': self.receiver.get('voltage_reference', 'calibrated complex volts'),
            'receiver_config': self.receiver, 'timestamp_clock': 'RF MEL acquisition UTC',
            'acquisition_source': 'ams-gra-rf-mel', 'clipping_metadata_available': False}
        self.context['window_samples'] = self.size
        if self.config.get('transmit'):
            self.context['transmit'] = self.config['transmit']

    def transmit_iq(self):
        """One fixed arbitrary waveform, represented as signed ComplexINT16."""
        tx = self.config['transmit']
        generate = load_hook(self.root, self.design['transmitter'])
        values = np.asarray(generate(self.context, tx['samples']), np.complex64)
        if values.shape != (tx['samples'],) or not np.isfinite(values).all():
            raise ValueError('TX hook must return exactly transmit.samples finite complex samples')
        if np.any(abs(values.real) > 1) or np.any(abs(values.imag) > 1):
            raise ValueError('Normalized TX components must be in [-1, 1]; no implicit clipping')
        pairs = np.column_stack((values.real, values.imag))
        return np.rint(pairs * 32767).astype('<i2').tobytes()

    def consume(self, payload, metadata_json):
        native = json.loads(metadata_json)
        streams = native['stream_ids']
        if len(streams) != 1 or str(streams[0]) not in self.mapping:
            raise ValueError('ComplexINT16 callback needs exactly one configured stream ID; ambiguous multiplexing is unsupported')
        channel = self.mapping[str(streams[0])]
        raw = np.frombuffer(payload, dtype='<i2')
        if not len(raw) or len(raw) % 2:
            raise ValueError('Complete nonempty ComplexINT16 samples required')
        # The calibration is volts per signed integer count, never an assumed dBFS conversion.
        values = (raw[::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) * np.float32(self.scales[channel])
        events = native['events']
        if not events:
            raise ValueError('MEL acquisition event metadata is required')
        first_fs = native['seconds'] * 10**15 + native['fractional_fs']
        offset = 0
        for event in events:
            if event['sample_rate_hz'] != self.rate or event['carrier_hz'] != self.carrier:
                raise ValueError('MEL event rate/carrier differs from receiver configuration')
            samples = Fraction(event['duration_fs']) * Fraction(str(self.rate)) / 10**15
            if samples.denominator != 1 or samples <= 0:
                raise ValueError('MEL event duration must describe an integer sample count')
            count = int(samples)
            if offset + count > len(values):
                raise ValueError('MEL event metadata exceeds callback samples')
            start = first_fs + event['start_fs'] - events[0]['start_fs']
            self._append(channel, start, values[offset:offset + count], native)
            offset += count
        if offset != len(values):
            raise ValueError('MEL metadata does not account for every callback sample')
        reports = []
        while all(n >= self.size for n in self.count.values()):
            starts = list(self.starts.values())
            if len(set(starts)) != 1:
                raise ValueError('Antenna acquisition epochs differ; no coherent window can be fabricated')
            start = starts[0]
            taken = {n: self._take(n) for n in self.channels}
            iq = np.column_stack([taken[n][0] for n in self.channels]).astype(np.complex64, copy=False)
            iq.flags.writeable = False
            self.context.setdefault('first_timestamp_ns', int(start // 10**6))
            metadata = {'index': self.index, 'timestamp_ns': int(start // 10**6),
                'timestamp_femtoseconds': str(start.numerator) if start.denominator == 1 else str(start),
                'sample_start': self.index * self.size, 'samples': self.size,
                'clipped_component_fraction': {n: None for n in self.channels},
                'native_mel_by_channel': {n: taken[n][1] for n in self.channels}}
            result = self.process(iq, dict(metadata), self.context)
            if not isinstance(result, dict):
                raise ValueError('process_window must return a finite JSON-compatible dict')
            json.dumps(result, allow_nan=False)
            signal_entries = self.report(result, dict(metadata), self.context)
            if not isinstance(signal_entries, list) or any(not isinstance(v, dict) for v in signal_entries):
                raise ValueError('signal_reports must return a list of UCI Signal dictionaries')
            if signal_entries:
                reports.append({'signals': signal_entries, 'timestamp_ns': metadata['timestamp_ns']})
            if self.results is not None:
                # Publish a complete file atomically, without replacing an older
                # experiment even if another process races for the same name.
                with tempfile.NamedTemporaryFile(mode='w', dir=self.results, delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        json.dump({'metadata': metadata, 'measurements': result}, stream, allow_nan=False)
                        stream.flush()
                        os.link(temporary, self.results / f'{self.index:012d}.json')
                    finally:
                        temporary.unlink(missing_ok=True)
            self.index += 1
        return json.dumps(reports, allow_nan=False)

    def _append(self, channel, start, values, native):
        start = Fraction(start)
        if channel in self.expected and abs(start - self.expected[channel]) > 1:
            tx = self.config.get('transmit')
            gap = tx['sequence_duration_fs'] - self.config['receive_duration_fs'] if tx else None
            if not (tx and self.count[channel] == 0 and abs(start - self.expected[channel] - gap) <= 1):
                raise ValueError('MEL acquisition gap/overlap; refusing to concatenate discontinuous I/Q')
        if self.count[channel] == 0:
            self.starts[channel] = start
        self.first_start.setdefault(channel, start)
        interval = tuple(native.get(k) for k in ('va_instance_id', 'job_details_id', 'job_interval_id'))
        if channel in self.last_interval and interval != self.last_interval[channel] and native.get('phase_coherence_with_prior') is False and not (self.config.get('transmit') and self.count[channel] == 0):
            sample_offset = (start - self.first_start[channel]) * Fraction(str(self.rate)) / 10**15
            if sample_offset.denominator != 1 or sample_offset.numerator % self.size:
                raise ValueError('MEL phase discontinuity inside a processing window')
        self.last_interval[channel] = interval
        self.expected[channel] = start + len(values) * Fraction(10**15, 1) / Fraction(str(self.rate))
        self.parts[channel].append((values, native))
        self.count[channel] += len(values)
        if self.count[channel] > max(self.size * 2, 131072):
            raise ValueError('Coherent channel backlog exceeded; a channel is missing or the skill cannot keep up')

    def _take(self, channel):
        remaining = self.size
        pieces = []
        provenance = []
        while remaining:
            part, native = self.parts[channel].popleft()
            take = min(remaining, len(part))
            pieces.append(part[:take])
            provenance.append({'sample_offset_in_window': self.size - remaining, 'samples': take,
                               'metadata': native})
            if take < len(part):
                self.parts[channel].appendleft((part[take:], native))
            remaining -= take
        self.count[channel] -= self.size
        self.starts[channel] += self.size * Fraction(10**15, 1) / Fraction(str(self.rate))
        return np.concatenate(pieces), provenance
