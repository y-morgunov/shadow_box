#!/usr/bin/env python3
"""Image → ray-aligned stencil and printable cube. All geometry in mm."""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageOps, ImageDraw
import shapely
from shapely.geometry import box, LineString, Polygon
from shapely.ops import unary_union, nearest_points
from shapely.affinity import scale
import trimesh


@dataclass
class Settings:
    size: float = 120
    thickness: float = 3
    border: float = 8
    bridge: float = 1.6
    resolution: int = 100
    threshold: int = 128
    wall_distance: float = 600  # emitter to wall, not box to wall
    source_diameter: float = 2
    invert: bool = False
    led_bore: float = 6
    led_protrusion: float = 5  # emitter height above the support tube

    def validate(self):
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError(f'{key}: требуется конечное число')
        if not 60 <= self.size <= 400:
            raise ValueError('Размер куба: 60–400 мм')
        if not 1 <= self.thickness <= self.size / 12:
            raise ValueError('Толщина: от 1 мм до 1/12 размера куба')
        if not self.thickness + 1 <= self.border < self.size / 4:
            raise ValueError('Рамка должна быть шире толщины + 1 мм и уже четверти куба')
        if not 0.8 <= self.bridge <= self.border:
            raise ValueError('Перемычка: от 0.8 мм до ширины рамки')
        if not 24 <= self.resolution <= 240 or not 0 <= self.threshold <= 255:
            raise ValueError('Разрешение: 24–240; порог: 0–255')
        if self.wall_distance <= self.size / 2:
            raise ValueError('Стена должна находиться за передней панелью')
        if not 0 <= self.source_diameter < self.size - 2 * self.thickness:
            raise ValueError('Источник должен помещаться внутри куба')
        if not 2 <= self.led_bore <= self.size / 5:
            raise ValueError('Диаметр канала держателя: 2 мм – 1/5 размера куба')
        if not 0 <= self.led_protrusion < self.size / 2 - 2 * self.thickness:
            raise ValueError('Недопустимая высота светодиода над держателем')


def polygons(geometry):
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [p for g in geometry.geoms for p in polygons(g)]


