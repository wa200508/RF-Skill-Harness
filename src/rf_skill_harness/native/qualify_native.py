"""Exercise an exported design through real native factory/callback/OWP APIs.

The provider is an interface fixture, not Squall or a physical acquisition device.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--expect-signal', action='store_true')
    parser.add_argument('--idle-seconds', type=float, default=6, help='Keep the optional position subscription silent beyond the transaction timeout')
    args = parser.parse_args()
    for name in ('rf_skill_harness', 'airsim_rf', 'projectairsim', 'sionna', 'mitsuba', 'gnuradio'):
        assert importlib.util.find_spec(name) is None, 'Qualify without the harness/simulator/GNU Radio: ' + name
    root = args.bundle.resolve()
    config = json.loads((root / 'config/runtime.json').read_text())
    receiver = json.loads((root / 'config/receiver.json').read_text())
    rate = receiver['sample_rate_hz']
    count = config['window_samples']
    if count > 65536:
        raise ValueError('This integration fixture uses complete windows <=65536 samples')
    epoch = 1_700_000_000_000_000_007
    duration_ns = count * 10**9 // rate
    assert duration_ns * rate == count * 10**9
    with tempfile.TemporaryDirectory(prefix='native-rf-qualification-') as temporary:
        work = Path(temporary)
        frames = []
        for window in range(2):
            for stream in config['stream_channels']:
                frames.append({'stream_id': int(stream), 'timestamp_ns': epoch + window * duration_ns,
                    'samples': [[int(stream) * 10, -int(stream) * 10]] * count})
        (work / 'provider.json').write_text(json.dumps({'sample_rate_hz': rate, 'frames': frames}))
        (work / 'registry.json').write_text(json.dumps({'1': {'config': str(work / 'provider.json'),
            'library': str(args.build_dir.resolve() / 'libtest_mel.so')}}))
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        config.update(skill_root=str(root), receiver_config=str(root / 'config/receiver.json'),
            rf_mel_registry_path=str(work / 'registry.json'), rf_mel_id=1,
            owp_uri=f'ws://127.0.0.1:{port}', owp_service_id='rf-fm-demod-skill',
            position_subscription_id='pos1', results_dir=str(work / 'results'),
            report_rate_limit_hz=1_000_000)
        (work / 'runtime.json').write_text(json.dumps(config))
        with (work / 'owp.log').open('w') as router_log, (work / 'skill.log').open('w') as skill_log:
            router = subprocess.Popen([str(args.build_dir / 'owp_fixture'), '--listen', f'127.0.0.1:{port}',
                '--capture', str(work / 'owp.jsonl'), '--ready-file', str(work / 'ready')], stdout=router_log, stderr=subprocess.STDOUT)
            skill = None
            try:
                deadline = time.monotonic() + 10
                while not (work / 'ready').exists():
                    if router.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('OWP fixture failed: ' + (work / 'owp.log').read_text())
                    time.sleep(.02)
                skill = subprocess.Popen([str(args.build_dir / 'rf-skill'), '--config', str(work / 'runtime.json')], stdout=skill_log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + 15
                while len(list((work / 'results').glob('*.json'))) < 2:
                    if skill.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Native skill failed: ' + (work / 'skill.log').read_text())
                    time.sleep(.02)
                entries = [json.loads(p.read_text()) for p in sorted((work / 'results').glob('*.json'))]
                assert [r['metadata']['timestamp_ns'] for r in entries] == [epoch, epoch + duration_ns]
                assert [r['metadata']['sample_start'] for r in entries] == [0, count]
                # The heartbeat interval is independent of the data processing rate.
                deadline = time.monotonic() + 5
                while True:
                    captured = [json.loads(line)['frame'] for line in (work / 'owp.jsonl').read_text().splitlines()]
                    published = [json.loads(frame.split(' ', 2)[2]) for frame in captured if frame.startswith('PUB ')]
                    statuses = [d['ServiceStatus']['MessageData']['ServiceState'] for d in published if 'ServiceStatus' in d]
                    if 'NORMAL' in statuses or time.monotonic() > deadline:
                        break
                    time.sleep(.02)
                assert 'INITIALIZING' in statuses and 'NORMAL' in statuses, statuses
                signals = [d['SignalReport'] for d in published if 'SignalReport' in d]
                if args.expect_signal:
                    assert signals, 'The fixture design did not publish its expected detection'
                expected_time = datetime.fromtimestamp(epoch // 10**9, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + '.000000007Z'
                for message in signals:
                    UUID(message['MessageHeader']['ServiceID']['UUID'])
                    UUID(message['MessageData']['SignalReportID']['UUID'])
                    assert message['MessageHeader']['Timestamp'] in [expected_time,
                        datetime.fromtimestamp((epoch + duration_ns) // 10**9, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{(epoch + duration_ns) % 10**9:09d}Z']
                    for signal_entry in message['MessageData']['Signal']:
                        UUID(signal_entry['SignalID']['UUID'])
                deadline = time.monotonic() + args.idle_seconds
                while time.monotonic() < deadline:
                    if skill.poll() is not None:
                        raise RuntimeError('An idle subscription stopped the skill: ' + (work / 'skill.log').read_text())
                    time.sleep(.05)
            finally:
                if skill is not None and skill.poll() is None:
                    skill.send_signal(signal.SIGTERM)
                    try:
                        skill.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        skill.kill(); skill.wait()
                        raise RuntimeError('Native skill did not stop cleanly')
                router.terminate()
                try:
                    router.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    router.kill(); router.wait()
            assert skill.returncode == 0, f'Exit {skill.returncode}: ' + (work / 'skill.log').read_text()
        print(json.dumps({'status': 'passed', 'windows': len(entries), 'channels': len(config['stream_channels']),
            'native_factories_jobs_and_callbacks': True, 'uci_owp_publications': len(published),
            'signal_reports': len(signals), 'timestamp_low_bits_preserved': True,
            'idle_subscription_survives_transaction_timeout': args.idle_seconds >= 6,
            'simulator_and_harness_packages_absent': True,
            'qualification_scope': 'pinned-interface fixtures; not deployed-provider or full-schema certification'}, indent=2))


if __name__ == '__main__':
    main()
