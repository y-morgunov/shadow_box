"""Run GUI integration on a graphical session: SHADOW_GUI_TEST=1 .venv/bin/python -m unittest test_gui -v"""
import os
import time
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

class SettingsTest(unittest.TestCase):
    """Settings/CLI contract checks also run without Qt or a display."""
    def snapshot(self):
        from gui_settings import CONTROL_SPECS
        return dict(version=1, controls={spec[0]: spec[4] for spec in CONTROL_SPECS},
                    image='/tmp/Картинка с пробелами.png', invert=False,
                    light_model='led', preview_pixels=300)

    def test_legacy_offsets_and_future_controls(self):
        from gui_settings import validated_controls
        data = self.snapshot()
        del data['controls']['box_x']
        del data['controls']['box_y']
        data['controls']['future_parameter'] = 'unknown'
        controls = validated_controls(data)
        self.assertEqual((controls['box_x'], controls['box_y']), (0, 0))
        self.assertNotIn('future_parameter', controls)
        self.assertNotIn('box_x', data['controls'], 'Validation must not mutate imported JSON')

    def test_invalid_controls(self):
        from gui_settings import validated_controls
        for key, value in [('flux', float('nan')), ('flux', float('inf')),
                           ('flux', True), ('height', 401), ('resolution', 80.5),
                           ('box_x', -41), ('smoothing', '0.8')]:
            with self.subTest(key=key, value=value):
                data = self.snapshot()
                data['controls'][key] = value
                with self.assertRaises(ValueError):
                    validated_controls(data)

    def test_integer_values_from_json(self):
        from gui_settings import generator_arguments
        data = self.snapshot()
        data['controls'].update(resolution=80.0, threshold=128.0)
        data['preview_pixels'] = 300.0
        args = generator_arguments('radial_shadow.py', 'output', data)
        for option, expected in [('--resolution', '80'), ('--threshold', '128'),
                                 ('--preview-pixels', '300')]:
            self.assertEqual(args[args.index(option) + 1], expected)

    def test_cli_roundtrip(self):
        from gui_settings import generator_arguments
        data = self.snapshot()
        data['controls'].update(box_x=-12, box_y=14, smoothing=.8)
        data.update(invert=True, light_model='isotropic', preview_pixels=600)
        args = generator_arguments('/tmp/a b/radial_shadow.py', '/tmp/output folder', data)
        self.assertEqual(args[:5], ['-u', '/tmp/a b/radial_shadow.py', data['image'], '--out', '/tmp/output folder'])
        self.assertEqual(args[-1], '--invert')
        values = dict(zip(args[5:-1:2], args[6:-1:2]))
        expected = {'--'+k.replace('_', '-'): str(v) for k, v in data['controls'].items()}
        expected.update({'--light-model': 'isotropic', '--preview-pixels': '600'})
        self.assertEqual(values, expected)
        self.assertEqual(len(args), 5 + 2 * len(expected) + 1)