def load_image(path, c):
    with Image.open(path) as source:
        rgba = ImageOps.exif_transpose(source).convert('RGBA')
        image = Image.new('RGBA', rgba.size, 'white')
        image.alpha_composite(rgba)
        image = image.convert('L')
    image.thumbnail((c.resolution, c.resolution), Image.Resampling.LANCZOS)
    canvas = Image.new('L', (c.resolution, c.resolution), 255)
    canvas.paste(image, ((c.resolution-image.width)//2, (c.resolution-image.height)//2))
    if c.invert:
        canvas = ImageOps.invert(canvas)
    return canvas


def stencil(image, c):
    """White transmits light. Join dark islands to the outer frame."""
    n = c.resolution
    width = c.size - 2*c.border
    pitch = width/n
    bright = np.asarray(image) >= c.threshold
    strips = []
    for row in range(n):
        changes = np.diff(np.r_[False, bright[row], False].astype(int))
        for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
            strips.append(box(-width/2+start*pitch, width/2-(row+1)*pitch,
                              -width/2+end*pitch, width/2-row*pitch))
    if not strips:
        raise ValueError('Изображение полностью чёрное: нет отверстий. Измените порог.')
    holes = unary_union(strips)
    # Round sub-pixel point contacts, then simplify below half a pixel.
    holes = holes.buffer(pitch*0.12).buffer(-pitch*0.12).simplify(pitch*0.12, preserve_topology=True)
    outer = box(-c.size/2, -c.size/2+c.thickness, c.size/2, c.size/2-c.thickness)
    material = outer.difference(holes)
    bridges = 0
    while len(polygons(material)) > 1:
        parts = sorted(polygons(material), key=lambda p: p.area, reverse=True)
        connected = parts[0]
        target = min(parts[1:], key=connected.distance)
        a, b = nearest_points(connected, target)
        link = LineString([a, b]).buffer(c.bridge/2, cap_style='round')
        material = unary_union([material, link]).intersection(outer)
        bridges += 1
        if bridges > n*n:
            raise ValueError('Не удалось соединить островки изображения')
    holes = outer.difference(material)
    if holes.is_empty:
        raise ValueError('После добавления перемычек отверстий не осталось')
    return holes, bridges, pitch


def solid_box(extents, center):
    mesh = trimesh.creation.box(extents)
    mesh.apply_translation(center)
    return mesh


def union(meshes):
    return trimesh.boolean.union(meshes, engine='manifold')


def difference(mesh, cutters):
    return trimesh.boolean.difference([mesh, *cutters], engine='manifold')


def tapered_hole(poly, c):
    """Project the aperture at y=size/2 backwards to inner surface."""
    near = c.size/2-c.thickness-0.1
    far = c.size/2+0.1
    mesh = trimesh.creation.extrude_polygon(poly, far-near, engine='earcut')
    v = mesh.vertices.copy()
    y = v[:, 2] + near
    mesh.vertices = np.column_stack((v[:, 0]*y/(c.size/2), y,
                                     c.size/2+v[:, 1]*y/(c.size/2)))
    # (x, image-up, depth) -> (x, depth, up) reverses handedness.
    mesh.invert()
    return mesh


def build_parts(holes, c):
    s, t = c.size, c.thickness
    body = difference(solid_box([s, s, s-t], [0, 0, (s-t)/2]), [
        solid_box([s-2*t, s, s], [0, t, s/2+t]),
        solid_box([s+2, t+1, s], [0, s/2-t/2+0.5, s/2+t])])
    # Hollow LED support. Its top is below the desired emitting centre.
    height = s/2-c.led_protrusion
    post = trimesh.creation.cylinder(c.led_bore/2+2, height, sections=48)
    post.apply_translation([0, 0, height/2])
    body = union([body, post])
    bore = trimesh.creation.cylinder(c.led_bore/2, height+2, sections=48)
    bore.apply_translation([0, 0, height/2])
    # Rear-bottom cable notch: easy to route without threading a USB plug.
    channel = solid_box([c.led_bore, s/2+2, t+2], [0, -s/4, (t+2)/2-1])
    body = difference(body, [bore, channel])
    panel = difference(solid_box([s, t, s-2*t], [0, s/2-t/2, s/2]),
                       [tapered_hole(p, c) for p in polygons(holes)])
    lid = solid_box([s, s, t], [0, 0, s-t/2])
    # Four inner locating tabs, 0.3 mm clearance. Lid remains removable.
    tabs = []
    for x in (-s/2+t+1.3, s/2-t-1.3):
        for y in (-s/4, s/4):
            tabs.append(solid_box([2, 10, 5], [x, y, s-t-2]))
    lid = union([lid, *tabs])
    return {'body': body, 'panel': panel, 'lid': lid}


def transmission(holes, c, samples=1, pixels=480):
    """Ray test at both aperture surfaces; finite uniform disk emitter.

    Relative transmission only: excludes room light, inverse-square falloff,
    side-wall/holder occlusion and reflections. Those require a full renderer.
    """
    extent = c.size * c.wall_distance/(c.size/2) * 1.1
    coordinates = np.linspace(-extent/2, extent/2, pixels)
    x, z = np.meshgrid(coordinates, coordinates[::-1])
    result = np.zeros_like(x)
    radius = c.source_diameter/2
    for i in range(samples):
        r = 0 if samples == 1 else radius*math.sqrt((i+0.5)/samples)
        angle = i*math.pi*(3-math.sqrt(5))
        sx, sz = r*math.cos(angle), r*math.sin(angle)
        allowed = np.ones(x.shape, dtype=bool)
        for distance in (c.size/2-c.thickness, c.size/2):
            q = distance/c.wall_distance
            hx = (sx*(1-q)+x*q)*(c.size/2)/distance
            hz = (sz*(1-q)+z*q)*(c.size/2)/distance
            allowed &= shapely.contains_xy(holes, hx, hz)
        result += allowed
    return Image.fromarray(np.uint8(result/samples*255)), extent


def preview(image, holes, c, out):
    ideal, extent = transmission(holes, c)
    real, _ = transmission(holes, c, samples=49)
    ideal.save(out/'projection-point.png')
    real.save(out/'projection-finite.png')
    sheet = Image.new('RGB', (1440, 540), '#e9e5dd')
    draw = ImageDraw.Draw(sheet)
    for index, (pic, label) in enumerate([(image, 'INPUT (black = shadow)'),
                                         (ideal, 'POINT SOURCE / bridges included'),
                                         (real, f'FINITE SOURCE / {c.source_diameter:g} mm')]):
        sheet.paste(pic.resize((460, 460)), (index*480+10, 44))
        draw.text((index*480+12, 14), label, fill='#202020')
    draw.text((12, 518), f'Projection crop: {extent:.0f} mm wide. Relative transmission; not a room photograph.', fill='#202020')
    sheet.save(out/'preview.png')


def generate(image_path, out, c):
    c.validate()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    image = load_image(image_path, c)
    holes, bridges, pitch = stencil(image, c)
    parts = build_parts(holes, c)
    checks = {}
    for name, mesh in parts.items():
        components = len(mesh.split())
        if not mesh.is_volume or components != 1:
            raise ValueError(f'{name}: модель не является единым замкнутым телом')
        checks[name] = {'watertight': bool(mesh.is_watertight), 'components': components,
                        'volume_mm3': float(mesh.volume), 'triangles': len(mesh.faces)}
    # Assembly coordinates retained in these files, convenient for Blender.
    for name, mesh in parts.items():
        mesh.export(out/f'{name}-assembled.stl')
        flat = mesh.copy()
        if name == 'panel':
            flat.apply_transform(trimesh.transformations.rotation_matrix(math.pi/2, [1, 0, 0]))
        elif name == 'lid':
            flat.apply_transform(trimesh.transformations.rotation_matrix(math.pi, [1, 0, 0]))
        flat.apply_translation([-flat.bounds[0, 0], -flat.bounds[0, 1], -flat.bounds[0, 2]])
        flat.export(out/f'{name}.stl')
    preview(image, holes, c, out)
    magnification = c.wall_distance/(c.size/2)
    report = {'settings': asdict(c), 'input': str(Path(image_path).resolve()),
              'bridges_added': bridges, 'sampling_pitch_mm': pitch,
              'magnification': magnification,
              'image_window_on_wall_mm': (c.size-2*c.border)*magnification,
              'geometric_blur_diameter_mm': c.source_diameter*(magnification-1),
              'parts': checks,
              'limitations': ['binary stencil, not continuous-tone photo',
                              'preview tests panel only; no room illumination or holder occlusion',
                              'thin details can be fragile; inspect slicing before printing',
                              'physical fit, heat and projection not tested']}
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    (out/'apertures.wkt').write_text(holes.wkt)
    return report


def demo(path):
    """Copy the bundled illustration without depending on a user's files."""
    source = Path(__file__).resolve().parent/'examples/astronaut/input.png'
    with Image.open(source) as image:
        image.save(path)


def main():
    p = argparse.ArgumentParser(description='Картинка → STL теневого куба; размеры в мм')
    p.add_argument('image', nargs='?', type=Path)
    p.add_argument('--out', type=Path, default=Path('output'))
    p.add_argument('--demo', action='store_true', help='Использовать пример с астронавтом')
    for name, field in Settings.__dataclass_fields__.items():
        flag = '--'+name.replace('_', '-')
        if isinstance(field.default, bool):
            p.add_argument(flag, action='store_true')
        else:
            p.add_argument(flag, type=int if name in ("resolution", "threshold") else float, default=field.default)
    args = p.parse_args()
    c = Settings(**{key: getattr(args, key) for key in Settings.__dataclass_fields__})
    if args.demo:
        args.out.mkdir(parents=True, exist_ok=True)
        args.image = args.out/'input.png'
        demo(args.image)
    if args.image is None:
        p.error('Укажите PNG/JPG/WebP или --demo')
    try:
        report = generate(args.image, args.out, c)
    except (ValueError, OSError) as e:
        p.exit(2, f'Ошибка: {e}\n')
    print(f"Готово: {args.out.resolve()}\nПеремычки: {report['bridges_added']}; "
          f"окно на стене: {report['image_window_on_wall_mm']:.0f} мм; "
          f"размытие: {report['geometric_blur_diameter_mm']:.1f} мм")


if __name__ == '__main__':
    main()
