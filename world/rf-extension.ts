// SPDX-License-Identifier: GPL-3.0-or-later
// Small editor extension; upstream GNU Radio World owns the canvas, code editor,
// scheduler and plots. No remote desktop or display server is involved.
import './rf-extension.css';

type Hooks = { graph(): any; load(graph: any): void; run(): unknown; stop(): void };
let selected: any = null;
let selectionGeneration = 0;

export function rfRunContext(doc: any): void {
  if (!selected) return;
  for (const block of doc.blocks) {
    if (block.id === 'epy_block') {
      block.parameters._source_code = 'import json as _rf_json\n' +
        `RF_WINDOW = _rf_json.loads(${JSON.stringify(JSON.stringify(selected.metadata))})\n` +
        `RF_CONTEXT = _rf_json.loads(${JSON.stringify(JSON.stringify(selected.context))})\n` +
        "RF_WINDOW['timestamp_ns'] = int(RF_WINDOW['timestamp_ns'])\n" +
        "RF_CONTEXT['first_timestamp_ns'] = int(RF_CONTEXT['first_timestamp_ns'])\n" + block.parameters._source_code;
    }
    if (block.id === 'variable' && block.name === 'samp_rate') block.parameters.value = String(selected.context.sample_rate_hz);
    if (block.id === 'variable' && block.name === 'carrier_hz') block.parameters.value = String(selected.context.carrier_hz);
  }
}

