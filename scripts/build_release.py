#!/usr/bin/env python3
"""Build a complete pip/uv release containing the browser runtime, without Docker."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assets', type=Path, required=True, help='Output of scripts/build_world.py')
    parser.add_argument('--output', type=Path, default=Path('dist'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    assets = args.assets.resolve()
    for name in ['index.html', 'blocks.json', 'runner/build/runner.wasm', 'upstream-lock.json']:
        if not (assets / name).is_file():
            parser.error('Incomplete browser runtime: ' + name)
    expected = json.loads((root / 'world/upstream-lock.json').read_text())
    if json.loads((assets / 'upstream-lock.json').read_text()) != expected:
        parser.error('Browser runtime does not match this source revision')
    # Check all unmodified binary/runtime files. The builder strips analytics
    # from runner.html; the editor is intentionally rebuilt with the extension.
    for name, info in expected['files'].items():
        if name == 'runner/build/runner.html':
            continue
        if sha256((assets / name).read_bytes()).hexdigest() != info['sha256']:
            parser.error('Runtime integrity mismatch: ' + name)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rf-harness-release-') as temp:
        staged = Path(temp) / 'source'
        staged.mkdir()
        for name in ['pyproject.toml', 'MANIFEST.in', 'LICENSE', 'README.md']:
            shutil.copyfile(root / name, staged / name)
        for name in ['src', 'world', 'scripts', 'docs', 'tests']:
            shutil.copytree(root / name, staged / name,
                ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'world_assets', 'images'))
        shutil.copytree(assets, staged / 'src/rf_skill_harness/world_assets')
        for path in (staged / 'src/rf_skill_harness/world_assets').rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        # Build once from a clean staging tree. No generated assets enter Git.
        subprocess.run([sys.executable, '-m', 'build', '--outdir', str(output), str(staged)], check=True)
    distributions = sorted([*output.glob('*.whl'), *output.glob('*.tar.gz')])
    checksum = ''.join(sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in distributions)
    (output / 'SHA256SUMS').write_text(checksum)
    print(checksum, end='')


if __name__ == '__main__':
    main()
