"""Supervise the real Companion desktop and software graphics behind noVNC."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

from .companion import grc_environment, init_companion


def graphics_probe():
    """Require an actual server-side Qt OpenGL context, not just installed libs."""
    from PyQt5 import QtGui, QtWidgets
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    application = QtWidgets.QApplication([])
    context = QtGui.QOpenGLContext()
    fmt = QtGui.QSurfaceFormat()
    fmt.setVersion(2, 1)
    context.setFormat(fmt)
    if not context.create():
        raise RuntimeError('Server-side Qt OpenGL context creation failed')
    surface = QtGui.QOffscreenSurface()
    surface.setFormat(context.format()); surface.create()
    if not context.makeCurrent(surface):
        raise RuntimeError('Server-side Qt OpenGL context could not become current')
    context.doneCurrent()
    details = subprocess.check_output(['glxinfo', '-B'], text=True)
    renderer = next((v.split(':', 1)[1].strip() for v in details.splitlines()
                     if 'OpenGL renderer string:' in v), '')
    if not renderer or 'llvmpipe' not in renderer.lower():
        raise RuntimeError('Expected server-side Mesa llvmpipe software rendering: ' + renderer)
    print(json.dumps({'qt_opengl': True, 'renderer': renderer,
        'opengl_version': f'{context.format().majorVersion()}.{context.format().minorVersion()}',
        'gtk_version': f'{Gtk.get_major_version()}.{Gtk.get_minor_version()}'}), flush=True)


def serve(workspace, *, name='my-rf-skill', transmit=False, resolution='1920x1080'):
    if not re.fullmatch(r'[1-3][0-9]{3}x[1-2][0-9]{3}', resolution):
        raise ValueError('Resolution must be WIDTHxHEIGHT, e.g. 1920x1080')
    root = Path(workspace).expanduser().resolve()
    if not (root / 'skill.json').is_file():
        init_companion(root, name, transmit=transmit)
    if not (root / 'skill.grc').is_file():
        raise ValueError('Use a new directory or an existing Companion skill project')
    processes, logs = {}, {}
    previous = {}
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, interrupted)
    with tempfile.TemporaryDirectory(prefix='rf-companion-server-') as temporary:
        runtime = Path(temporary)
        # A writable private HOME also works for arbitrary host UIDs and for
        # rootless Docker's container-root to host-owner mapping.
        home = runtime / 'home'; home.mkdir()
        xdg = runtime / 'xdg'; xdg.mkdir(mode=0o700)
        env = grc_environment()
        env.update(HOME=str(home), XDG_RUNTIME_DIR=str(xdg), DISPLAY=':99',
            XDG_CONFIG_HOME=str(home / '.config'), XDG_CACHE_HOME=str(home / '.cache'),
            QT_QPA_PLATFORM='xcb', QT_OPENGL='software', QT_X11_NO_MITSHM='1',
            LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe',
            RF_COMPANION_BROWSER='1',
            PYTHONPATH=str(root) + os.pathsep + env.get('PYTHONPATH', ''))
        preferences = home / '.gnuradio/config.conf'
        preferences.parent.mkdir()
        preferences.write_text('[grc]\neditor = mousepad\nxterm_executable = xterm\n')
        # GNU Radio 3.10 builds also support XDG configuration locations.
        modern = home / '.config/gnuradio/config.conf'
        modern.parent.mkdir(parents=True)
        modern.write_text(preferences.read_text())
        def launch(label, args):
            stream = (runtime / (label + '.log')).open('w')
            logs[label] = stream
            processes[label] = subprocess.Popen(args, env=env, cwd=root,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        def check():
            for label, process in processes.items():
                if process.poll() is not None:
                    raise RuntimeError(f'{label} exited ({process.returncode}):\n'
                        + (runtime / (label + '.log')).read_text()[-10000:])
        def wait_until(predicate, description):
            deadline = time.monotonic() + 60
            while not predicate():
                check()
                if time.monotonic() > deadline:
                    raise RuntimeError('Startup timed out waiting for ' + description)
                time.sleep(.1)
        try:
            launch('display', ['Xvfb', ':99', '-screen', '0', resolution + 'x24',
                '+extension', 'GLX', '-nolisten', 'tcp', '-ac', '-noreset'])
            def display_ready():
                return subprocess.run(['xdpyinfo'], env=env, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL).returncode == 0
            wait_until(display_ready, 'the virtual display')
            probe = subprocess.run([sys.executable, '-m', 'rf_skill_harness.companion_server', '--probe'], env=env,
                text=True, capture_output=True, timeout=30)
            if probe.returncode:
                raise RuntimeError('Graphics self-test failed:\n' + probe.stdout + probe.stderr)
            graphics = json.loads(probe.stdout.strip().splitlines()[-1])
            launch('window-manager', ['openbox'])
            launch('vnc', ['x11vnc', '-display', ':99', '-localhost', '-rfbport', '5900',
                '-nopw', '-forever', '-shared', '-xkb', '-repeat', '-noxdamage', '-quiet'])
            launch('tools', [sys.executable, '-m', 'rf_skill_harness.companion_gui',
                '--workspace', str(root), '--no-editor'])
            launch('editor', [sys.executable, '/usr/bin/gnuradio-companion', str(root / 'skill.grc')])
            def windows():
                result = subprocess.run(['wmctrl', '-lp'], env=env, text=True, capture_output=True)
                if result.returncode:
                    return {}
                return {int(parts[2]): parts[0] for line in result.stdout.splitlines()
                        if len(parts := line.split(None, 4)) >= 4 and parts[2].isdigit()}
            def applications_ready():
                listing = windows()
                return (processes['editor'].pid in listing and processes['tools'].pid in listing
                    and '>>> Done' in (runtime / 'editor.log').read_text())
            wait_until(applications_ready, 'Companion and the RF Skill panel')
            listing = windows()
            for label, geometry in [('editor', '0,0,0,1260,1000'), ('tools', '0,1270,0,630,1000')]:
                subprocess.run(['wmctrl', '-ir', listing[processes[label].pid], '-e', geometry], env=env, check=True)
            web = runtime / 'web'; web.mkdir()
            for item in Path('/usr/share/novnc').iterdir():
                (web / item.name).symlink_to(item)
            health = {'status': 'ready', 'graphics': graphics,
                'instance': os.environ.get('RF_COMPANION_INSTANCE', ''),
                'applications': ['GNU Radio Companion', 'RF Skill tools'],
                'rendering': 'server-side software OpenGL', 'resolution': resolution}
            (web / 'health.json').write_text(json.dumps(health) + '\n')
            launch('browser', ['websockify', '--web', str(web), '--heartbeat', '30',
                '0.0.0.0:6080', '127.0.0.1:5900'])
            print(json.dumps(health, indent=2), flush=True)
            print('Companion browser desktop ready. Host launcher publishes port 6080 on loopback.', flush=True)
            while True:
                check()
                time.sleep(.2)
        except KeyboardInterrupt:
            return 0
        finally:
            # Kill entire child process groups, including GNU Radio previews and
            # text editors; repeated launches do not inherit stale display locks.
            for process in reversed(list(processes.values())):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for process in processes.values():
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL); process.wait()
            for stream in logs.values():
                stream.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=Path('/workspace'))
    parser.add_argument('--name', default='my-rf-skill')
    parser.add_argument('--transmit', action='store_true')
    parser.add_argument('--resolution', default='1920x1080')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.probe:
        graphics_probe(); return 0
    try:
        return serve(args.workspace, name=args.name, transmit=args.transmit, resolution=args.resolution)
    except Exception as error:
        print(f'Companion server failed: {error}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
