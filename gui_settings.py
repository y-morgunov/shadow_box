"""Desktop settings schema and CLI adapter, independent of Qt.

The JSON version and control names are kept compatible with saved presets.
Unknown controls are ignored when importing, as in the original GUI.
"""
import math

CONTROL_SPECS = [('size','Основание, мм',60,300,100,1), ('height','Источник над стеной, мм',40,400,70,1),
         ('span','Картинка на стене, мм',120,4500,600,10), ('thickness','Толщина стенок, мм',1.2,5,2,.1),
         ('box_x','Центр короба X, %',-40,40,0,1),
         ('box_y','Центр короба Y, %',-40,40,0,1),
         ('bridge','Перемычки, мм',.8,5,1.4,.1), ('source_diameter','Размер свечения, мм',0,10,2,.1),
         ('beam_angle','Угол LED (½ силы), °',10,170,120,5),
         ('flux','Световой поток, лм',1,5000,100,10), ('ambient','Свет в комнате, лк',0,1000,1,1),
         ('white_lux','Белый в превью, лк',1,10000,100,10),
         ('min_feature','Мин. элемент, мм',.8,5,1.2,.1),
         ('smoothing','Сглаживание краёв, мм',0,2,.8,.1),
         ('threshold','Порог чёрного',0,255,128,1), ('resolution','Детализация стенок',40,300,180,10)]
INTEGER_CONTROLS = frozenset(('threshold', 'resolution'))


def validated_controls(data):
    """Validate the complete v1 document before any widget can be changed.

    Return recognized numeric controls, with zero offsets for older presets.
    Image existence/decodability is checked by the UI, allowing missing files
    to retain the currently selected image.
    """
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('controls'), dict):
        raise ValueError('Неизвестный формат настроек')
    specs = {spec[0]: spec for spec in CONTROL_SPECS}
    controls = {'box_x': 0, 'box_y': 0}
    for key, value in data['controls'].items():
        if key not in specs:
            continue
        _, _, low, high, _, _ = specs[key]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not low <= value <= high
                or key in INTEGER_CONTROLS and value != int(value)):
            raise ValueError('Недопустимый параметр: ' + key)
        controls[key] = int(value) if key in INTEGER_CONTROLS else value
    if (data.get('light_model') not in ('led', 'isotropic') or data.get('preview_pixels') not in (300, 600)
            or type(data.get('invert')) is not bool or not isinstance(data.get('image'), str)):
        raise ValueError('Некорректные параметры изображения или освещения')
    return controls


def generator_arguments(script, output, settings):
    """Build QProcess arguments without shell parsing or quoting paths."""
    controls = validated_controls(settings)
    args = ['-u', str(script), settings['image'], '--out', str(output)]
    for key, value in controls.items():
        args.extend(['--' + key.replace('_', '-'), str(value)])
    args.extend(['--light-model', settings['light_model'],
                 '--preview-pixels', str(int(settings['preview_pixels']))])
    if settings['invert']:
        args.append('--invert')
    return args
