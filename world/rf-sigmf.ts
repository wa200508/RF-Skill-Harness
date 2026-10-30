// SPDX-License-Identifier: GPL-3.0-or-later
// Brian Day's SigMF Viewer is a peer workspace; GNU Radio World stays the editor.
type Tap = {block: string; port: number; channels: number; datatype: string};
type Hooks = {graph(): any; outputs(): Tap[]; run(): Promise<unknown>; stop(): void;
  viewer(button: HTMLButtonElement, panel: HTMLElement): () => void};
let inspection: any = null;

export function inspectionRunContext(doc: any): void {
  if (!inspection) return;
  const type = inspection.datatype === 'cf32_le' ? 'complex' : 'float';
  let source = inspection.block, port = String(inspection.port);
  const add = (name: string, id: string, parameters: any) => doc.blocks.push({name, id, parameters,
    states: {state: 'enabled', coordinate: [0, 0], rotation: 0}});
  if (inspection.channels > 1) {
    add('rf_inspection_flatten', 'blocks_vector_to_stream', {type, vlen: '1', num_items: String(inspection.channels)});
    doc.connections.push([source, port, 'rf_inspection_flatten', '0']); source = 'rf_inspection_flatten'; port = '0';
  }
  add('rf_inspection_sink', 'wasm_sigmf_sink', {type, file: '/local-output/rf-inspection/signal',
    sample_rate: String(inspection.sample_rate), center_freq: '0', layout: 'real', annotate_tags: 'False',
    author: '', description: 'RF development stream inspection', hw_info: ''});
  doc.connections.push([source, port, 'rf_inspection_sink', '0']);
}

export function installSigmf(hooks: Hooks, bar: HTMLElement, selected: () => any,
                             select: () => Promise<void>, status: (text: string) => void): void {
  const button = document.createElement('button'); button.className = 'workspace-tab';
  button.id = 'rf-sigmf-tab'; button.role = 'tab'; button.textContent = 'SigMF Viewer';
  button.setAttribute('aria-controls', 'rf-sigmf-panel');
  const panel = document.createElement('section'); panel.id = 'rf-sigmf-panel'; panel.className = 'workspace-panel'; panel.hidden = true;
  panel.role = 'tabpanel'; panel.setAttribute('aria-labelledby', button.id);
  const frame = document.createElement('iframe'); frame.id = 'rf-sigmf-frame'; frame.title = 'Brian Day SigMF Viewer';
  panel.append(frame); document.querySelector('#workspaceTabs')!.append(button); document.querySelector('#workspaceContent')!.append(panel);
  const activate = hooks.viewer(button, panel);
  let lastUrl = '/sigmf/';
  const open = (url = lastUrl) => {lastUrl = url; frame.src = url; activate();};
  button.addEventListener('click', () => { if (!frame.src) frame.src = '/sigmf/'; });
  const raw = document.createElement('button'); raw.id = 'rf-view-iq'; raw.textContent = 'View I/Q'; bar.append(raw);
  const choose = document.createElement('select'); choose.id = 'rf-inspect-tap'; choose.ariaLabel = 'Signal inspection tap';
  const refresh = () => {
    const previous = choose.value; choose.replaceChildren();
    for (const tap of hooks.outputs()) {
      choose.add(new Option(`${tap.block}:${tap.port} (${tap.channels} ch, ${tap.datatype})`, JSON.stringify(tap)));
    }
    if ([...choose.options].some(o => o.value === previous)) choose.value = previous;
    else { const preferred = [...choose.options].find(o => JSON.parse(o.value).block === 'custom_dsp'); if (preferred) choose.value = preferred.value; }
  };
  choose.addEventListener('focus', refresh); refresh(); bar.append(choose);
  const rate = document.createElement('input'); rate.id = 'rf-inspect-rate'; rate.type = 'number'; rate.min = '1'; rate.ariaLabel = 'Tap sample rate Hz';
  rate.title = 'Actual output sample rate in Hz; adjust for decimation/interpolation'; rate.placeholder = 'Tap rate (Hz)'; bar.append(rate);
  const run = document.createElement('button'); run.id = 'rf-inspect-run'; run.textContent = 'Run tap';
  const finish = document.createElement('button'); finish.id = 'rf-inspect-finish'; finish.textContent = 'Finish & view'; bar.append(run, finish);
  const guard = (fn: () => Promise<void>) => async () => {try {await fn();} catch (error) {status(String(error));}};
  const inputToken = () => selected().url.split('/').pop();
  raw.onclick = guard(async () => {
    if (inspection) throw new Error('Finish the current tap before changing the inspection.');
    if (!selected()) await select();
    if (!selected()) throw new Error('Select a test capture first');
    const result = await api('/rf/inspection/raw', {input_token: inputToken(), tap: {channels: selected().context.channels.length}});
    open(result.url); status(`Saved ${result.samples} original samples/channel in inspections/${result.id}`);
  });
  run.onclick = guard(async () => {
    if (inspection) throw new Error('Finish the current tap first');
    await select(); if (!selected()) throw new Error('Select a test capture first');
    const tap = JSON.parse(choose.value);
    const sample_rate = Number(rate.value || selected().context.sample_rate_hz);
    const ticket = await api('/rf/inspection/prepare', {input_token: inputToken(), tap: {...tap, sample_rate,
      units: tap.block === 'receive' || tap.block === 'custom_dsp' ? 'volts' : 'stream units (developer-defined)'}});
    inspection = {...tap, sample_rate, id: ticket.id};
    (window as any).__rfInspectTap = true;
    const token = await hooks.run();
    if (!token) {inspection = null; delete (window as any).__rfInspectTap; throw new Error('Run did not start; see the GNU Radio console');}
    inspection.runToken = token;
    status('Recording the selected stream. Choose Finish & view after the window has processed.');
  });
  finish.onclick = () => {if (inspection) {status('Finishing SigMF recording…'); hooks.stop();} else open();};
  // The runner's original SigMF Sink supplies the exact data Blob. No plot
  // samples, FFT bins, image pixels or resampling are used as I/Q.
  (window as any).__rfAcceptSigmf = (source: Window, token: string, path: string, message: any) => {
    const runner = document.querySelector('#runFrame') as HTMLIFrameElement;
    if (!inspection || source !== runner.contentWindow || token !== inspection.runToken || path !== '/local-output/rf-inspection/signal') return false;
    const saved = inspection;
    void (async () => {
      const response = await fetch(`/rf/inspection/${saved.id}/data`, {method: 'POST',
        headers: {'Content-Type': 'application/octet-stream'}, body: message.data});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      inspection = null; delete (window as any).__rfInspectTap;
      open(result.url); status(`Saved ${result.samples} samples/channel from ${saved.block}:${saved.port} in inspections/${result.id}`);
    })().catch(error => {inspection = null; delete (window as any).__rfInspectTap; status(String(error));});
    return true;
  };
  (window as any).__rfInspector = {open, refresh, active: () => !!inspection};
}

async function api(url: string, value: any): Promise<any> {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(value)});
  const result = await response.json(); if (!response.ok) throw new Error(result.error); return result;
}
