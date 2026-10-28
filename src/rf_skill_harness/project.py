"""Skill-first projects; captures are replaceable test inputs, not project identity."""
import json
from pathlib import Path
import shutil

from .paths import new_directory


def init_project(output, name='my-rf-skill'):
    root = new_directory(output)
    package = Path(__file__).parent
    for filename in ('processor.py', 'report.py', 'plot_output.py', 'plot_results.py'):
        shutil.copyfile(package / filename, root / filename)
    (root / 'skill.json').write_text(json.dumps({'schema_version': 1, 'name': name,
        'version': '0.1.0', 'interface': 'ams-gra-rf-mel-uci', 'capabilities': ['receive'],
        'processor': 'processor.py:process_window', 'reporter': 'report.py:signal_reports',
        'files': ['processor.py', 'report.py'], 'dependencies': ['numpy>=1.24']}, indent=2) + '\n')
    (root / 'project.json').write_text(json.dumps({'schema_version': 2,
        'description': 'Independent skill project; recordings are external test inputs.'}, indent=2) + '\n')
    (root / 'recordings.json').write_text('{}\n')
    (root / 'config').mkdir()
    (root / 'config/receiver.json').write_text(json.dumps({'schema_version': 1,
        'sample_rate_hz': 2_000_000, 'carrier_hz': 915_000_000,
        'sites': [{'id': 'receiver', 'channels': ['rx0']}],
        'voltage_reference': 'calibrated complex volts'}, indent=2) + '\n')
    (root / 'config/runtime.json').write_text(json.dumps({
        'skill_root': '/opt/skill', 'receiver_config': '/opt/skill/config/receiver.json',
        'rf_mel_registry_path': '/opt/skill/config/rf_mel_registry.json', 'rf_mel_id': 1,
        'virtual_aperture_definition_id': 0, 'capability_ids': [],
        'window_samples': 4096, 'receive_event_samples': 4096,
        'stream_channels': {'1': 'rx0'}, 'volts_per_count': {'rx0': None},
        'queue_capacity': 64, 'owp_uri': 'ws://127.0.0.1:21402',
        'owp_service_id': name, 'owp_schema_version': '002.5.0', 'owp_versions': ['1.0'],
        'position_subscription_id': 'position',
        'position_topic': 'mission.position-report', 'signal_topic': 'mission.signal-report',
        'service_status_topic': 'mission.service-status', 'service_status_rate_hz': 1,
        'report_rate_limit_hz': 10, 'system_name': 'university-lab', 'service_name': name,
        'mission_name': 'mission', 'namespace_uuid': '507e1ce1-1111-4444-8888-507e1ce11111',
        'results_dir': None}, indent=2) + '\n')
    (root / 'config/rf_mel_registry.json').write_text(json.dumps({'1': {
        'config': '/providers/provider.json', 'library': '/providers/librf_mel.so'}}, indent=2) + '\n')
    from .workbench import project_notebook
    (root / 'analysis.ipynb').write_text(json.dumps(project_notebook(), indent=2) + '\n')
    (root / 'README.md').write_text('''# RF skill development project

Open analysis.ipynb in the workbench. Choose test captures independently of this
project, edit processor.py and plot_output.py, then process a selected window.
recordings.json may map display labels to local capture paths for native Python.
The container launcher supplies its read-only capture catalog separately.

Before exporting, edit skill.json, report.py and config/. Declare helper files
and dependencies explicitly. config/ describes the deployed receiver, not a
simulation scene. Set the real volts-per-count calibration; it is not guessed.
The report mapping defaults to no detections. Export creates an independent
native RF MEL/UCI source package; it does not certify the deployed service.
''')
    return {'project': str(root), 'name': name, 'recording_required': False}