@unittest.skipUnless(os.environ.get('SHADOW_GUI_TEST') == '1', 'GUI integration is opt-in')
class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6 import QtWidgets as W
        # Keep one QApplication alive across all test windows. Recreating it
        # between tests can destroy shared OpenGL state during Qt teardown.
        cls.app = W.QApplication.instance() or W.QApplication([])

    def setUp(self):
        from PySide6 import QtCore as C
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = C.QSettings(str(Path(self.temp.name)/'settings.ini'),C.QSettings.Format.IniFormat)

    def tearDown(self):
        from PySide6 import QtCore as C
        # Dispose GL widgets while the application and event loop still exist.
        for widget in self.app.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        C.QCoreApplication.sendPostedEvents(None, C.QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_presets_and_persistent_settings(self):
        from PySide6 import QtWidgets as W, QtCore as C
        from gui import Window
        app = self.app
        window = Window(self.store)
        try:
            self.assertEqual(window.controls['smoothing'].value(),.8)
            window.controls['height'].setValue(120)
            window.controls['span'].setValue(1000)
            window.controls['flux'].setValue(240)
            window.controls['box_x'].setValue(-12)
            window.controls['box_y'].setValue(14)
            window.invert.setChecked(True)
            window.light_model.setCurrentIndex(1)
            window.apply_quality_preset(80,300)
            self.assertEqual(window.controls['resolution'].value(),80)
            self.assertEqual(window.quality.currentIndex(),0)
            window.apply_quality_preset(300,600)
            self.assertEqual(window.controls['resolution'].value(),300)
            self.assertEqual(window.quality.currentIndex(),1)
            self.assertEqual(window.controls['height'].value(),120)
            self.assertEqual(window.controls['smoothing'].value(),.8)
            expected = window.settings_snapshot()
            exported = Path(self.temp.name)/'preset.json'
            with patch.object(W.QFileDialog,'getSaveFileName',return_value=(str(exported),'JSON')):
                window.export_settings()
            self.assertEqual(json.loads(exported.read_text()),expected)
            window.controls['flux'].setValue(100)
            with patch.object(W.QFileDialog,'getOpenFileName',return_value=(str(exported),'JSON')):
                window.import_settings()
            self.assertEqual(window.settings_snapshot(),expected)
            invalid = json.loads(exported.read_text())
            invalid['controls']['height'] = 80
            invalid['controls']['flux'] = float('nan')
            with self.assertRaises(ValueError): window.restore_settings(invalid)
            self.assertEqual(window.settings_snapshot(),expected)
        finally:
            window.close()
        # Reopen from a fresh store instance, as in a new process.
        store = C.QSettings(self.store.fileName(),C.QSettings.Format.IniFormat)
        reopened = Window(store)
        try:
            self.assertEqual(reopened.settings_snapshot(),expected)
            missing = dict(expected,image=str(Path(self.temp.name)/'missing.png'))
            reopened.restore_settings(missing)
            self.assertTrue(reopened.missing_saved_image)
            self.assertEqual(str(reopened.image_path),expected['image'])
        finally:
            reopened.close(); app.processEvents()

    def test_generation_export_and_cancel(self):
        from PySide6 import QtWidgets as W, QtCore as C, QtTest
        from gui import Window, ROOT
        import trimesh
        app = self.app
        window = Window(self.store); window.show(); QtTest.QTest.qWait(1200)
        try:
            self.assertIsNotNone(window.mesh_item)
            self.assertTrue(window.view.isValid(), 'OpenGL context is unavailable')
            window.controls['resolution'].setValue(60)
            window.meter_preset()
            window.controls['smoothing'].setValue(.6)
            self.assertEqual(window.controls['span'].value(),1000)
            old = window.result
            window.generate_button.click()
            self.assertFalse(window.settings.isEnabled())
            end = time.monotonic()+180
            while window.process.state() != C.QProcess.ProcessState.NotRunning and time.monotonic()<end:
                QtTest.QTest.qWait(50)
            self.assertEqual(window.process.state(), C.QProcess.ProcessState.NotRunning)
            QtTest.QTest.qWait(300)
            self.assertNotEqual(window.result, old, window.log)
            import json
            self.assertEqual(json.loads((window.result/'request.json').read_text())['smoothing'],.6)
            self.assertTrue(window.settings.isEnabled())
            self.assertTrue(trimesh.load_mesh(window.result/'radial-box.stl').is_volume)
            self.assertFalse(window.preview.original.isNull())
            self.assertEqual(window.preview.original.width(),300)
            self.assertFalse(window.lighting.original.isNull())
            self.assertEqual(window.lighting.original.width(),300)
            self.assertIn('угол/ось',window.info.text())
            self.assertTrue(window.kit.isEnabled())
            self.assertEqual(window.part_choice.count(),7)
            window.part_choice.setCurrentIndex(3)
            self.assertLessEqual(window.extent,210)
            archive = window.result/'saved kit.zip'
            with patch.object(W.QFileDialog,'getSaveFileName',return_value=(str(archive),'ZIP (*.zip)')):
                window.save_kit()
            self.assertEqual(archive.read_bytes(),(window.result/'print-k1.zip').read_bytes())
            window.part_choice.setCurrentIndex(0)
            dest = window.result/'export with spaces.stl'
            with patch.object(W.QFileDialog,'getSaveFileName',return_value=(str(dest),'STL (*.stl)')):
                window.save_stl()
            self.assertEqual(dest.read_bytes(),(window.result/'radial-box.stl').read_bytes())
            window.grab().save(str(ROOT/'examples/gui-screenshot.png'))
            window.tabs.setCurrentWidget(window.preview); QtTest.QTest.qWait(200)
            window.grab().save(str(ROOT/'examples/gui-preview.png'))
            success = window.result
            window.generate(); window.cancel_job()
            end = time.monotonic()+10
            while window.process.state() != C.QProcess.ProcessState.NotRunning and time.monotonic()<end:
                QtTest.QTest.qWait(50)
            QtTest.QTest.qWait(100)
            self.assertEqual(window.result,success)
            self.assertTrue(window.settings.isEnabled())
        finally:
            window.close(); app.processEvents()

if __name__ == '__main__': unittest.main()
