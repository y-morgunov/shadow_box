"""Split the assembled geometry into glue-up parts, preserving the ray surfaces."""
import json
import math
import zipfile
import numpy as np
import trimesh
import manifold3d
from shapely.geometry import Polygon
from shadow_cube import solid_box, difference


def intersection(a,b):
    return trimesh.boolean.intersection([a,b],engine='manifold')


def miter_wall(wall,side,size,height):
    limit = max(size,height)*3
    clip = trimesh.creation.extrude_polygon(Polygon([(0,0),(limit,limit),(-limit,limit)]),2*limit,engine='earcut')
    clip.apply_translation([0,0,-limit])
    clip.apply_transform(np.round(trimesh.transformations.rotation_matrix(side*math.pi/2,[0,0,1])))
    return intersection(wall,clip)


def trim_planes(mesh, planes):
    """Cut the assembly with planes without re-intersecting coincident faces."""
    solid = manifold3d.Manifold(manifold3d.Mesh64(
        np.array(mesh.vertices, dtype=np.float64, order='C', copy=True),
        np.array(mesh.faces, dtype=np.uint64, order='C', copy=True)))
    for normal, offset in planes:
        solid = solid.trim_by_plane(normal, offset)
    if solid.status() != manifold3d.Error.NoError:
        raise ValueError(f'Ошибка разбиения сборки: {solid.status()}')
    data = solid.to_mesh64()
    result = trimesh.Trimesh(vertices=data.vert_properties[:, :3],
                             faces=data.tri_verts, process=False)
    # A cut through a corner can leave a closed, zero-volume sheet on the
    # dividing plane. It is no physical fragment and cannot survive STL.
    pieces = result.split(only_watertight=False)
    volumes = [piece.volume for piece in pieces]
    if any(volume == 0 for volume in volumes):
        result = trimesh.util.concatenate([
            piece for piece,volume in zip(pieces,volumes) if volume != 0])
    return result


def split_parts(body, walls, size, height, thickness):
    # Exact butt joints at z=3. All cuts use the final assembly as their source:
    # independently re-cutting walls can resurrect fragments merged at corners.
    parts = [('base', trim_planes(body, [([0,0,-1], -3)]), None)]
    # Rebuild the known tube exactly; cutting the STL at its coplanar base can
    # retain microscopic plate slivers and misleading 20 mm bounding boxes.
    post = trimesh.creation.cylinder(5,height-18,sections=48)
    post.apply_translation([0,0,3+(height-18)/2])
    bore = trimesh.creation.cylinder(3,height,sections=48)
    bore.apply_translation([0,0,height/2])
    parts.append(('post',difference(post,[bore]),None))
    for side in range(4):
        planes = [([0,0,1],3)]
        for normal,offset in [((0,1),size/2-thickness),((1,1),0),((-1,1),0)]:
            x,y = normal
            for _ in range(side): x,y = -y,x
            planes.append(([x,y,0],offset))
        part = trim_planes(body, planes)
        parts.append((f'wall-{side+1}',part,side))
    return parts


def oriented(mesh, side):
    result = mesh.copy()
    transform = np.eye(4)
    if side is not None:
        rotation = trimesh.transformations.rotation_matrix(-side*math.pi/2,[0,0,1])
        lay = np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]],dtype=float)
        transform = lay@rotation
        result.apply_transform(transform)
    offset = -result.bounds[0]
    result.apply_translation(offset)
    transform[:3,3] += offset
    return result,transform


