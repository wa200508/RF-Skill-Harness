"""Portable exported-design checks; no live radio or simulation is required."""
from fractions import Fraction
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import numpy as np


def test_declared_design_and_native_window_contract(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'export-manifest.json').read_text())
    for filename, digest in manifest['files_sha256'].items():
        assert sha256((root / 'design' / filename).read_bytes()).hexdigest() == digest
    spec = importlib.util.spec_from_file_location('engine', root / 'runtime/engine.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads((root / 'config/runtime.json').read_text())
    config.update(skill_root=str(root), receiver_config=str(root / 'config/receiver.json'), results_dir=str(tmp_path / 'results'))
    engine = module.Engine(json.dumps(config))
    original = engine.process
    observed = []
    def checked(iq, metadata, context):
        assert iq.dtype == np.complex64 and not iq.flags.writeable
        assert iq.shape == (config['window_samples'], len(context['channels']))
        expected_fs = Fraction(1_700_000_000_000_000_007 * 10**6) + Fraction(metadata['index'] * config['window_samples'] * 10**15, 1) / Fraction(str(engine.rate))
        assert metadata['timestamp_ns'] == expected_fs // 10**6
        observed.append(metadata)
        return original(iq, metadata, context)
    engine.process = checked
    # This checks the declared design on zero input, not physical RF accuracy.
    # Missing-target/zero-energy behavior is part of the developer's skill.
    epoch_fs = 1_700_000_000_000_000_007 * 10**6
    offset = 0
    while offset < config['window_samples']:
        count = config['receive_event_samples']
        duration = Fraction(count * 10**15, 1) / Fraction(str(engine.rate))
        start = epoch_fs + Fraction(offset * 10**15, 1) / Fraction(str(engine.rate))
        assert duration.denominator == start.denominator == 1, 'Test events must align to MEL femtoseconds'
        for stream in config['stream_channels']:
            native = {'stream_ids': [int(stream)], 'seconds': int(start // 10**15),
                'fractional_fs': int(start % 10**15), 'events': [{'start_fs': 0,
                'duration_fs': int(duration), 'sample_rate_hz': engine.rate, 'carrier_hz': engine.carrier}]}
            reports = json.loads(engine.consume(np.zeros((count, 2), '<i2').tobytes(), json.dumps(native)))
            assert isinstance(reports, list)
        offset += count
    assert len(observed) >= 1
    assert (tmp_path / 'results/000000000000.json').is_file()
