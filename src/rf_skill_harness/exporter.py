"""Create independent native AMS-GRA RF MEL/UCI skill source distributions."""
from fractions import Fraction
from hashlib import sha256
import ast
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import ipaddress
from urllib.parse import urlsplit
from uuid import UUID

from .paths import new_directory


def _read(path):
    return json.loads(path.read_text())


def export_project(workspace, output):
    project = Path(workspace).expanduser().resolve()
    spec = _read(project / 'skill.json')
    if spec.get('schema_version') != 1 or spec.get('interface') != 'ams-gra-rf-mel-uci':
        raise ValueError('Expected a schema-v1 native AMS-GRA skill.json')
    if not re.fullmatch('[a-z][a-z0-9-]{0,62}', spec.get('name', '')):
        raise ValueError('Skill name must be a lowercase deployment identifier')
    if spec.get('capabilities') != ['receive']:
        raise ValueError('This native export profile implements receive jobs; TX jobs require a separate implemented profile')
    files = spec.get('files', [])
    if not files or len(files) != len(set(files)):
        raise ValueError('Declare a unique explicit files list for the skill design')
    payloads = {}
    for name in files:
        relative = PurePosixPath(name)
        source = project / name
        if relative.is_absolute() or '..' in relative.parts or source.is_symlink() or not source.resolve().is_relative_to(project):
            raise ValueError('Design files must be ordinary files inside the project')
        if relative.suffix not in ('.py', '.json', '.toml', '.txt', '.csv', '.npy', '.npz'):
            raise ValueError('Only declared code/configuration/model assets can be exported; never captures or notebooks')
        if not source.is_file():
            raise ValueError('Missing declared design file: ' + name)
        content = source.read_bytes()
        if relative.suffix == '.py':
            tree = ast.parse(content, filename=name)
            for node in ast.walk(tree):
                imports = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                if any(m.split('.')[0] in {'rf_skill_harness', 'airsim_rf', 'projectairsim', 'sionna', 'mitsuba'} for m in imports):
                    raise ValueError('Exported DSP must be independent of the harness/simulator: ' + name)
        payloads[name] = content
    for field in ('processor', 'reporter'):
        filename, separator, function = spec.get(field, '').partition(':')
        if not separator or not function.isidentifier() or filename not in payloads:
            raise ValueError(f'{field} must identify a declared Python file:function')
    dependencies = spec.get('dependencies')
    if not isinstance(dependencies, list) or not all(isinstance(v, str) and v and '\n' not in v and not v.startswith('-') for v in dependencies):
        raise ValueError('Declare Python package requirements in dependencies')
    config = _read(project / 'config/runtime.json')
    receiver = _read(project / 'config/receiver.json')
    rate = receiver.get('sample_rate_hz')
    carrier = receiver.get('carrier_hz')
    if type(rate) is not int or not 0 < rate < 2**32 or type(carrier) not in (int, float) or not math.isfinite(carrier) or carrier <= 0:
        raise ValueError('Native receiver needs a positive uint32 sample rate and finite positive carrier')
    for field in ('owp_service_id', 'position_topic', 'signal_topic', 'service_status_topic'):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', config.get(field, '')):
            raise ValueError('Invalid OWP identifier: ' + field)
    uri = urlsplit(config.get('owp_uri', ''))
    if uri.scheme != 'ws' or not uri.hostname or not uri.port or uri.username or uri.password:
        raise ValueError('Configure the native OWP ws://IPv4:port endpoint')
    ipaddress.IPv4Address(uri.hostname)  # Reference OWP client uses IPv4 literals.
    UUID(config['namespace_uuid'])
    if type(config.get('queue_capacity')) is not int or not 1 <= config['queue_capacity'] <= 4096:
        raise ValueError('queue_capacity must be in 1..4096')
    for field in ('report_rate_limit_hz', 'service_status_rate_hz'):
        value = config.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('A positive finite rate is required: ' + field)
    for field in ('system_name', 'service_name', 'mission_name', 'owp_schema_version', 'rf_mel_registry_path'):
        if not isinstance(config.get(field), str) or not config[field]:
            raise ValueError('Missing native configuration: ' + field)
    channels = [n for site in receiver['sites'] for n in site['channels']]
    if len(set(channels)) != len(channels) or not channels:
        raise ValueError('Unique receiver antenna channels required')
    if set(config.get('stream_channels', {}).values()) != set(channels) or len(config['stream_channels']) != len(channels):
        raise ValueError('Configure the native stream_channels mapping for every antenna')
    if any(not str(k).isdigit() or not 0 <= int(k) < 2**32 or str(int(k)) != k for k in config['stream_channels']):
        raise ValueError('Native stream IDs must be canonical uint32 strings')
    scales = config.get('volts_per_count', {})
    if set(scales) != set(channels) or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in scales.values()):
        raise ValueError('Set the calibrated volts_per_count for every channel before export')
    count = config.get('window_samples')
    event_samples = config.get('receive_event_samples', count)
    if type(count) is not int or not 1 <= count <= 2_000_000 or type(event_samples) is not int or not 1 <= event_samples <= 65536:
        raise ValueError('Declare window_samples (1..2000000) and receive_event_samples (1..65536)')
    duration = Fraction(event_samples * 10**15, 1) / Fraction(str(receiver['sample_rate_hz']))
    if duration.denominator != 1:
        raise ValueError('Receive event duration must align to RF MEL femtoseconds')
    registry = project / 'config/rf_mel_registry.json'
    entry = _read(registry).get(str(config.get('rf_mel_id')))
    if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) or not entry[k] for k in ('config', 'library')):
        raise ValueError('The RF MEL registry needs a selected library/config entry')
    config.update(skill_root='/opt/skill', receiver_config='/opt/skill/config/receiver.json',
        iq_sample_rate_hz=receiver['sample_rate_hz'], center_frequency_hz=receiver['carrier_hz'],
        receive_duration_fs=int(duration))
    root = new_directory(output)
    (root / 'design').mkdir()
    for name, content in payloads.items():
        target = root / 'design' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    native = Path(__file__).with_name('native')
    (root / 'native').mkdir()
    for name in ('host.cpp', 'CMakeLists.txt', 'fetch_dependencies.py', 'dependencies.json', 'qualify_native.py'):
        shutil.copyfile(native / name, root / 'native' / name)
    shutil.copyfile(native / 'Dockerfile', root / 'Dockerfile')
    (root / 'runtime').mkdir()
    shutil.copyfile(native / 'engine.py', root / 'runtime/engine.py')
    (root / 'tests').mkdir()
    shutil.copyfile(native / 'test_contract.py', root / 'tests/test_contract.py')
    (root / 'config').mkdir()
    (root / 'config/receiver.json').write_text(json.dumps(receiver, indent=2) + '\n')
    (root / 'config/runtime.json').write_text(json.dumps(config, indent=2) + '\n')
    shutil.copyfile(registry, root / 'config/rf_mel_registry.json')
    (root / 'skill.json').write_text(json.dumps(spec, indent=2) + '\n')
    (root / 'requirements.txt').write_text('\n'.join(dict.fromkeys(['numpy>=1.24', *dependencies])) + '\n')
    shutil.copyfile(native / 'LICENSE.txt', root / 'LICENSE')
    (root / 'NOTICE').write_text('Native RF skill exported by RF Skill Harness (Apache-2.0).\n'
        'Native acquisition/control and UCI/OWP support reuse pinned AMS-GRA RF FM Demod source.\n'
        'Dependency licenses and Government licensing intent are retained under third_party/.\n'
        'The developer is responsible for licensing their declared design and dependencies.\n')
    (root / '.dockerignore').write_text('.git\n**/__pycache__\nbuild\nresults\n*.sigmf-data\n')
    manifest = {'schema_version': 1, 'interface': spec['interface'], 'capabilities': spec['capabilities'],
        'files_sha256': {n: sha256(v).hexdigest() for n, v in payloads.items()},
        'native_dependency_revisions': _read(native / 'dependencies.json'),
        'native_source_patches': ['cancellable-idle-owp-subscription'],
        'adapter_files_sha256': {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest()
            for folder in ('native', 'runtime') for p in (root / folder).iterdir() if p.is_file()},
        'validation': {'native_provider_integration': 'not_run', 'uci_schema_validation': 'not_run', 'certified': False}}
    (root / 'export-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (root / 'README.md').write_text(f'''# {spec['name']}

Native AMS-GRA RF MEL receive skill with a Python DSP core and UCI/OWP reporting.
The design is copied byte-for-byte, with hashes in export-manifest.json.
This source package contains no notebooks, test I/Q, simulation or harness runtime.

```bash
docker build -t {spec['name']}:local .
docker build --target test -t {spec['name']}:test .
docker run --rm --init --network YOUR_STARTER_KIT_NETWORK \\
  --mount type=bind,src=/absolute/path/to/provider,dst=/providers,readonly \\
  {spec['name']}:local
```

Edit config/runtime.json and config/rf_mel_registry.json for your RF MEL library,
provider configuration, OWP endpoint/service authorization, capability IDs,
stream IDs and calibrated voltage scale. Provider dependencies and C++ ABI must
match this image; mount or build the provider's dependencies too when required.
The image intentionally does not download or guess an acquisition provider.
Override runtime configuration by mounting it over /opt/skill/config/runtime.json.
An empty report mapping emits service status without fabricating detections.

Build-time source revisions are pinned in native/dependencies.json. The builder
retains upstream licenses and notices; runtime images retain them in third_party/.
Run provider/OWP integration and UCI schema validation on the deployed starter kit
before claiming compliance; export-manifest.json explicitly records these as
not run. Contract-shaped scaffolding is not a conformance certificate.
The test target checks design hashes, coherent read-only windows, voltage units
and finite outputs on zero input. It does not substitute for provider validation.

This initial native profile schedules receive events only. It does not synthesize
radar TX jobs, convert simulator truth into measurements, or infer UCI semantics
from arbitrary detector outputs. Active radar requires a transmit-capable profile.
''')
    return {'export': str(root), 'interface': spec['interface'], 'name': spec['name'], 'certified': False}
