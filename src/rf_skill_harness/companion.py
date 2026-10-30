"""GNU Radio Companion OOT extension, preview and native skill compilation."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .paths import new_directory


def blocks_path():
    return Path(__file__).with_name('companion_blocks')


def grc_environment():
    env = dict(os.environ)
    env['GRC_BLOCKS_PATH'] = str(blocks_path()) + os.pathsep + env.get('GRC_BLOCKS_PATH', '')
    return env


def read_graph(path):
    import yaml
    return yaml.safe_load(Path(path).read_text())


def visualization_blocks(graph):
    return [b for b in graph['blocks'] if b['id'].startswith('qtgui_')
            and b.get('states', {}).get('state', 'enabled') == 'enabled']


def deployment_graph(graph, keep_visualizations=()):
    """Drop plot taps and their now-unused branches, without rewiring the DSP."""
    value = deepcopy(graph)
    keep_visualizations = set(keep_visualizations)
    plots = {b['name'] for b in visualization_blocks(value)}
    if not keep_visualizations <= plots:
        raise ValueError('Unknown visualization selection')
    blocks = {b['name']: b for b in value['blocks']
              if b.get('states', {}).get('state', 'enabled') == 'enabled'}
    mandatory = {name for name, b in blocks.items() if b['id'] in
                 ('rfskill_measurements', 'rfskill_tx')}
    if sum(b['id'] == 'rfskill_rx' for b in blocks.values()) != 1 or sum(b['id'] == 'rfskill_measurements' for b in blocks.values()) != 1:
        raise ValueError('Use exactly one RF Receive Endpoint and one RF Window Measurements endpoint')
    if sum(b['id'] == 'rfskill_tx' for b in blocks.values()) > 1:
        raise ValueError('This TX profile supports one waveform endpoint')
    retained = mandatory | keep_visualizations
    connections = [c for c in value['connections'] if c[0] in blocks and c[2] in blocks]
    tx_nodes = {n for n, b in blocks.items() if b['id'] == 'rfskill_tx'}
    if tx_nodes:
        upstream = set(tx_nodes)
        while True:
            previous = set(upstream)
            upstream.update(c[0] for c in connections if c[2] in upstream)
            if upstream == previous:
                break
        if any(blocks[n]['id'] == 'rfskill_rx' for n in upstream):
            raise ValueError('The fixed-waveform TX profile cannot derive transmit samples from received I/Q')
        if not any(blocks[n]['id'] == 'blocks_head' and blocks[n]['parameters'].get('num_items') == 'tx_samples' for n in upstream):
            raise ValueError('Bound the TX source with Head(tx_samples) before its endpoint and plot taps')
    # Keep every ancestor of an endpoint/selected plot. An unused FFT branch is
    # removed with its plot; the measurement/TX chain cannot be deselected.
    while True:
        previous = set(retained)
        retained.update(c[0] for c in connections if c[2] in retained)
        if retained == previous:
            break
    if not any(blocks[n]['id'] == 'rfskill_rx' for n in retained):
        raise ValueError('The measurement chain must use the RF Receive Endpoint')
    declarations = {'parameter', 'variable', 'import', 'note', 'epy_module'}
    for name, block in blocks.items():
        if block['id'].startswith('variable_qtgui_'):
            block['id'] = 'variable'
            block['parameters'] = {'value': block['parameters']['value']}
        if block['id'] in declarations or block['id'].startswith('variable_'):
            retained.add(name)
    value['blocks'] = [b for n, b in blocks.items() if n in retained]
    value['connections'] = [c for c in connections if c[0] in retained and c[2] in retained]
    for b in value['blocks']:
        identifier = b['id'].lower()
        if any(word in identifier for word in ('file_source', 'file_sink', 'wavfile', 'usrp', 'osmosdr', 'soapy', 'zeromq', 'udp_', 'tcp_', 'socket')):
            raise ValueError('Replace test/hardware I/O with RF endpoints before export: ' + b['name'])
    options = value['options']['parameters']
    options.update(id='skill_flowgraph', output_language='python',
        generate_options='qt_gui' if keep_visualizations else 'no_gui', run_options='run',
        run_command='{python} -u {filename}')
    return value


def compile_graph(graph, output, *, design_root=None):
    """Use GNU Radio's own validator/generator, including Embedded Python Blocks."""
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'skill.grc'
    path.write_text(json.dumps(graph, indent=2) + '\n')
    env = grc_environment()
    if design_root is not None:
        env['PYTHONPATH'] = str(Path(design_root).resolve()) + os.pathsep + env.get('PYTHONPATH', '')
    result = subprocess.run(['grcc', '-o', str(root), str(path)], env=env,
        cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise ValueError('Companion validation/code generation failed:\n' + result.stdout + result.stderr)
    return sorted(root.glob('*.py'))


def init_companion(output, name='my-rf-skill', *, transmit=False):
    from .project import init_project
    root = Path(init_project(output, name)['project'])
    package = Path(__file__).parent
    shutil.copyfile(package / 'companion_runtime.py', root / 'skill_io.py')
    shutil.copyfile(package / 'companion_dsp.py', root / 'grc_processor.py')
    (root / 'skill.grc').write_text(json.dumps(starter_graph(transmit), indent=2) + '\n')
    spec = json.loads((root / 'skill.json').read_text())
    spec.update(processor='grc_processor.py:process_window',
        files=['grc_processor.py', 'skill_io.py', 'processor.py', 'report.py'],
        companion={'graph': 'skill.grc', 'profile': 'finite-window-v1'})
    if transmit:
        spec['capabilities'].append('transmit')
        spec['transmitter'] = 'grc_processor.py:transmit_waveform'
        config = json.loads((root / 'config/runtime.json').read_text())
        config['transmit'] = {'samples': 256, 'guard_samples': 32,
            'tx_power_w': None, 'tx_power_mode_id': 0, 'attenuation_db': 0}
        (root / 'config/runtime.json').write_text(json.dumps(config, indent=2) + '\n')
    (root / 'skill.json').write_text(json.dumps(spec, indent=2) + '\n')
    (root / 'README.md').write_text('''# Companion RF skill project

skill.grc is the capture-independent design. Save it in Companion before Preview
or Export in the RF Skill panel. Use Embedded Python Blocks for custom streaming
DSP, processor.py for finite-window measurements, and report.py for UCI mapping.
Select a different capture in the panel to test this same design. The selected
capture lives only in companion-session.json; it is never exported.

RX blocks must preserve count, vector channel order, units and acquisition time.
This first profile resets native streaming-block state at each acquisition
window. Export excludes plot taps by default, or retains explicitly selected
Qt sinks. Retained plots need a display (or explicit offscreen Qt configuration).
Configure actual receiver calibration and native provider settings in config/.
For TX, configure real power and timing; the initial native profile repeats one
finite waveform and schedules non-overlapping TX/guard/RX/guard intervals.
''')
    return {'project': str(root), 'graph': str(root / 'skill.grc'), 'recording_required': False}


def starter_graph(transmit=False):
    def block(name, identifier, parameters, x, y):
        return {'name': name, 'id': identifier, 'parameters': parameters,
            'states': {'coordinate': [x, y], 'rotation': 0, 'state': 'enabled'}}
    blocks = [block(n, 'parameter', {'type': t, 'value': v}, 240 + i*210, 20)
              for i, (n, t, v) in enumerate([('channel_count', 'intx', '1'),
                  ('samp_rate', 'eng_float', '2e6'), ('carrier_hz', 'eng_float', '915e6'),
                  ('tx_samples', 'intx', '256')])]
    custom = '''import numpy as np
from gnuradio import gr
class blk(gr.sync_block):
    """Replace this gain with your streaming DSP. Channels are simultaneous volts."""
    def __init__(self, channels=1, gain=1.0):
        gr.sync_block.__init__(self, name='Custom Python DSP',
            in_sig=[(np.complex64, channels)], out_sig=[(np.complex64, channels)])
        self.gain = gain
    def work(self, input_items, output_items):
        output_items[0][:] = input_items[0] * self.gain
        return len(output_items[0])
'''
    blocks.extend([
        block('receive', 'rfskill_rx', {'channels': 'channel_count'}, 20, 200),
        block('custom_dsp', 'epy_block', {'_source_code': custom, 'channels': 'channel_count', 'gain': '1.0'}, 300, 200),
        block('measurements', 'rfskill_measurements', {'channels': 'channel_count', 'processor': 'processor.py:process_window'}, 600, 200),
        block('split', 'blocks_vector_to_streams', {'type': 'complex', 'num_streams': 'channel_count', 'vlen': '1'}, 20, 380),
        block('antenna', 'blocks_selector', {'type': 'complex', 'num_inputs': 'channel_count', 'num_outputs': '1',
              'input_index': '0', 'output_index': '0', 'enabled': 'True', 'vlen': '1'}, 270, 380),
        block('rx_spectrum', 'qtgui_freq_sink_x', {'type': 'complex', 'name': 'Processed RX: antenna 0', 'fftsize': '1024',
              'fc': '0', 'bw': 'samp_rate', 'nconnections': '1', 'autoscale': 'True', 'grid': 'True',
              'gui_hint': '1,0,1,1'}, 850, 380),
        block('rx_time', 'qtgui_time_sink_x', {'type': 'complex', 'name': 'Processed RX I/Q volts', 'size': '1024',
              'srate': 'samp_rate', 'nconnections': '1', 'autoscale': 'True', 'grid': 'True', 'yunit': 'V',
              'entags': 'False', 'label1': 'I', 'label2': 'Q', 'gui_hint': '0,0,1,1'}, 850, 540)])
    connections = [['receive', '0', 'custom_dsp', '0'], ['custom_dsp', '0', 'measurements', '0'],
        ['custom_dsp', '0', 'split', '0'], ['antenna', '0', 'rx_spectrum', '0'], ['antenna', '0', 'rx_time', '0']]
    # Vector fanout is a compile-time GRC construct. Its connections are expanded
    # for the design's declared channel count, independently of any capture.
    connections.append(['split', '0', 'antenna', '0'])
    if transmit:
        blocks.extend([
            block('waveform', 'analog_sig_source_x', {'type': 'complex', 'samp_rate': 'samp_rate', 'waveform': 'analog.GR_COS_WAVE',
                'freq': '100e3', 'amp': '.25', 'offset': '0', 'phase': '0'}, 20, 580),
            block('finite_tx', 'blocks_head', {'type': 'complex', 'num_items': 'tx_samples', 'vlen': '1'}, 300, 580),
            block('transmit', 'rfskill_tx', {}, 600, 580),
            block('tx_time', 'qtgui_time_sink_x', {'type': 'complex', 'name': 'TX normalized waveform', 'size': '256',
                'srate': 'samp_rate', 'nconnections': '1', 'autoscale': 'True', 'grid': 'True',
                'entags': 'False', 'label1': 'I', 'label2': 'Q', 'gui_hint': '2,0,1,1'}, 850, 740)])
        connections.extend([['waveform', '0', 'finite_tx', '0'], ['finite_tx', '0', 'transmit', '0'], ['finite_tx', '0', 'tx_time', '0']])
    return {'options': {'parameters': {'id': 'skill_flowgraph', 'title': 'RF Skill — edit DSP, tap plots, export',
        'output_language': 'python', 'generate_options': 'qt_gui', 'run_options': 'run',
        'run_command': '{python} -u -m rf_skill_harness.companion preview {filename}'},
        'states': {'coordinate': [20, 20], 'rotation': 0, 'state': 'enabled'}},
        'blocks': blocks, 'connections': connections, 'metadata': {'file_format': 1}}


def build_companion(workspace, output, *, keep_visualizations=(), channel_count=None):
    project = Path(workspace).resolve()
    spec = json.loads((project / 'skill.json').read_text())
    source_path = project / spec['companion']['graph']
    if source_path.is_symlink() or not source_path.resolve().is_relative_to(project):
        raise ValueError('The graph must be an ordinary file inside the skill project')
    source = read_graph(source_path)
    graph = deployment_graph(source, keep_visualizations)
    parameter = next(b for b in graph['blocks'] if b['name'] == 'channel_count' and b['id'] == 'parameter')
    declared = int(parameter['parameters']['value'])
    if not 1 <= declared <= 256:
        raise ValueError('Declare channel_count as a constant integer in 1..256')
    if channel_count is not None and channel_count != declared:
        raise ValueError(f'This graph expects {declared} coherent channels; the collect has {channel_count}. Use compatible collects or explicitly redesign the antenna layout.')
    channel_count = declared
    if any(b['name'] == 'split' for b in graph['blocks']):
        graph['connections'] = [c for c in graph['connections'] if not (c[0] == 'split' and c[2] == 'antenna')]
        graph['connections'].extend([['split', str(i), 'antenna', str(i)] for i in range(channel_count)])
    generated = compile_graph(graph, output, design_root=project)
    return graph, generated


def export_companion(workspace, output, *, keep_visualizations=()):
    from .exporter import export_project
    project = Path(workspace).resolve()
    # Compile into a clean staging project. Capture paths, notebook state and
    # preview selections never reach export_project's explicit file allowlist.
    with tempfile.TemporaryDirectory(prefix='rf-grc-export-') as directory:
        staged = Path(directory) / 'project'
        staged.mkdir()
        spec = json.loads((project / 'skill.json').read_text())
        for name in spec['files']:
            source = project / name
            if not source.resolve().is_relative_to(project) or source.is_symlink():
                raise ValueError('Declare only local design files')
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copytree(project / 'config', staged / 'config')
        graph, generated = build_companion(project, Path(directory) / 'generated', keep_visualizations=keep_visualizations)
        receiver = json.loads((staged / 'config/receiver.json').read_text())
        declared = int(next(b for b in graph['blocks'] if b['name'] == 'channel_count')['parameters']['value'])
        if sum(len(s['channels']) for s in receiver['sites']) != declared:
            raise ValueError('Deployment receiver channel count must match the declared graph layout')
        has_tx = any(b['id'] == 'rfskill_tx' for b in graph['blocks'])
        if has_tx != ('transmit' in spec['capabilities']):
            raise ValueError('TX capability and RF Transmit Waveform endpoint must agree')
        for file in generated:
            shutil.copyfile(file, staged / file.name)
        spec['files'] = list(dict.fromkeys([*spec['files'], *[p.name for p in generated]]))
        spec['companion']['retained_visualizations'] = list(keep_visualizations)
        spec['companion']['graph'] = 'source/skill.grc'
        spec['system_dependencies'] = {'ubuntu-24.04': ['gnuradio']}
        # Ubuntu's GNU Radio 3.10 bindings are compiled against NumPy 1.x.
        spec['dependencies'] = list(dict.fromkeys([*spec['dependencies'], 'numpy>=1.24,<2']))
        (staged / 'skill.json').write_text(json.dumps(spec, indent=2) + '\n')
        result = export_project(staged, output)
        root = Path(result['export'])
        (root / 'source').mkdir()
        source_path = root / 'source/skill.grc'
        source_path.write_text(json.dumps(graph, indent=2) + '\n')
        manifest_path = root / 'export-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['companion'] = {'profile': 'finite-window-v1',
            'source_sha256': sha256(source_path.read_bytes()).hexdigest(),
            'retained_visualizations': list(keep_visualizations),
            'streaming_block_state': 'reset at each acquisition window',
            'system_dependencies': spec['system_dependencies']}
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        # GNU Radio bindings come from apt, never a similarly named PyPI package.
        dockerfile = root / 'Dockerfile'
        dockerfile.write_text(dockerfile.read_text().replace('ca-certificates git python3 python3-pip libpython',
            'ca-certificates gnuradio git python3 python3-pip libpython'))
        notice = root / 'NOTICE'
        notice.write_text(notice.read_text() + 'GNU Radio and generated GNU Radio code retain their upstream GPL obligations; review distribution of your combined skill.\n')
        return {**result, 'retained_visualizations': list(keep_visualizations), 'transmit': has_tx}


