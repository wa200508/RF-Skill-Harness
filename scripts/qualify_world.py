#!/usr/bin/env python3
"""Exercise GNU Radio World's actual browser DSP and native skill export."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


async def qualify(url, output, chromium=None, native_tools=True):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True,
            **({'executable_path': chromium} if chromium else {}),
            args=['--no-sandbox', '--disable-dev-shm-usage', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width': 1920, 'height': 1200})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.add_init_script("localStorage.setItem('gnuradio_world_welcome_seen', '1')")
        await page.goto(url)
        await page.wait_for_function('!!window.__rfHarness', timeout=90000)
        await page.wait_for_function("document.querySelector('#rf-status').textContent.includes('samples ×')")
        assert await page.evaluate('crossOriginIsolated && typeof SharedArrayBuffer !== "undefined"')
        session = await (await page.request.get(url + 'rf/session')).json()
        assert len(session['captures']) >= 2
        graph = session['graph']
        next(b for b in graph['blocks'] if b['name'] == 'custom_dsp')['parameters']['gain'] = '2.0'
        await page.evaluate('graph => window.__rfHarness.hooks.load(graph)', graph)
        await page.click('#rf-save')
        await page.wait_for_function("document.querySelector('#rf-status').textContent.includes('Saved')")
        await page.screenshot(path=str(output / 'editor.png'))
        await page.click('#rf-run')
        # Finite windows intentionally run unpaced. Keep upstream's ordinary
        # warning and explicitly acknowledge it as a human would.
        await page.wait_for_timeout(500)
        warning = page.locator('.unpaced-run-warning')
        if await warning.count():
            await warning.get_by_role('checkbox').check()
            await warning.get_by_role('button', name='Run anyway', exact=True).click()
        frame = page.frame_locator('#runFrame')
        runner = page.locator('#runFrame')
        await frame.locator('#result').wait_for(state='attached', timeout=30000)
        live = await (await runner.element_handle()).content_frame()
        await live.wait_for_function("document.querySelector('#result').textContent.includes('RUNNER_PASS')", timeout=120000)
        await live.wait_for_function("window.__grstats && JSON.parse(window.__grstats).blocks.some(b => b.name === 'measurements' && b.items > 0)", timeout=30000)
        await page.wait_for_timeout(1500)
        stats = await live.evaluate('JSON.parse(window.__grstats)')
        plots = await live.evaluate("window.__grGuiObservation.readPlotData('', 128)")
        (output / 'browser-dsp.json').write_text(json.dumps({'stats': stats, 'plots': plots}, indent=2))
        names = {b['name'] for b in stats['blocks'] if b.get('items', 0) > 0}
        assert {'receive', 'custom_dsp', 'measurements', 'transmit'} <= names, names
        assert {'rx_time', 'rx_spectrum', 'tx_time'} <= {w['name'] for w in plots['widgets']}
        rx = next(w for w in plots['widgets'] if w['name'] == 'rx_time')
        assert .035 < max(c['peak']['y'] for c in rx['curves']) < .05
        await page.screenshot(path=str(output / 'plots.png'))
        first_design = await (await page.request.get(url + 'rf/session')).json()
        await page.select_option('#rf-capture', session['captures'][1]['id'])
        await page.fill('#rf-window', '1'); await page.locator('#rf-window').press('Tab')
        await page.wait_for_function("document.querySelector('#rf-status').textContent.includes('123456789004000007')")
        second_design = await (await page.request.get(url + 'rf/session')).json()
        assert first_design['graph'] == second_design['graph']
        # Editing a project file through the browser persists on the host.
        await page.click('#rf-files')
        await page.get_by_role('combobox', name='Project file').select_option('processor.py')
        editor = page.locator('#rf-file-editor')
        await editor.fill((await editor.input_value()) + '\n# Saved through GNU Radio World RF tools.\n')
        await page.get_by_role('button', name='Save file', exact=True).click()
        await page.wait_for_function("document.querySelector('.rf-dialog').textContent.includes(' Saved')")
        await page.locator('.rf-dialog').get_by_role('button', name='Close', exact=True).click()
        # Brian Day's real SigMF Viewer remains a peer tab in World.
        await page.wait_for_function('!!window.__rfInspector')
        await page.click('#rf-view-iq')
        await page.wait_for_function("document.querySelector('#rf-status').textContent.includes('original samples')")
        viewer = page.frame_locator('#rf-sigmf-frame')
        await viewer.locator('.js-plotly-plot').first.wait_for(timeout=30000)
        assert 'SigMF Viewer' in await viewer.locator('body').inner_text()
        await page.screenshot(path=str(output / 'sigmf-input.png'))
        await page.click('#tabEditor')
        await page.click('#rf-inspect-run')
        await page.wait_for_function("document.querySelector('#rf-status').textContent.includes('from custom_dsp:0')", timeout=120000)
        await viewer.locator('.js-plotly-plot').first.wait_for(timeout=30000)
        await page.screenshot(path=str(output / 'sigmf-processed.png'))
        assert not await page.locator('#rf-sigmf-panel').is_hidden()
        assert await page.locator('#tabEditor').count() == 1
        after = await (await page.request.get(url + 'rf/session')).json()
        assert after['graph'] == second_design['graph'], 'Viewer instrumentation entered the design'
        native, exported = None, None
        if native_tools:
            await page.click('#rf-native')
            await page.get_by_role('heading', name='Native window results').wait_for(timeout=60000)
            native = json.loads(await page.locator('.rf-dialog pre').inner_text())
            assert native['metadata']['index'] == 1 and native['tx_samples'] == 256
            assert native['metadata']['timestamp_ns'] == '123456789004000007'
            assert native['measurements']['rms_volts']['radar_rx0'] > .03
            await page.locator('.rf-dialog').get_by_role('button', name='Close', exact=True).click()
            # Configure deliberate interface-fixture calibration/power, never infer
            # deployment parameters from the selected test recording.
            files = await (await page.request.get(url + 'rf/files')).json()
            config = json.loads(files['config/runtime.json'])
            config['volts_per_count'] = {'rx0': .001}; config['transmit']['tx_power_w'] = 1
            response = await page.request.post(url + 'rf/file', data={'name': 'config/runtime.json', 'text': json.dumps(config)})
            assert response.ok, await response.text()
            await page.click('#rf-export')
            await page.click('#rf-confirm-export')
            await page.wait_for_function("document.querySelector('.rf-dialog pre').textContent.includes('export-manifest.json') || document.querySelector('#rf-status').textContent.includes('Export saved')", timeout=60000)
            exported = json.loads(await page.locator('.rf-dialog pre').inner_text())
            await page.screenshot(path=str(output / 'export.png'))
            await page.locator('.rf-dialog').get_by_role('button', name='Close', exact=True).click()
        await page.reload(); await page.wait_for_function('!!window.__rfHarness', timeout=90000)
        reloaded = await (await page.request.get(url + 'rf/session')).json()
        assert next(b for b in reloaded['graph']['blocks'] if b['name'] == 'custom_dsp')['parameters']['gain'] == '2.0'
        assert not errors, errors
        await browser.close()
    result = {'status': 'passed', 'interface': 'gnuradio-world', 'browser_wasm_dsp': True,
        'python_gain_edited': True, 'rx_tx_samples_processed': True, 'rx_tx_plots': True, 'sigmf_input_and_processed_viewer': True,
        'dataset_switch_preserves_design': True, 'project_file_saved_in_browser': True,
        'exact_nanosecond_metadata': True, 'native_window_test': native,
        'native_export': exported, 'native_tools_exercised': native_tools, 'reload_preserves_skill': True, 'page_errors': errors}
    (output / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--image'); mode.add_argument('--url', help='Developer server with two synthetic radar collects')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chromium')
    parser.add_argument('--browser-only', action='store_true', help='Qualify editing/replay/plots without local GNU Radio')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.url:
        asyncio.run(qualify(args.url, args.output, args.chromium, not args.browser_only)); return
    from rf_skill_harness.demo import write_demo
    with tempfile.TemporaryDirectory(prefix='rf-world-qualification-') as directory:
        root = Path(directory)
        write_demo(root / 'captures' / 'a', kind='radar', windows=2)
        write_demo(root / 'captures' / 'b', kind='radar', windows=3)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
        url = f'http://127.0.0.1:{port}/'
        launcher = Path(__file__).with_name('launch_world.py')
        with (args.output / 'server.log').open('w') as log:
            process = subprocess.Popen([sys.executable, str(launcher), '--image', args.image, '--no-pull',
                '--workspace', str(root / 'skill'), '--recordings', str(root / 'captures'),
                '--transmit', '--port', str(port)], stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None: raise RuntimeError('Server stopped; inspect server.log')
                    try:
                        with urlopen(url + 'health.json', timeout=1) as response:
                            if json.load(response).get('status') == 'ready': break
                    except (OSError, ValueError): pass
                    if time.monotonic() > deadline: raise RuntimeError('World did not become ready')
                    time.sleep(.2)
                asyncio.run(qualify(url, args.output, args.chromium, not args.browser_only))
                if not args.browser_only:
                    exports = list((root / 'skill/exports').iterdir())
                    assert len(exports) == 1 and (exports[0] / 'export-manifest.json').is_file()
                    assert not list(exports[0].rglob('*.sigmf-data'))
            finally:
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=20)
            assert process.returncode == 130


if __name__ == '__main__':
    main()