export async function installRfExtension(hooks: Hooks): Promise<void> {
  const session = await api('/rf/session');
  const bar = document.createElement('section');
  bar.id = 'rf-tools';
  bar.innerHTML = `<strong>RF Skill</strong><select id="rf-capture" aria-label="Test capture"></select>
    <input id="rf-window" aria-label="Acquisition window" type="number" min="0" value="0" title="Acquisition window index">
    <button id="rf-run">Run window</button><button id="rf-save">Save skill</button>
    <button id="rf-native">Native test</button><button id="rf-files">Project files</button>
    <button id="rf-export">Export AMS-GRA…</button><span id="rf-status" role="status"></span>`;
  document.querySelector('#headerBars')!.appendChild(bar);
  const get = (id: string) => bar.querySelector('#' + id) as HTMLInputElement;
  const capture = get('rf-capture') as unknown as HTMLSelectElement;
  for (const row of session.captures) capture.add(new Option(row.label, row.id));
  const status = (message: string) => { get('rf-status').textContent = message; };
  const guarded = (action: () => Promise<void>) => async () => {
    try { status('Working…'); await action(); }
    catch (error) { status(String(error)); }
  };
  const save = async () => { await api('/rf/save', {graph: hooks.graph()}); };
  const select = async () => {
    const generation = ++selectionGeneration;
    hooks.stop();
    selected = null;
    delete (window as any).__rfScope;
    delete (window as any).__rfInput;
    if (!capture.value) { status('Add captures with --recordings PATH. Editing/export works without a capture.'); return; }
    const index = Number(get('rf-window').value);
    const value = await api('/rf/select', {capture: capture.value, window: index, graph: hooks.graph()});
    if (generation !== selectionGeneration) return;
    selected = value;
    (window as any).__rfScope = {samp_rate: value.context.sample_rate_hz, carrier_hz: value.context.carrier_hz};
    get('rf-window').max = String(value.windows - 1);
    (window as any).__rfInput = {name: session.roles.rx, path: '/local-files/rf/input.cf32',
      descriptor: {kind: 'http', path: '/local-files/rf/input.cf32', url: location.origin + value.url,
        size: value.bytes, sampleRate: value.context.sample_rate_hz}};
    status(`${value.metadata.samples} samples × ${value.context.channels.length} channels · ${value.context.sample_rate_hz} S/s · ${value.metadata.timestamp_ns} ns`);
  };
  capture.onchange = guarded(select);
  get('rf-window').onchange = guarded(select);
  get('rf-save').onclick = guarded(async () => { await save(); status('Saved skill.grc on host'); });
  get('rf-run').onclick = guarded(async () => { await select(); if (!selected) return; const token = await hooks.run(); status(token ? 'Running in browser · original acquisition metadata bound' : 'Run did not start; see the GNU Radio console'); });
  get('rf-native').onclick = guarded(async () => {
    if (!selected) { await select(); if (!selected) return; }
    hooks.stop(); await save();
    const result = await api('/rf/native', {capture: capture.value, window: Number(get('rf-window').value)});
    dialog('Native window results', body => {
      const pre = document.createElement('pre'); pre.textContent = JSON.stringify(result, null, 2); body.append(pre);
    }); status('Native DSP completed');
  });
  get('rf-files').onclick = guarded(async () => {
    const files = await api('/rf/files');
    dialog('Project source and deployment configuration', body => {
      const picker = document.createElement('select'); picker.ariaLabel = 'Project file';
      for (const name of Object.keys(files)) picker.add(new Option(name, name));
      const text = document.createElement('textarea'); text.id = 'rf-file-editor'; text.spellcheck = false;
      const load = () => { text.value = files[picker.value]; }; picker.onchange = load; load();
      const button = document.createElement('button'); button.textContent = 'Save file';
      const state = document.createElement('span');
      button.onclick = async () => { try { await api('/rf/file', {name: picker.value, text: text.value}); files[picker.value] = text.value; state.textContent = ' Saved'; }
        catch (error) { state.textContent = String(error); } };
      const help = document.createElement('p'); help.textContent = 'Use Embedded Python Block → Code on the canvas for streaming DSP. processor.py consumes the complete processed window in the native skill; report.py maps its output to UCI. Configuration is independent of the selected capture.';
      body.append(help, picker, text, button, state);
    }); status('Edit project files');
  });
  get('rf-export').onclick = guarded(async () => {
    dialog('Export native AMS-GRA RF MEL/UCI skill', body => {
      const intro = document.createElement('p'); intro.textContent = 'Select plots to retain. RX processing and TX stay mandatory. Unselected plots and unused branches are removed. No test recording is exported. Retained plots require a deployment display.'; body.append(intro);
      const boxes: HTMLInputElement[] = [];
      for (const block of hooks.graph().blocks.filter((b: any) => b.id.startsWith('qtgui_') && b.states?.state !== 'disabled')) {
        const label = document.createElement('label'); const box = document.createElement('input'); box.type = 'checkbox'; box.value = block.name;
        label.append(box, ' ' + block.name); body.append(label); boxes.push(box);
      }
      const note = document.createElement('p'); note.textContent = 'Set actual calibration, provider, antenna channels and TX power in Project files → config/. Browser-only JavaScript blocks and unsupported native blocks produce an explicit export error.';
      const button = document.createElement('button'); button.id = 'rf-confirm-export'; button.textContent = 'Export skill';
      const outcome = document.createElement('pre');
      button.onclick = async () => { button.disabled = true; outcome.textContent = 'Compiling edited graph and exporting…';
        try { hooks.stop(); await save(); const value = await api('/rf/export', {keep_plots: boxes.filter(b => b.checked).map(b => b.value)});
          outcome.textContent = JSON.stringify(value, null, 2); status('Export saved on host'); }
        catch (error) { outcome.textContent = String(error); }
        finally { button.disabled = false; }
      }; body.append(note, button, outcome);
    }); status('Select deployment visualizations');
  });
  // Always reopen the persisted skill, never a capture-dependent autosaved run.
  hooks.load(session.graph);
  (window as any).__rfHarness = {hooks, session, select};
  await select();
}

async function api(path: string, value?: any): Promise<any> {
  const response = await fetch(path, value === undefined ? {cache: 'no-store'} :
    {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || response.statusText);
  return result;
}

function dialog(title: string, build: (body: HTMLElement) => void): void {
  const modal = document.createElement('dialog'); modal.className = 'rf-dialog';
  const heading = document.createElement('h2'); heading.textContent = title;
  const body = document.createElement('div'); const close = document.createElement('button');
  close.textContent = 'Close'; close.onclick = () => { modal.close(); modal.remove(); };
  modal.append(heading, body, close); build(body); document.body.append(modal); modal.showModal();
}