def export_kit(body,walls,args,out,exporter):
    directory = out/'print-k1'; directory.mkdir(exist_ok=True)
    assembled_dir = directory/'assembly'; assembled_dir.mkdir(exist_ok=True)
    records = []
    parts = split_parts(body,walls,args.size,args.height,args.thickness)
    if abs(sum(part.volume for _,part,_ in parts)-body.volume) > max(.1,body.volume*1e-5):
        raise ValueError('Разбиение деталей изменило объём сборки; комплект не экспортирован')
    for name,part,side in parts:
        if not part.is_volume or (getattr(args, 'supports', True) and len(part.split()) != 1):
            raise ValueError(f'Деталь {name} не образует единый объём; измените толщину или рисунок')
        exporter(part,assembled_dir/f'{name}.stl')
        laid,transform = oriented(part,side)
        checked = exporter(laid,directory/f'{name}.stl')
        extents = checked.extents
        fits = bool(np.all(extents <= np.array([210,210,240])+1e-4))
        normals = checked.face_normals
        # Downward surfaces above the first layer may need supports.
        overhang = (normals[:,2] < -math.cos(math.radians(45))) & (checked.triangles_center[:,2] > .2)
        records.append(dict(name=name,file=f'{name}.stl',size_mm=extents.tolist(),
                            components=len(checked.split()),
                            fits_k1_with_margin=fits,volume_mm3=float(checked.volume),
                            possible_support_area_mm2=float(checked.area_faces[overhang].sum()),
                            print_to_assembly=np.linalg.inv(transform).tolist()))
    notes = ['Сборка на клей: стыки без защёлок и натяга.',
             'В слайсере включить автоматические поддержки; проверить слои перед печатью.',
             'Реальная печать, прочность и нагрев не проверены.']
    if not getattr(args, 'supports', True):
        notes.insert(0, 'Соединительные перемычки отключены: отдельные фрагменты нужно закрепить при сборке. Поддержки слайсера не соединяют их в готовом изделии.')
    if args.min_feature == 0:
        notes.append('Фильтр мелких элементов отключён; проверьте воспроизводимость тонких деталей в слайсере.')
    if not all(r['fits_k1_with_margin'] for r in records):
        notes.insert(0,'НЕ ПЕЧАТАТЬ в текущих размерах: некоторые детали не входят в рабочую область с запасом.')
    data = dict(printer='Creality K1',nozzle_mm=args.nozzle,build_volume_mm=[220,220,250],
                usable_with_margin_mm=[210,210,240],layer_height_mm=.2,
                profile_feature_mm=args.min_feature,parts=records,warnings=notes,
                all_parts_fit=all(r['fits_k1_with_margin'] for r in records))
    (directory/'print-report.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    rows = '\n'.join(f"| {r['file']} | {' × '.join(f'{v:.1f}' for v in r['size_mm'])} | {'да' if r['fits_k1_with_margin'] else 'НЕТ'} |" for r in records)
    instructions = f'''# Комплект для Creality K1, сопло {args.nozzle:g} мм

Размеры в миллиметрах. Одна деталь на столе за один запуск. STL в этом каталоге уже ориентированы для печати, минимум Z=0. Не используйте автоматическую переориентацию стенок.

| Деталь | Габариты, мм | Входит с запасом |
|---|---|---|
{rows}

Область K1: 220 × 220 × 250 мм; проверка использует 210 × 210 × 240 мм, оставляя место под кайму. Не уменьшайте отдельные детали масштабированием: это нарушит сборку и оптику. Если деталь не входит, уменьшите параметры в программе и пересоздайте весь комплект.

## Печать

Слой 0,2 мм, минимум 3 периметра; непрозрачный пластик. Температуры и скорости — профиль выбранного материала и K1 в слайсере. Для тонких стенок используйте сплошное заполнение. Кайма 4 мм, особенно для стойки. Включите автоматические поддержки, в том числе от модели, и проверьте послойное превью: лучевые скосы стенок могут требовать поддержек даже при печати плашмя. У основания есть нижняя канавка провода. Стойка печатается вертикально; у высоких вариантов требуется особенно хорошее сцепление со столом.

## Сборка

1. Удалите поддержки, не повредив кромки рисунка. Проведите провода через канал основания и стойку до склейки.
2. Основание лежит стороной с канавкой к стене. Стойку приклейте по центру над отверстием. Её нижний торец соответствует высоте Z=3 мм сборки.
3. Стенки приклейте нижними срезами к верхней стороне основания, наружные поверхности расположите по краям квадрата. Угловые стыки скошены; сведите их без перекрытия. Используйте временный прямоугольный упор при склейке.
4. Нумерация, если смотреть на открытый короб: wall-1 сверху (+Y), wall-2 слева (-X), wall-3 снизу (-Y), wall-4 справа (+X). Наружная поверхность стенки при печати обращена к столу. Сохраните номера на деталях после снятия со стола.
5. Центр свечения разместите на высоте {args.height:g} мм от стены. Крепление LED и металлического радиатора подбирается отдельно; пластиковая стойка не заменяет радиатор.

Файлы assembly/*.stl сохраняют координаты собранного изделия: импортируйте их совместно с сохранением координат для проверки расположения. Их не нужно печатать в этой ориентации. Матрицы восстановления координат есть в print-report.json. Файлы в корне — те же детали в положении печати.

Фильтр мелких элементов и соединительные перемычки применяются только при включении соответствующих настроек. Это изменяет тень и учитывается в превью. Проверка ширины — геометрическая фильтрация, не гарантия прочности каждого острого кончика.

'''+'\n'.join('- '+note for note in notes)+'\n'
    (directory/'PRINT.md').write_text(instructions)
    archive = out/'print-k1.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for file in sorted(directory.rglob('*')):
            if file.is_file(): z.write(file,str(file.relative_to(directory)))
    return data
