"""Find receiver archives without importing NumPy or exposing mission truth."""
import json
from pathlib import Path


def discover_recordings(roots):
    found = {}
    for root in roots:
        base = Path(root).expanduser().resolve()
        if not base.is_dir():
            raise ValueError('Capture location does not exist: ' + str(base))
        candidates = [base, base / 'iq']
        candidates += [p.parent for pattern in ('*/manifest.json', '*/iq/manifest.json') for p in sorted(base.glob(pattern))]
        for path in candidates:
            manifest = path / 'manifest.json'
            if not manifest.is_file():
                continue
            try:
                value = json.loads(manifest.read_text())
                if not isinstance(value, dict) or value.get('format') != 'airsim-rf-sample-recording-v1':
                    continue
            except (ValueError, OSError):
                continue
            path = path.resolve()
            if path in found.values():
                continue
            label = path.parent.name if path.name == 'iq' else path.name
            unique = label
            index = 2
            while unique in found:
                unique = f'{label} ({index})'
                index += 1
            found[unique] = path
    return found
