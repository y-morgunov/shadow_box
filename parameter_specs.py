"""Shared numeric limits for the CLI, desktop and web constructor."""

# key, label, minimum, maximum, default, step
CONTROL_SPECS = [
    ('size', 'Основание, мм', 20, 1000, 100, 1),
    ('height', 'Источник над стеной, мм', 20, 2000, 70, 1),
    ('span', 'Картинка на стене, мм', 21, 100000, 600, 10),
    ('thickness', 'Толщина стенок, мм', .1, 30, 2, .1),
    ('box_x', 'Центр короба X, %', -49, 49, 0, 1),
    ('box_y', 'Центр короба Y, %', -49, 49, 0, 1),
    ('bridge', 'Перемычки, мм', .05, 20, 1.4, .05),
    ('source_diameter', 'Размер свечения, мм', 0, 100, 2, .1),
    ('beam_angle', 'Угол LED (½ силы), °', 1, 179, 120, 1),
    ('flux', 'Световой поток, лм', .01, 100000, 100, 10),
    ('ambient', 'Свет в комнате, лк', 0, 100000, 1, 1),
    ('white_lux', 'Белый в превью, лк', .01, 1000000, 100, 10),
    ('min_feature', 'Мин. элемент, мм (0 — без фильтра)', 0, 20, 0, .05),
    ('smoothing', 'Сглаживание краёв, мм', 0, 10, .15, .05),
    ('threshold', 'Порог чёрного', 0, 255, 128, 1),
    ('resolution', 'Детализация стенок', 20, 2000, 360, 10),
]
INTEGER_CONTROLS = frozenset(('threshold', 'resolution'))


def validate_geometry(controls):
    size, height, span = (controls[k] for k in ('size', 'height', 'span'))
    if span <= size:
        raise ValueError('Размер картинки должен быть больше основания')
    if controls['thickness'] >= size/2:
        raise ValueError('Толщина стенки должна быть меньше половины основания')
    offset = max(abs(controls[k]) for k in ('box_x', 'box_y'))/100
    if offset*span + size/2 > span/2:
        raise ValueError('Короб выходит за границы картинки')
    if height*(1-size/(span*(1+2*offset))) <= 5:
        raise ValueError('Недостаточно высоты для стенок над нижней рейкой; увеличьте высоту или картинку')


def validate_supports(data):
    value = data.get('supports', True)  # Existing presets keep their bridges.
    if type(value) is not bool:
        raise ValueError('Параметр supports должен быть логическим')
    return value
