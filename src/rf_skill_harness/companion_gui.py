"""Side panel for stock Companion: test input selection and reviewed native export.

GRC 3.10 has no supported general menu-plugin API. This panel launches unmodified
Companion and contributes its RF endpoints through GRC_BLOCKS_PATH.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from PyQt5 import QtCore, QtWidgets

from .companion import grc_environment, init_companion, read_graph, visualization_blocks
from .workbench import available_recordings


class SkillPanel(QtWidgets.QWidget):
    def __init__(self, workspace, *, start_editor=True):
        super().__init__()
        self.root = Path(workspace).resolve()
        self.setWindowTitle('RF Skill — Companion tools')
        self.resize(650, 720)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('<b>Design in GNU Radio Companion</b><br>Save skill.grc before Preview or Export.')
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(QtWidgets.QLabel(str(self.root)))
        help_text = QtWidgets.QLabel('Custom DSP: add an Embedded Python Block and open its code editor.\n'
            'Plot taps: connect Qt GUI sinks anywhere in your graph.\n'
            'RX stays in volts; TX is normalized full scale. Captures are test inputs.')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        form = QtWidgets.QFormLayout()
        self.capture = QtWidgets.QComboBox()
        self.capture.setObjectName('capture_selector')
        form.addRow('Test capture', self.capture)
        self.window = QtWidgets.QSpinBox()
        self.window.setRange(0, 1_000_000_000)
        form.addRow('Acquisition window', self.window)
        layout.addLayout(form)
        buttons = QtWidgets.QHBoxLayout()
        browse = QtWidgets.QPushButton('Add capture…')
        preview = QtWidgets.QPushButton('Preview saved graph')
        browse.clicked.connect(self.browse_capture)
        preview.clicked.connect(self.preview)
        preview.setShortcut('Ctrl+Return')
        preview.setToolTip('Preview the saved graph (Ctrl+Enter)')
        buttons.addWidget(browse); buttons.addWidget(preview)
        layout.addLayout(buttons)
        self.preview_button = preview
        self.capture_info = QtWidgets.QLabel()
        self.capture_info.setWordWrap(True)
        layout.addWidget(self.capture_info)
        layout.addWidget(QtWidgets.QLabel('<b>Native export: choose which plots to retain</b>'))
        self.plots = QtWidgets.QListWidget()
        self.plots.setObjectName('export_plot_selection')
        layout.addWidget(self.plots)
        refresh = QtWidgets.QPushButton('Refresh from saved graph')
        refresh.clicked.connect(self.refresh_plots)
        layout.addWidget(refresh)
        explanation = QtWidgets.QLabel('Unchecked plots and unused plot branches are removed. RX/TX DSP and RF endpoints are mandatory.\n'
            'Retained Qt plots need a display at deployment. Default export runs headless.\n'
            'Configure config/ calibration, provider and TX power before export.\n'
            'Finite-window profile: streaming-block history resets at each window. Export is not certification.')
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.export_button = QtWidgets.QPushButton('Export native AMS-GRA RF skill…')
        self.export_button.clicked.connect(self.export)
        self.export_button.setShortcut('Ctrl+Shift+E')
        layout.addWidget(self.export_button)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        if os.environ.get('RF_COMPANION_BROWSER') == '1':
            edit = QtWidgets.QPushButton('Edit project file… (Ctrl+O)')
            edit.setShortcut('Ctrl+O')
            edit.clicked.connect(self.edit_project_file)
            layout.addWidget(edit)
        self.process = None
        self.editor = None
        self.capture.currentIndexChanged.connect(self.select_capture)
        self.window.valueChanged.connect(self.save_selection)
        catalog = available_recordings(self.root)
        settings = self.root / 'companion-session.json'
        selected = json.loads(settings.read_text()) if settings.is_file() else {}
        for label, path in catalog.items():
            self.capture.addItem(label, path)
        if selected.get('recording') and self.capture.findData(selected['recording']) < 0:
            self.capture.addItem(Path(selected['recording']).name, selected['recording'])
        if selected.get('recording'):
            self.capture.setCurrentIndex(self.capture.findData(selected['recording']))
        self.window.setValue(selected.get('window', 0))
        self.select_capture()
        self.refresh_plots()
        if start_editor:
            executable = shutil.which('gnuradio-companion')
            if not executable:
                raise RuntimeError('Install GNU Radio Companion 3.10 in the selected Python environment')
            env = grc_environment()
            env['PYTHONPATH'] = str(self.root) + os.pathsep + env.get('PYTHONPATH', '')
            self.editor = subprocess.Popen([sys.executable, executable, str(self.root / 'skill.grc')],
                cwd=self.root, env=env)

    def browse_capture(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, 'Select receiver I/Q directory')
        if path:
            self.capture.addItem(Path(path).name, path)
            self.capture.setCurrentIndex(self.capture.count() - 1)

    def edit_project_file(self):
        filename = QtWidgets.QFileDialog.getOpenFileName(self, 'Edit project file', str(self.root),
            'Project files (*.py *.json *.grc *.txt);;All files (*)')[0]
        if filename:
            subprocess.Popen(['mousepad', '--disable-server', filename], cwd=self.root)

    def save_selection(self):
        if self.capture.currentData():
            (self.root / 'companion-session.json').write_text(json.dumps({
                'recording': self.capture.currentData(), 'window': self.window.value()}, indent=2) + '\n')

    def select_capture(self):
        path = self.capture.currentData()
        self.preview_button.setEnabled(bool(path))
        if not path:
            self.capture_info.setText('No capture selected. The skill can be designed and exported without one.')
            return
        try:
            from .archive import Archive
            archive = Archive(path)
            self.window.setMaximum(archive.manifest['windows'] - 1)
            self.capture_info.setText(f"{len(archive.channels)} coherent channels · {archive.rate:g} samples/s · {archive.carrier:g} Hz\n"
                f"{archive.manifest['windows']} acquisition windows · {archive.config.get('voltage_reference', 'complex volts')}")
            self.save_selection()
        except Exception as error:
            self.preview_button.setEnabled(False)
            self.capture_info.setText(str(error))

    def refresh_plots(self):
        try:
            prior = {self.plots.item(i).data(QtCore.Qt.UserRole): self.plots.item(i).checkState()
                     for i in range(self.plots.count())}
            self.plots.clear()
            for block in visualization_blocks(read_graph(self.root / 'skill.grc')):
                item = QtWidgets.QListWidgetItem(f"{block['name']} ({block['id']})", self.plots)
                item.setData(QtCore.Qt.UserRole, block['name'])
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(prior.get(block['name'], QtCore.Qt.Unchecked))
        except Exception as error:
            self.log.appendPlainText(str(error))

    def selected_plots(self):
        return [self.plots.item(i).data(QtCore.Qt.UserRole) for i in range(self.plots.count())
                if self.plots.item(i).checkState() == QtCore.Qt.Checked]

    def start(self, arguments):
        if self.process and self.process.state() != QtCore.QProcess.NotRunning:
            self.log.appendPlainText('Wait for the current preview/export to finish.')
            return
        self.process = QtCore.QProcess(self)
        self.process.setWorkingDirectory(str(self.root))
        self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(lambda: self.log.appendPlainText(
            bytes(self.process.readAllStandardOutput()).decode(errors='replace')))
        self.process.finished.connect(lambda code, status: self.log.appendPlainText(f'Process exited: {code}'))
        self.process.start(sys.executable, ['-m', 'rf_skill_harness.companion', *arguments])

    def preview(self):
        self.save_selection()
        self.start(['preview', str(self.root)])

    def export(self):
        self.refresh_plots()
        exports = self.root / 'exports'
        exports.mkdir(exist_ok=True)
        name = json.loads((self.root / 'skill.json').read_text())['name']
        destination = QtWidgets.QFileDialog.getSaveFileName(self, 'New export directory', str(exports / name))[0]
        if destination:
            arguments = ['export', str(self.root), '--output', destination]
            for name in self.selected_plots():
                arguments += ['--keep-plot', name]
            self.start(arguments)

    def closeEvent(self, event):
        if self.process and self.process.state() != QtCore.QProcess.NotRunning:
            self.process.terminate()
            if not self.process.waitForFinished(3000):
                self.process.kill(); self.process.waitForFinished(3000)
        # The editor owns the saved/unsaved design prompts. It remains usable
        # when this optional tools panel closes.
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--name', default='my-rf-skill')
    parser.add_argument('--transmit', action='store_true', help='Include TX in a NEW project')
    parser.add_argument('--no-editor', action='store_true', help='The browser service supervises the editor separately')
    args = parser.parse_args(argv)
    root = args.workspace.expanduser().resolve()
    if not (root / 'skill.json').is_file():
        init_companion(root, args.name, transmit=args.transmit)
    if not (root / 'skill.grc').is_file():
        parser.error('This is not a Companion skill project; use a new workspace for this branch')
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    panel = SkillPanel(root, start_editor=not args.no_editor)
    panel.show()
    return application.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
