"""Create an explicitly synthetic export for native adapter qualification."""
import argparse
import json
from pathlib import Path
import tempfile

from rf_skill_harness.project import init_project
from rf_skill_harness.exporter import export_project

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary) / 'skill'
    init_project(root, 'native-contract-fixture')
    channels = [f'antenna{i}' for i in range(8)]
    config = json.loads((root / 'config/runtime.json').read_text())
    config.update(window_samples=4096, receive_event_samples=4096,
        stream_channels={str(i + 1): n for i, n in enumerate(channels)},
        volts_per_count={n: .001 for n in channels})
    (root / 'config/runtime.json').write_text(json.dumps(config))
    receiver = json.loads((root / 'config/receiver.json').read_text())
    receiver['sites'][0]['channels'] = channels
    (root / 'config/receiver.json').write_text(json.dumps(receiver))
    (root / 'processor.py').write_text('''import numpy as np
def process_window(iq, metadata, context):
    assert iq.shape == (4096,8) and not iq.flags.writeable
    for channel in range(8):
        np.testing.assert_allclose(iq[:,channel], (channel+1)*(.01-.01j), rtol=1e-6)
    assert metadata['timestamp_ns'] % 10 == 7
    assert len(metadata['native_mel_by_channel']) == 8
    return {'detected': True, 'frequency_hz': context['carrier_hz']}
''')
    (root / 'report.py').write_text('''def signal_reports(m, metadata, context):
    return [{'Parametrics': {'Frequency': {'FrequencyAverage': m['frequency_hz']}}}]
''')
    print(json.dumps(export_project(root, args.output), indent=2))
