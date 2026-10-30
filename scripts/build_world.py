#!/usr/bin/env python3
"""Build the pinned upstream browser editor with our small RF extension.

DSP/Pyodide artifacts are downloaded and hash-checked at build time; never mixed
with a live upstream runtime. No Qt/Emscripten toolchain is needed for this step.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--cache', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    lock = json.loads((repo / 'world/upstream-lock.json').read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rf-world-build-') as temp:
        source = args.source or Path(temp) / 'upstream'
        if not args.source:
            subprocess.run(['git', 'clone', '--filter=blob:none', '--no-checkout', lock['upstream'], str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'checkout', lock['revision']], check=True)
        actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != lock['revision']:
            raise ValueError('Upstream source revision differs from lock')
        def fetch(item):
            name, expected = item
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            cached = args.cache / name if args.cache else target
            if cached.is_file() and sha256(cached.read_bytes()).hexdigest() == expected['sha256']:
                if cached != target:
                    shutil.copyfile(cached, target)
            else:
                subprocess.run(['curl', '-fsSL', '--retry', '3', '--max-time', '180', lock['origin'] + name, '-o', str(target)], check=True)
            if sha256(target.read_bytes()).hexdigest() != expected['sha256']:
                raise ValueError('Upstream artifact changed; refusing mixed runtime: ' + name)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(fetch, lock['files'].items()))
        shutil.copyfile(output / 'blocks.json', source / 'editor/public/blocks.json')
        for name in ['rf-extension.ts', 'rf-extension.css', 'rf-sigmf.ts']:
            shutil.copyfile(repo / 'world' / name, source / 'editor/src' / name)
        main = source / 'editor/src/main.ts'
        text = main.read_text()
        marker = '// RF Skill Harness extension hooks (GPL-3.0-or-later).'
        if marker not in text:
            text = "import { installRfExtension, rfRunContext } from './rf-extension';\n" + text
            old = '  return dumpGrc(doc);\n}\nfunction downloadBlob'
            if text.count(old) != 1: raise ValueError('Upstream run hook changed')
            text = text.replace(old, '  rfRunContext(doc);\n' + old)
            text += '\n' + marker + '''
editorReady.then(() => installRfExtension({
  outputs: () => state.insts.filter(i => i.enabled && !i.bypassed).flatMap(i =>
    Array.from({length: portCount(i, 'out')}, (_, n) => ({i, n, p: portMeta(i, 'out', n)}))
      .filter(x => x.p.domain === 'stream' && ['complex', 'float'].includes(portType(x.i, 'out', x.n)))
      .map(x => {const vlen = evalExpr(String(x.p.vlen), buildRunScope());
        return {block: x.i.name, port: x.p.streamIndex, channels: vlen.ok ? Number(vlen.value) : 1,
                datatype: portType(x.i, 'out', x.n) === 'complex' ? 'cf32_le' : 'rf32_le'};})),
  viewer: (button, panel) => {
    const entry = {id: 'sigmf', button, panel}; workspaceTabs.push(entry); wireWorkspaceTab(entry);
    const previous = workspaceTabController.activate.bind(workspaceTabController);
    workspaceTabController.activate = tab => {panel.hidden = tab !== 'sigmf'; previous(tab);};
    return () => activateWorkspaceTab('sigmf');
  },
  graph: () => buildGrcDoc(),
  load: doc => { loadFlowgraph(doc, false); resetHistory(); },
  run: () => run(),
  stop: () => stop(),
})).catch(error => log(String(error)));
'''
            text = text.replace('const runScope = resolve ? buildRunScope() : {};', 'const runScope = resolve ? {...buildRunScope(), ...(window as any).__rfScope} : {};')
            main.write_text(text)
            run = source / 'editor/src/run-session.ts'
            text = run.read_text()
            anchor = "    // A hosted recording: the runner's factory derives"
            if text.count(anchor) != 1: raise ValueError('Upstream input hook changed')
            text = text.replace(anchor, '''    // RF capture binding is session-only, outside the editable skill graph.
    const rf = (window as any).__rfInput;
    if (block.id === 'blocks_file_source' && block.name === rf?.name &&
        block.params.file === 'RF_RECEIVE') {
      fileOverrides.set(block.name, rf.path);
      recordingFiles.push(rf.descriptor);
      continue;
    }

''' + anchor)
            text = text.replace("return deps.state.insts.some(i => i.id === SIGMF_SINK_ID", "return !!(window as any).__rfInspectTap || deps.state.insts.some(i => i.id === SIGMF_SINK_ID")
            text = text.replace("  const addedPaths = new Set<string>();", "  if ((window as any).__rfInspectTap) recordingFiles.push({kind: 'output', path: '/local-output/rf-inspection/signal', base: 'signal', dir: null});\n  const addedPaths = new Set<string>();")
            run.write_text(text)
        subprocess.run(['npm', 'ci', '--no-audit', '--no-fund'], cwd=source / 'editor', check=True)
        # Skip upstream's optional example/wiki generation; retain our own project.
        subprocess.run(['npx', 'vite', 'build'], cwd=source / 'editor', check=True)
        shutil.copytree(source / 'editor/dist', output, dirs_exist_ok=True)
        shutil.copytree(source / 'example_flowgraphs', output / 'example_flowgraphs', dirs_exist_ok=True)
        (output / 'example_flowgraphs/index.json').write_text(json.dumps([str(p.relative_to(source / 'example_flowgraphs')) for p in (source / 'example_flowgraphs').rglob('*.grc')]))
        (output / 'wiki').mkdir(exist_ok=True)
        (output / 'wiki/index.json').write_text('{}')
        # No upstream analytics in this locally served private workbench.
        for path in [output / 'index.html', output / 'runner/build/runner.html']:
            text = path.read_text()
            text = re.sub(r'<script\b[^>]*>.*?</script>', lambda m: '' if any(word in m[0] for word in
                ('googletagmanager', 'gtag(', 'cloudflareinsights')) else m[0], text, flags=re.S)
            path.write_text(text)
        runner_html = output / 'runner/build/runner.html'
        text = runner_html.read_text()
        anchor = "if (message.type === 'done' && message.mode === 'download') {"
        if text.count(anchor) != 1: raise ValueError('Upstream SigMF output hook changed')
        text = text.replace(anchor, "if (message.type === 'done' && message.mode === 'download' && !window.parent.__rfAcceptSigmf?.(window, RUN_RECORDING_TOKEN, path, message)) {")
        runner_html.write_text(text)
        shutil.copyfile(source / 'LICENSE' , output / 'GNU-Radio-World-LICENSE.txt')
        shutil.copyfile(repo / 'world/upstream-lock.json', output / 'upstream-lock.json')
        # The complete editor source and our patch are supplied beside binaries.
        shutil.copytree(source / 'editor/src', output / 'corresponding-source/editor/src', dirs_exist_ok=True)
        shutil.copyfile(repo / 'scripts/build_world.py', output / 'corresponding-source/build_world.py')
        print('GNU Radio World + RF extension ready:', output)


if __name__ == '__main__':
    main()
