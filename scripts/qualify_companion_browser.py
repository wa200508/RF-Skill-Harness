"""Real Chromium/noVNC editing, plotting, export, reconnect and restart checks.

CI/developer dependencies: numpy, Playwright and its Chromium browser. These
are not required on a user's host to launch the workbench.
"""
import argparse
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
from rf_skill_harness.demo import write_demo


def wait_for(predicate, description, seconds=60):
    deadline = time.monotonic() + seconds
    while True:
        result = predicate()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out waiting for ' + description)
        time.sleep(.1)


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chromium', help='Optional system Chromium executable')
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='rf-browser-qualification-') as temporary:
        root = Path(temporary)
        capture = write_demo(root / 'capture', kind='radar', windows=2)
        workspace = root / 'skill'
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        url = base + '/vnc.html?autoconnect=true&resize=scale'
        process = None
        log = (output / 'server.log').open('w')
        def health():
            if process is not None and process.poll() is not None:
                raise RuntimeError('Server exited; see ' + str(output / 'server.log'))
            try:
                with urlopen(base + '/health.json', timeout=1) as response:
                    value = json.load(response)
                return value if value.get('status') == 'ready' else None
            except OSError:
                return None
        def start():
            nonlocal process
            process = subprocess.Popen([sys.executable, str(source / 'scripts/launch_companion_browser.py'),
                '--image', args.image, '--no-pull', '--workspace', str(workspace),
                '--recordings', str(capture), '--port', str(port), '--transmit'],
                stdout=log, stderr=subprocess.STDOUT)
            return wait_for(health, 'browser desktop readiness', 100)
        def stop():
            nonlocal process
            if process is not None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(); raise
                process = None
        try:
            initial = start()
            assert initial['graphics']['qt_opengl'] and 'llvmpipe' in initial['graphics']['renderer'].lower()
            container = subprocess.check_output(['docker', 'ps', '--filter',
                'label=org.rf-skill-harness.companion.instance=' + initial['instance'], '--format', '{{.ID}}'], text=True).strip()
            assert container
            def execute(*command):
                return subprocess.check_output(['docker', 'exec', '-e', 'DISPLAY=:99', container, *command], text=True)
            def windows():
                return execute('wmctrl', '-l')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium, headless=True, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1920, 'height': 1200})
                page.goto(url); page.wait_for_selector('html.noVNC_connected', timeout=30000)
                page.wait_for_timeout(400)
                page.screenshot(path=str(output / 'desktop.png'))
                def click(x, y):
                    rect = page.locator('canvas').bounding_box()
                    page.mouse.click(rect['x'] + rect['width'] * x / 1920,
                                     rect['y'] + rect['height'] * y / 1080)
                tools_id = next(v.split()[0] for v in windows().splitlines() if 'Companion tools' in v)
                def focus_tools():
                    execute('wmctrl', '-ia', tools_id)
                    click(1740, 60)
                # Actual browser mouse input starts the selected window's DSP.
                focus_tools(); page.keyboard.press('Control+Enter')
                listing = wait_for(lambda: (v if 'completed window' in (v := windows()) else None), 'Qt RX/TX plots')
                preview_id = next(v.split()[0] for v in listing.splitlines() if 'completed window' in v)
                execute('wmctrl', '-ir', preview_id, '-e', '0,250,0,1120,1000')
                page.wait_for_timeout(500); page.screenshot(path=str(output / 'plots.png'))
                execute('wmctrl', '-ic', preview_id)
                # A real text editor receives browser key events and saves onto
                # the bind-mounted host project, proving the custom-code route.
                edited = workspace / 'browser-edit.txt'; edited.write_text('before\n')
                focus_tools(); page.keyboard.press('Control+o')
                wait_for(lambda: 'Edit project file' in windows(), 'project file picker')
                page.keyboard.press('Control+l'); page.keyboard.type('/workspace/browser-edit.txt', delay=15)
                page.keyboard.press('Enter')
                listing = wait_for(lambda: (v if 'browser-edit.txt' in (v := windows()) else None), 'text editor')
                editor_id = next(v.split()[0] for v in listing.splitlines() if 'browser-edit.txt' in v)
                execute('wmctrl', '-ir', editor_id, '-e', '0,200,150,1000,700')
                execute('wmctrl', '-ia', editor_id)
                click(450, 400)
                page.keyboard.press('Control+a'); page.keyboard.type('RF skill browser edit round trip\n', delay=20)
                page.keyboard.press('Control+s')
                wait_for(lambda: 'browser edit round trip' in edited.read_text(), 'host-visible saved edit')
                execute('wmctrl', '-ic', editor_id)
                # Fixture-only calibration/power: export qualification, not a
                # hardware calibration or a claim that RF energy was emitted.
                path = workspace / 'config/runtime.json'; config = json.loads(path.read_text())
                config['volts_per_count'] = {'rx0': .001}; config['transmit']['tx_power_w'] = 1
                path.write_text(json.dumps(config))
                focus_tools(); page.keyboard.press('Control+Shift+e')
                wait_for(lambda: 'New export directory' in windows(), 'export directory dialog')
                page.keyboard.press('Enter')
                exported = workspace / 'exports/my-rf-skill/export-manifest.json'
                wait_for(exported.is_file, 'persistent native export')
                manifest = json.loads(exported.read_text())
                assert manifest['capabilities'] == ['receive', 'transmit']
                assert not list(exported.parent.rglob('*.sigmf-data'))
                page.wait_for_timeout(300); page.screenshot(path=str(output / 'export.png'))
                page.reload(); page.wait_for_selector('html.noVNC_connected', timeout=30000)
                assert health()['instance'] == initial['instance']
                browser.close()
            assert process.poll() is None, 'Closing a browser must not terminate the desktop'
            stop()
            assert subprocess.run(['docker', 'inspect', container], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode != 0, 'Launcher left a container behind'
            restarted = start()
            assert restarted['instance'] != initial['instance']
            assert 'browser edit round trip' in edited.read_text() and exported.is_file()
            result = {'status': 'passed', 'graphics': initial['graphics'],
                'browser_connected': True, 'qt_rx_tx_plots': True, 'browser_edit_saved_on_host': True,
                'native_export_persisted': True, 'reconnect_preserved_session': True,
                'browser_close_keeps_server_running': True, 'container_cleanup': True,
                'restart_preserves_project': True, 'fixture': 'synthetic radar protocol fixture; no radio/GPU'}
            (output / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result, indent=2))
        finally:
            stop(); log.close()


if __name__ == '__main__':
    main()
