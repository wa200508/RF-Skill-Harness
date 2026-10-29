"""Create a synthetic, capture-free Companion TX/RX export for native CI."""
import argparse
import json
from pathlib import Path
import tempfile

from rf_skill_harness.companion import init_companion, export_companion


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='companion-native-fixture-') as directory:
        root = Path(directory) / 'design'
        init_companion(root, 'companion-fixture', transmit=True)
        path = root / 'config/runtime.json'
        config = json.loads(path.read_text())
        config['volts_per_count'] = {'rx0': .001}  # Interface-fixture scale only.
        config['transmit']['tx_power_w'] = 1  # Fixture command, not hardware power validation.
        path.write_text(json.dumps(config, indent=2) + '\n')
        print(json.dumps(export_companion(root, args.output), indent=2))


if __name__ == '__main__':
    main()
