#!/usr/bin/env python3
"""Local desktop interface for the radial shadow generator."""
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import trimesh
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
import pyqtgraph.opengl as gl

from gui_settings import CONTROL_SPECS, INTEGER_CONTROLS, validated_controls, generator_arguments

ROOT = Path(__file__).resolve().parent


class Picture(W.QLabel):
    def __init__(self):
        super().__init__('Здесь появится изображение')
        self.original = G.QPixmap()
        self.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 180)
        self.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Ignored)
        self.setStyleSheet('background:#f5f4ef; border-radius:8px; color:#687078')

    def load(self, path):
        self.original = G.QPixmap(str(path))
        self.fit()

    def fit(self):
        if not self.original.isNull():
            self.setPixmap(self.original.scaled(self.size(), C.Qt.AspectRatioMode.KeepAspectRatio,
                                               C.Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class Window(W.QMainWindow):
    def __init__(self, settings_store=None):
        super().__init__()
        self.settings_store = settings_store if settings_store is not None else C.QSettings('ShadowCube','Constructor')
        self.restoring = True
        palette = G.QPalette()
        for role, color in [(G.QPalette.ColorRole.Window,'#fafbfc'), (G.QPalette.ColorRole.WindowText,'#243441'),
                            (G.QPalette.ColorRole.Base,'#ffffff'), (G.QPalette.ColorRole.Text,'#243441'),
                            (G.QPalette.ColorRole.Button,'#eef2f4'), (G.QPalette.ColorRole.ButtonText,'#243441'),
                            (G.QPalette.ColorRole.Highlight,'#177965'), (G.QPalette.ColorRole.HighlightedText,'#ffffff')]:
            palette.setColor(role,G.QColor(color))
        W.QApplication.instance().setStyle("Fusion")
        W.QApplication.instance().setPalette(palette)
        self.setPalette(palette)
        self.setMinimumSize(980,640)
        self.setWindowTitle('Теневая лампа · конструктор')
        self.resize(1180, 790)
        self.image_path = ROOT/'examples/astronaut/input.png'
        self.result = None
        self.pending = None
        self.log = ''
        self.buffer = ''
        self.cancelled = False
        self.mesh_item = None
        self.controls = {}
        self.process = C.QProcess(self)
        self.process.setProcessChannelMode(C.QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)

        container = W.QWidget(); self.setCentralWidget(container)
        outer = W.QHBoxLayout(container); outer.setContentsMargins(20,20,20,20); outer.setSpacing(20)
        self.settings = W.QWidget(); self.settings.setFixedWidth(300)
        left = W.QVBoxLayout(self.settings)
        title = W.QLabel('Теневая лампа'); title.setStyleSheet('font-size:25px;font-weight:700')
        left.addWidget(title)
        subtitle = W.QLabel('Картинка → фигурные стенки → тень'); left.addWidget(subtitle)
        self.file_label = W.QLabel(); self.file_label.setWordWrap(True)
        choose = W.QPushButton('Открыть изображение…'); choose.clicked.connect(self.choose_image)
        demo = W.QPushButton('Пример с астронавтом'); demo.clicked.connect(self.demo)
        left.addWidget(choose); left.addWidget(demo); left.addWidget(self.file_label)
        presets = W.QHBoxLayout()
        for label, resolution, pixels in [('Превью',80,300),('Для печати',300,600)]:
            button = W.QPushButton(label)
            button.setToolTip(f'Детализация стенок {resolution}, превью {pixels} px. Остальные параметры сохраняются.')
            button.clicked.connect(lambda checked=False,r=resolution,p=pixels: self.apply_quality_preset(r,p))
            presets.addWidget(button)
        left.addLayout(presets)
        save_settings = W.QPushButton('Сохранить настройки…'); save_settings.clicked.connect(self.export_settings)
        load_settings = W.QPushButton('Загрузить настройки…'); load_settings.clicked.connect(self.import_settings)
        left.addWidget(save_settings); left.addWidget(load_settings)
        form = W.QFormLayout(); form.setVerticalSpacing(10)
        for key,label,low,high,value,step in CONTROL_SPECS:
            spin = W.QDoubleSpinBox() if key not in INTEGER_CONTROLS else W.QSpinBox()
            spin.setRange(low,high); spin.setSingleStep(step); spin.setValue(value)
            if isinstance(spin,W.QDoubleSpinBox): spin.setDecimals(1)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self.mark_dirty)
            self.controls[key] = spin; form.addRow(label,spin)
        for key in ('box_x','box_y'):
            self.controls[key].setToolTip('Положение короба на изображении: 0 — центр. X: плюс вправо; Y: плюс вниз. Проценты размера картинки. LED остаётся в центре короба.')
        self.controls['smoothing'].setToolTip(
            'Сглаживание геометрии STL в миллиметрах стенки. 0 — выключено. '
            'По умолчанию 0,8 мм; большие значения удаляют мелкие детали.')
        left.addLayout(form)
        self.light_model = W.QComboBox()
        self.light_model.addItem('LED направлен к стене', 'led')
        self.light_model.addItem('Всесторонний — теоретический', 'isotropic')
        self.light_model.currentIndexChanged.connect(self.mark_dirty)
        left.addWidget(self.light_model)
        preset = W.QPushButton('Тень 1 м · короб 10 × 10 × 12 см')
        preset.clicked.connect(self.meter_preset); left.addWidget(preset)
        self.invert = W.QCheckBox('Инвертировать чёрное и белое'); self.invert.toggled.connect(self.mark_dirty)
        left.addWidget(self.invert)
        self.quality = W.QComboBox(); self.quality.addItems(['Быстрое превью · 300 px','Точное превью · 600 px'])
        self.quality.currentIndexChanged.connect(self.mark_dirty); left.addWidget(self.quality)
        hint = W.QLabel('Чёрное создаёт тень. Положение бокса задаётся X и Y. Размер свечения — размер светящейся области LED.')
        hint.setWordWrap(True); hint.setStyleSheet('color:#65717c; font-size:12px'); left.addWidget(hint)
        self.generate_button = W.QPushButton('Создать модель и превью'); self.generate_button.setObjectName('primary')
        self.generate_button.clicked.connect(self.generate); left.addWidget(self.generate_button)
        left.addStretch()
        scroll = W.QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        scroll.setWidget(self.settings); scroll.setFixedWidth(322)
        outer.addWidget(scroll)
        right = W.QVBoxLayout(); outer.addLayout(right,1)
        self.tabs = W.QTabWidget(); right.addWidget(self.tabs,1)
        self.view = gl.GLViewWidget(); self.view.setBackgroundColor('#edf0f3')
        grid = gl.GLGridItem(); grid.setSize(240,240); grid.setSpacing(20,20); grid.setColor((120,135,150,100))
        self.view.addItem(grid)
        model_page = W.QWidget(); model_layout = W.QVBoxLayout(model_page)
        self.part_choice = W.QComboBox(); self.part_choice.addItem('Собранная модель', 'radial-box.stl')
        self.part_choice.currentIndexChanged.connect(self.show_selected_part)
        model_layout.addWidget(self.part_choice); model_layout.addWidget(self.view,1)
        row = W.QHBoxLayout(); row.addWidget(W.QLabel('ЛКМ — вращать · колесо — масштаб · Ctrl + ЛКМ — сдвиг'))
        reset = W.QPushButton('Сбросить вид'); reset.clicked.connect(self.reset_view); row.addWidget(reset)
        model_layout.addLayout(row); self.tabs.addTab(model_page,'3D-модель')
        self.preview = Picture(); self.tabs.addTab(self.preview,'Размытие тени')
        self.lighting = Picture(); self.tabs.addTab(self.lighting,'Освещение стены')
        self.point = Picture(); self.tabs.addTab(self.point,'Идеальная тень')
        self.source = Picture(); self.tabs.addTab(self.source,'Исходник')
        self.info = W.QLabel(); self.info.setWordWrap(True); right.addWidget(self.info)
        self.status = W.QLabel('Выберите изображение или используйте пример.'); self.status.setWordWrap(True); self.status.setMinimumHeight(44); right.addWidget(self.status)
        self.progress = W.QProgressBar(); self.progress.setRange(0,1); self.progress.setValue(0); self.progress.setTextVisible(False)
        right.addWidget(self.progress)
        bottom = W.QHBoxLayout(); right.addLayout(bottom)
        self.cancel = W.QPushButton('Отменить'); self.cancel.clicked.connect(self.cancel_job); self.cancel.setEnabled(False)
        self.save = W.QPushButton('STL сборки…'); self.save.clicked.connect(self.save_stl)
        self.kit = W.QPushButton('Комплект для K1…'); self.kit.clicked.connect(self.save_kit); self.kit.setEnabled(False)
        self.folder = W.QPushButton('Папка результата'); self.folder.clicked.connect(self.open_folder)
        bottom.addWidget(self.cancel); bottom.addStretch(); bottom.addWidget(self.kit); bottom.addWidget(self.folder); bottom.addWidget(self.save)
        self.save.setEnabled(False); self.folder.setEnabled(False)
        self.setStyleSheet('''QWidget {background:#fafbfc; color:#243441;} QLabel {color:#243441;} QPushButton {padding:9px 12px; background:#eef2f4; border:1px solid #cbd5dc; border-radius:4px;} QPushButton:disabled {color:#9aa6af;} QCheckBox {color:#243441;}
            QPushButton#primary {background:#177965; color:white; font-weight:600; border:0; border-radius:6px;}
            QSpinBox,QDoubleSpinBox,QComboBox {padding:5px; background:white; color:#243441;} QTabBar::tab {padding:9px; background:#e5ecef;} QTabBar::tab:selected {background:white; color:#177965;} QTabWidget::pane {border:1px solid #d6dfe4;}
            QProgressBar {max-height:5px; background:#e1e8ec;border:0;} QProgressBar::chunk {background:#177965;}''')
        self.controls['box_y'].setValue(14)
        self.update_input()
        initial = ROOT/'examples/astronaut'
        if (initial/'report.json').exists():
            self.load_result(initial)
            self.status.setText('Открыт готовый пример. Измените настройки и нажмите «Создать модель и превью».')
        saved = self.settings_store.value('state')
        if saved:
            try:
                self.restore_settings(json.loads(saved))
                self.status.setText('Настройки восстановлены. Пересоздайте модель; на экране — готовый пример.')
                if self.missing_saved_image:
                    self.status.setText('Настройки восстановлены, но исходник недоступен. Выберите изображение; пока открыт пример.')
            except (ValueError,TypeError):
                self.status.setText('Сохранённые настройки повреждены. Используются значения по умолчанию.')
        self.restoring = False

    def settings_snapshot(self):
        return dict(version=1,controls={k:s.value() for k,s in self.controls.items()},
                    image=str(self.image_path),invert=self.invert.isChecked(),
                    light_model=self.light_model.currentData(),preview_pixels=300 if self.quality.currentIndex()==0 else 600)

    def persist_settings(self):
        if not self.restoring:
            self.settings_store.setValue('state',json.dumps(self.settings_snapshot(),ensure_ascii=False))
            self.settings_store.sync()

    def restore_settings(self, data):
        controls = validated_controls(data)
        was_restoring = self.restoring
        self.restoring = True
        try:
            for key,value in controls.items():
                self.controls[key].setValue(value)
            self.invert.setChecked(data['invert'])
            self.light_model.setCurrentIndex(self.light_model.findData(data['light_model']))
            self.quality.setCurrentIndex(0 if data['preview_pixels']==300 else 1)
            path = Path(data['image'])
            self.missing_saved_image = not path.is_file() or G.QImage(str(path)).isNull()
            if not self.missing_saved_image: self.image_path = path
            self.update_input()
        finally:
            self.restoring = was_restoring

    def apply_quality_preset(self, resolution, pixels):
        self.controls['resolution'].setValue(resolution)
        self.quality.setCurrentIndex(0 if pixels==300 else 1)
        self.mark_dirty()

    def export_settings(self):
        name,_ = W.QFileDialog.getSaveFileName(self,'Сохранить настройки',str(Path.home()/'shadow-settings.json'),'JSON (*.json)')
        if not name: return
        path = Path(name)
        if path.suffix.lower() != '.json': path = path.with_suffix('.json')
        try:
            path.write_text(json.dumps(self.settings_snapshot(),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            self.status.setText('Настройки сохранены: '+str(path))
        except OSError as exc: W.QMessageBox.warning(self,'Ошибка сохранения',str(exc))

    def import_settings(self):
        name,_ = W.QFileDialog.getOpenFileName(self,'Загрузить настройки',str(Path.home()),'JSON (*.json)')
        if not name: return
        try:
            self.restore_settings(json.loads(Path(name).read_text(encoding='utf-8')))
            self.mark_dirty()
            if self.missing_saved_image:
                self.status.setText('Настройки загружены, но файл изображения недоступен. Выберите исходник; оставлено текущее изображение.')
        except (OSError,ValueError,TypeError) as exc:
            W.QMessageBox.warning(self,'Ошибка загрузки настроек',str(exc))

    def meter_preset(self):
        for key,value in dict(size=100,height=120,span=1000,source_diameter=1,
                              beam_angle=120,flux=100,ambient=1,white_lux=100).items():
            self.controls[key].setValue(value)
        self.light_model.setCurrentIndex(0)
        self.mark_dirty()

    def update_input(self):
        self.file_label.setText(self.image_path.name)
        self.file_label.setToolTip(str(self.image_path))
        self.source.load(self.image_path)

    def mark_dirty(self, *_):
        if self.restoring: return
        if hasattr(self,'status'):
            self.status.setText('Настройки изменены. Нажмите «Создать модель и превью». На экране — предыдущий результат.')
            self.persist_settings()

    def demo(self):
        self.image_path = ROOT/'examples/astronaut/input.png'
        self.controls['box_y'].setValue(14)
        self.update_input(); self.mark_dirty()

    def choose_image(self):
        name,_ = W.QFileDialog.getOpenFileName(self,'Изображение для тени',str(self.image_path.parent),
                                               'Изображения (*.png *.jpg *.jpeg *.webp *.bmp);;Все файлы (*)')
        if name:
            path = Path(name)
            if G.QImage(str(path)).isNull():
                W.QMessageBox.warning(self,'Не удалось открыть','Файл не является поддерживаемым изображением.'); return
            self.image_path = path; self.update_input(); self.mark_dirty(); self.tabs.setCurrentWidget(self.source)

    def busy(self, active):
        self.settings.setEnabled(not active)
        self.cancel.setEnabled(active)
        self.progress.setRange(0,0 if active else 1)
        if not active: self.progress.setValue(1)

    def generate(self):
        size,span = self.controls['size'].value(),self.controls['span'].value()
        if not size*2 <= span <= size*15:
            W.QMessageBox.warning(self,'Проверьте размер',f'Для этого основания ширина картинки должна быть от {size*2:g} до {size*15:g} мм.'); return
        offset = max(abs(self.controls[k].value()) for k in ('box_x','box_y'))
        if offset/100*span+size/2 > span/2:
            W.QMessageBox.warning(self,'Положение короба','Короб выходит за границы картинки. Уменьшите смещение или размер основания.'); return
        if not self.image_path.is_file():
            W.QMessageBox.warning(self,'Нет картинки','Выберите существующий файл изображения.'); return
        self.pending = ROOT/'output/gui'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        args = generator_arguments(ROOT/'radial_shadow.py', self.pending, self.settings_snapshot())
        self.log = ''; self.buffer = ''; self.cancelled = False
        self.busy(True); self.status.setText('Запуск генератора… Можно продолжать вращать предыдущую модель.')
        self.process.start(sys.executable,args)

    def read_output(self):
        text = bytes(self.process.readAllStandardOutput()).decode('utf-8',errors='replace')
        self.log += text; self.buffer += text
        while '\n' in self.buffer:
            line,self.buffer = self.buffer.split('\n',1)
            if line.startswith('STAGE:'): self.status.setText(line[6:]+'…')

    def process_error(self, error):
        if error == C.QProcess.ProcessError.FailedToStart:
            self.busy(False); self.status.setText('Не удалось запустить Python: '+self.process.errorString())

    def finished(self, code, exit_status):
        self.read_output(); self.busy(False)
        if self.cancelled:
            self.status.setText('Генерация отменена. Предыдущий результат сохранён.'); return
        if code != 0:
            self.status.setText('Не удалось создать модель. Предыдущий результат сохранён.')
            message = W.QMessageBox(self); message.setWindowTitle('Ошибка генерации')
            message.setText('Проверьте картинку и параметры. Подробности доступны ниже.')
            message.setDetailedText(self.log[-12000:]); message.exec(); return
        try:
            self.load_result(self.pending)
            self.status.setText('Готово. Модель и превью сохранены; STL можно экспортировать.')
            self.tabs.setCurrentIndex(0)
        except Exception as exc:
            self.status.setText('Ошибка открытия результата: '+str(exc))

    def load_result(self, directory):
        report = json.loads((directory/'report.json').read_text())
        mesh = trimesh.load_mesh(directory/'radial-box.stl')
        if not mesh.is_volume: raise ValueError('STL не является замкнутым телом')
        self.result = directory
        self.part_choice.blockSignals(True); self.part_choice.clear()
        self.part_choice.addItem('Собранная модель', 'radial-box.stl')
        if (directory/'print-k1.zip').exists():
            for name,label in [('base','Основание'),('post','Стойка')]+[(f'wall-{i}',f'Стенка {i}') for i in range(1,5)]:
                self.part_choice.addItem(label+' — положение печати',f'print-k1/{name}.stl')
        self.part_choice.blockSignals(False)
        self.display_mesh(mesh)
        self.preview.load(directory/'projection-finite.png'); self.point.load(directory/'projection-point.png')
        light_path = directory/'projection-lighting.png'
        self.lighting.original = G.QPixmap()
        self.lighting.clear()
        if light_path.exists(): self.lighting.load(light_path)
        else: self.lighting.setText('Пересоздайте пример для расчёта освещения.')
        c = report['settings']
        self.info.setText(f"Результат: основание {c['size']:g} мм · картинка {c['span']:g} мм · "
                          f"{len(mesh.faces):,} треугольников · замкнутое тело\n"
                          'Освещение — расчётная оценка; нагрев, стойка и отражения не учтены.')
        if 'lighting' in report:
            light = report['lighting']
            self.info.setText(self.info.text()+
                f"\nБез препятствий: край {light['edge_lux']:.1f} лк · угол {light['corner_lux']:.1f} лк · "
                f"угол/ось {100*light['corner_axis_ratio']:.2f}%"+
                ('\n'+' '.join(light['warnings']) if light['warnings'] else ''))
        self.kit.setEnabled((directory/'print-k1.zip').exists())
        if 'printing' in report:
            pr = report['printing']
            message = 'K1 / 0,4 мм: 6 деталей, сборка на клей; поддержки включить в слайсере.'
            if not pr['all_parts_fit']: message = 'НЕ ВХОДИТ В K1: уменьшите размеры и пересоздайте комплект.'
            self.info.setText(self.info.text()+'\n'+message)
        self.save.setEnabled(True); self.folder.setEnabled(True)

    def show_selected_part(self, *_):
        if self.result and self.part_choice.currentData():
            self.display_mesh(trimesh.load_mesh(self.result/self.part_choice.currentData()))

    def display_mesh(self, mesh):
        direction = np.array([.4,-.5,.8]); direction /= np.linalg.norm(direction)
        brightness = .55+.45*np.maximum(0,mesh.face_normals@direction)
        colors = np.ones((len(mesh.faces),4),dtype=np.float32)
        colors[:,:3] = brightness[:,None]*np.array([.36,.72,.75])
        item = gl.GLMeshItem(vertexes=np.asarray(mesh.vertices,dtype=np.float32), faces=np.asarray(mesh.faces,dtype=np.uint32),
                             smooth=False,faceColors=colors,drawEdges=False)
        if self.mesh_item is not None: self.view.removeItem(self.mesh_item)
        self.mesh_item = item; self.view.addItem(item)
        self.extent = float(mesh.extents.max()); self.center = mesh.bounds.mean(axis=0)
        self.reset_view()

    def reset_view(self):
        center = getattr(self,'center',np.array([0,0,25]))
        self.view.opts['center'] = G.QVector3D(*[float(x) for x in center])
        self.view.setCameraPosition(distance=getattr(self,'extent',100)*2.7,elevation=28,azimuth=40)

    def cancel_job(self):
        self.cancelled = True; self.cancel.setEnabled(False); self.process.kill()

    def save_stl(self):
        if not self.result: return
        name,_ = W.QFileDialog.getSaveFileName(self,'Сохранить модель',str(Path.home()/'shadow-lamp.stl'),'STL (*.stl)')
        if name:
            dest = Path(name)
            if dest.suffix.lower() != '.stl': dest = dest.with_suffix('.stl')
            try:
                src = self.result/'radial-box.stl'
                if dest.resolve() != src.resolve(): shutil.copy2(src,dest)
                self.status.setText('STL сохранён: '+str(dest))
            except OSError as exc: W.QMessageBox.warning(self,'Ошибка сохранения',str(exc))

    def save_kit(self):
        if not self.result or not (self.result/'print-k1.zip').exists(): return
        name,_ = W.QFileDialog.getSaveFileName(self,'Комплект STL для Creality K1',
                                              str(Path.home()/'shadow-lamp-k1.zip'),'ZIP (*.zip)')
        if name:
            dest = Path(name)
            if dest.suffix.lower() != '.zip': dest = dest.with_suffix('.zip')
            try:
                src = self.result/'print-k1.zip'
                if dest.resolve() != src.resolve(): shutil.copy2(src,dest)
                self.status.setText('Комплект сохранён. Откройте PRINT.md перед печатью.')
            except OSError as exc: W.QMessageBox.warning(self,'Ошибка сохранения',str(exc))

    def open_folder(self):
        if self.result: G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(self.result)))

    def closeEvent(self,event):
        self.persist_settings()
        if self.process.state() != C.QProcess.ProcessState.NotRunning:
            self.process.kill(); self.process.waitForFinished(3000)
        event.accept()


def main():
    app = W.QApplication(sys.argv); app.setStyle('Fusion')
    window = Window(); window.show()
    sys.exit(app.exec())

if __name__ == '__main__': main()
