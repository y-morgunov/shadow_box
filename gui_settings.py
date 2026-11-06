"""Desktop settings schema and CLI adapter, independent of Qt.

The JSON version and control names are kept compatible with saved presets.
Unknown controls are ignored when importing, as in the original GUI.
"""
import math

from parameter_specs import CONTROL_SPECS, INTEGER_CONTROLS, validate_supports


def validated_controls(data):
    """Validate the complete v1 document before any widget can be changed.

    Return recognized numeric controls, with zero offsets for older presets.
    Image existence/decodability is checked by the UI, allowing missing files
    to retain the currently selected image.
    """
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('controls'), dict):
        raise ValueError('Неизвестный формат настроек')
    validate_supports(data)
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
    if not validate_supports(settings):
        args.append('--no-supports')
    if settings['invert']:
        args.append('--invert')
    return args