def evaluate_companion(workspace, recordings, output):
    """Compile once, then run that same design on each collect with fresh state."""
    from .evaluation import evaluate_project
    project = Path(workspace).resolve()
    with tempfile.TemporaryDirectory(prefix='rf-grc-evaluate-') as directory:
        staged = Path(directory)
        spec = json.loads((project / 'skill.json').read_text())
        for name in spec['files']:
            source = project / name
            if source.is_symlink() or not source.resolve().is_relative_to(project):
                raise ValueError('Declare only local design files')
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copytree(project / 'config', staged / 'config')
        _, generated = build_companion(project, staged / 'generated')
        for file in generated:
            shutil.copyfile(file, staged / file.name)
        spec['files'] = list(dict.fromkeys([*spec['files'], *[p.name for p in generated]]))
        (staged / 'skill.json').write_text(json.dumps(spec, indent=2) + '\n')
        for file in generated:
            sys.modules.pop(file.stem, None)
        sys.modules.pop('skill_io', None)
        return evaluate_project(staged, recordings, output)


def preview(workspace, recording, *, index=0, plots=True):
    from .archive import Archive
    root = Path(workspace).resolve()
    archive = Archive(recording)
    window = next(archive.windows(start_window=index, stop_window=index + 1))
    spec = json.loads((root / 'skill.json').read_text())
    source = read_graph(root / spec['companion']['graph'])
    kept = [b['name'] for b in visualization_blocks(source)] if plots else []
    with tempfile.TemporaryDirectory(prefix='rf-grc-preview-') as directory:
        _, files = build_companion(root, directory, keep_visualizations=kept, channel_count=len(archive.channels))
        # Generated Python and skill_io must share one import identity for binding.
        sys.path[:0] = [directory, str(root)]
        for name in ('skill_io', *[f.stem for f in files]):
            sys.modules.pop(name, None)
        import skill_io
        result, tx = skill_io.run_graph(window.samples, window.metadata(), archive.context(), hold_plots=plots)
        return {'metadata': window.metadata(), 'measurements': result, 'tx_samples': len(tx)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    init = subs.add_parser('init')
    init.add_argument('--workspace', type=Path, required=True)
    init.add_argument('--name', default='my-rf-skill')
    init.add_argument('--transmit', action='store_true')
    export = subs.add_parser('export')
    export.add_argument('workspace', type=Path)
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--keep-plot', action='append', default=[])
    evaluate = subs.add_parser('evaluate')
    evaluate.add_argument('workspace', type=Path)
    evaluate.add_argument('--recordings', type=Path, action='append', required=True)
    evaluate.add_argument('--output', type=Path, required=True)
    run = subs.add_parser('preview')
    run.add_argument('graph', type=Path, help='GRC generated Python or project directory')
    run.add_argument('--recording', type=Path)
    run.add_argument('--window', type=int)
    run.add_argument('--no-plots', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'init':
        result = init_companion(args.workspace, args.name, transmit=args.transmit)
    elif args.command == 'export':
        result = export_companion(args.workspace, args.output, keep_visualizations=args.keep_plot)
    elif args.command == 'evaluate':
        result = evaluate_companion(args.workspace, args.recordings, args.output)
    else:
        root = args.graph.resolve() if args.graph.is_dir() else args.graph.resolve().parent
        settings = root / 'companion-session.json'
        selected = json.loads(settings.read_text()) if settings.is_file() else {}
        recording = args.recording or selected.get('recording')
        if not recording:
            parser.error('Select a test capture in the RF Skill panel before running the graph')
        result = preview(root, recording, index=args.window if args.window is not None else selected.get('window', 0), plots=not args.no_plots)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
