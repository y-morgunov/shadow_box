#!/usr/bin/env python3
"""Four sculpted side walls casting an image around a central box. Millimetres."""
import argparse
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image, ImageDraw
import shapely
from shapely.geometry import box, LineString, Polygon
from shapely.ops import unary_union, nearest_points
from scipy.ndimage import gaussian_filter1d
import trimesh
import manifold3d
from printing import export_kit, miter_wall
from lighting import illuminance, preview as light_preview, diagnostics
from shadow_cube import load_image, demo, polygons, solid_box, union, difference


def smooth_profile(material, amount):
    """Smooth uniformly sampled closed contours with a periodic Gaussian.

    Run before the minimum-feature filter and bridges: smoothing must not
    subsequently thin the structural connections. Zero preserves legacy contours.
    """
    if amount <= 0:
        return material
    def ring_points(ring):
        points = np.asarray(ring.coords)
        lengths = np.linalg.norm(np.diff(points,axis=0),axis=1)
        distance = np.r_[0,np.cumsum(lengths)]
        count = max(16, int(np.ceil(distance[-1]/min(.15,amount/4))))
        positions = np.arange(count)*distance[-1]/count
        sampled = np.column_stack([np.interp(positions,distance,points[:,i]) for i in range(2)])
        return gaussian_filter1d(sampled,amount/(distance[-1]/count),axis=0,mode='wrap')

    smoothed = []
    for poly in polygons(material):
        candidate = Polygon(ring_points(poly.exterior),
                            [ring_points(r) for r in poly.interiors])
        # Concave contours can intersect after smoothing. Keep only valid area;
        # the downstream feature filter and bridge builder restore printability.
        smoothed.extend(polygons(shapely.make_valid(candidate).buffer(0)))
    # Tiny fixed chord tolerance only reduces redundant mesh vertices. Never use
    # the user smoothing radius as a polyline simplification tolerance.
    return unary_union(smoothed).simplify(.005,preserve_topology=True)


def profiles(image, size, height, span, resolution, bridge, rail, min_feature=0, smoothing=0, box_x=0, box_y=0):
    a = size/2
    top = height*(1-size/(span*(1+2*max(abs(box_x),abs(box_y))/100.0)))
    du, dz = size/resolution, (top-1)/resolution
    dark = np.asarray(image) < 128
    result, counts = [], []
    for side in range(4):
        strips = []
        for row in range(resolution):
            z = 1+(row+0.5)*dz
            u = -a+(np.arange(resolution)+0.5)*du
            k = height/(height-z)
            x, y = u*k, np.full_like(u, a*k)
            for _ in range(side):
                x, y = -y, x
            ix = np.floor((x/span+0.5+box_x/100)*image.width).astype(int)
            iy = np.floor((0.5+box_y/100-y/span)*image.height).astype(int)
            valid = (ix >= 0)&(ix < image.width)&(iy >= 0)&(iy < image.height)
            mask = np.zeros(resolution, dtype=bool)
            mask[valid] = dark[iy[valid], ix[valid]]
            changes = np.diff(np.r_[False, mask, False].astype(int))
            for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
                strips.append(box(-a+start*du, 1+row*dz, -a+end*du, 1+(row+1)*dz))
        material = unary_union([box(-a, 1, a, rail), *strips])
        if smoothing > 0:
            material = smooth_profile(material, smoothing).intersection(box(-a,1,a,top))
            material = unary_union([material,box(-a,1,a,rail)])
        else:
            material = material.buffer(0.08).buffer(-0.08).simplify(0.06, preserve_topology=True)
        if min_feature > 0:
            # Opening removes sub-nozzle slivers/necks before reconnecting islands.
            radius = min_feature/2
            material = material.buffer(-radius).buffer(radius)
            material = unary_union([material,box(-a,1,a,rail)])
            bridge = max(bridge,min_feature)
        added = 0
        while len(polygons(material)) > 1:
            parts = polygons(material)
            anchor = max(parts, key=lambda p: p.intersection(box(-a, 1, a, rail)).area)
            target = min((p for p in parts if p is not anchor), key=anchor.distance)
            p, q = nearest_points(anchor, target)
            material = unary_union([material, LineString([p, q]).buffer(bridge/2)])
            added += 1
            if added > resolution*resolution:
                raise ValueError('Cannot connect profile')
        result.append(material)
        counts.append(added)
    return result, counts


def side_mesh(profile, side, size, height, thickness):
    # Extrusion along source rays makes both panel surfaces project identically.
    # Earcut can omit boundary triangles on detailed, otherwise valid polygons.
    # Build the solid with Manifold, preserving its indexed topology.
    profile = shapely.orient_polygons(profile)
    contours = [np.asarray(ring.coords)[:-1] for poly in polygons(profile)
                for ring in (poly.exterior, *poly.interiors)]
    solid = manifold3d.CrossSection(contours).extrude(thickness)
    if solid.status() != manifold3d.Error.NoError or solid.is_empty():
        raise ValueError(f'Не удалось построить объём стенки {side+1}')
    data = solid.to_mesh64()
    mesh = trimesh.Trimesh(vertices=data.vert_properties[:, :3],
                           faces=data.tri_verts, process=False)
    v = mesh.vertices.copy()
    radial = size/2-thickness+v[:, 2]
    ratio = radial/(size/2)
    x = v[:, 0]*ratio*1.001  # slight corner overlap avoids edge-only joints
    y = radial
    z = height+(v[:, 1]-height)*ratio
    for _ in range(side):
        x, y = -y, x
    mesh.vertices = np.column_stack([x, y, z])
    mesh.invert()
    if not mesh.is_volume:
        raise ValueError(f"Стенка {side+1}: сетка не образует замкнутый объём")
    return mesh


def export_printable(mesh, path):
    """Validate the STL round trip; simplify micrometre-scale edges only if needed."""
    if not mesh.is_volume or len(mesh.split()) != 1:
        raise ValueError('Исходная деталь не образует единый замкнутый объём')
    candidate = mesh
    for tolerance in (0, .0001, .001):
        if tolerance:
            solid = manifold3d.Manifold(manifold3d.Mesh64(
                np.asarray(mesh.vertices,dtype=np.float64),
                np.asarray(mesh.faces,dtype=np.uint64))).simplify(tolerance)
            if solid.status() != manifold3d.Error.NoError: continue
            data = solid.to_mesh64()
            candidate = trimesh.Trimesh(vertices=data.vert_properties[:,:3],faces=data.tri_verts,process=False)
        printable = trimesh.load_mesh(io.BytesIO(candidate.export(file_type='stl')), file_type='stl')
        printable.update_faces(printable.nondegenerate_faces())
        printable.update_faces(printable.unique_faces())
        printable.remove_unreferenced_vertices()
        if not printable.is_volume or len(printable.split()) != 1: continue
        payload = printable.export(file_type='stl')
        check = trimesh.load_mesh(io.BytesIO(payload), file_type='stl')
        if not check.is_volume or len(check.split()) != 1: continue
        Path(path).write_bytes(payload)
        return check
    raise ValueError('После преобразования в STL модель не образует единый замкнутый объём')


def projection(profiles_, size, height, thickness, span, diameter=0, pixels=600, lighting=None, mitered=False, box_x=0, box_y=0):
    coords = (np.arange(pixels)+0.5)/pixels*span-span/2
    x, y = np.meshgrid(coords-span*box_x/100, -coords+span*box_y/100)
    total = np.zeros_like(x)
    samples = 1 if diameter == 0 else 25
    for sample in range(samples):
        r = diameter/2*math.sqrt((sample+0.5)/samples)
        angle = sample*math.pi*(3-math.sqrt(5))
        sx, sy = r*math.cos(angle), r*math.sin(angle)
        blocked = (abs(x) <= size/2)&(abs(y) <= size/2)
        # Intersect actual ray-tapered mesh cross sections at five radii.
        for side, profile in enumerate(profiles_):
            tx, ty, ox, oy = x.copy(), y.copy(), sx, sy
            for _ in range(side):
                tx, ty, ox, oy = ty, -tx, oy, -ox
            for radial in np.linspace(size/2-thickness, size/2, 5):
                with np.errstate(divide='ignore', invalid='ignore'):
                    q = (radial-oy)/(ty-oy)
                    u = (ox+q*(tx-ox))*(size/2)/radial/1.001
                    z = height+(height*(1-q)-height)*(size/2)/radial
                valid = (q > 0)&(q < 1)&np.isfinite(u)&np.isfinite(z)
                if mitered: valid &= np.abs(u) <= size/2/1.001
                blocked |= valid & shapely.intersects_xy(profile, np.where(valid,u,0), np.where(valid,z,0))
        weight = 1 if lighting is None else illuminance(x-sx,y-sy,height,**lighting)
        total += (~blocked)*weight
    if lighting is not None:
        return total/samples
    return Image.fromarray(np.uint8(total/samples*255))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path, nargs='?')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--out', type=Path, default=Path('output/radial'))
    parser.add_argument('--size', type=float, default=100)
    parser.add_argument('--height', type=float, default=70, help='Emitter distance from wall')
    parser.add_argument('--span', type=float, default=600, help='Image width on wall')
    parser.add_argument('--box-x', type=float, default=0, help='Box center offset in image percent; positive right')
    parser.add_argument('--box-y', type=float, default=0, help='Box center offset in image percent; positive down')
    parser.add_argument('--thickness', type=float, default=2)
    parser.add_argument('--bridge', type=float, default=1.4)
    parser.add_argument('--source-diameter', type=float, default=2)
    parser.add_argument('--resolution', type=int, default=180)
    parser.add_argument('--smoothing', type=float, default=.8,
                        help='Contour smoothing in profile mm; 0 disables, default 0.8')
    parser.add_argument('--threshold', type=int, default=128)
    parser.add_argument('--invert', action='store_true')
    parser.add_argument('--preview-pixels', type=int, choices=(300, 600), default=600)
    parser.add_argument('--light-model', choices=('led','isotropic'), default='led')
    parser.add_argument('--beam-angle', type=float, default=120, help='Full half-intensity angle, degrees')
    parser.add_argument('--flux', type=float, default=100, help='Total luminous flux, lumens')
    parser.add_argument('--ambient', type=float, default=1, help='Uniform ambient illuminance, lux')
    parser.add_argument('--white-lux', type=float, default=100, help='Fixed preview white level, lux')
    parser.add_argument('--nozzle', type=float, default=.4)
    parser.add_argument('--min-feature', type=float, default=1.2, help='Minimum profile feature before ray taper, mm')
    args = parser.parse_args()
    if not (math.isfinite(args.smoothing) and 0 <= args.smoothing <= 2):
        parser.error('Smoothing must be between 0 and 2 mm')
    if not (.2 <= args.nozzle <= .8 and math.isfinite(args.min_feature) and 0.8 <= args.min_feature <= 5):
        parser.error('Invalid print settings')
    if args.thickness < 3*args.nozzle:
        parser.error('Wall thickness must be at least three nozzle diameters')
    if not (all(math.isfinite(v) for v in (args.beam_angle,args.flux,args.ambient,args.white_lux))
            and 10 <= args.beam_angle <= 170 and 1 <= args.flux <= 5000
            and 0 <= args.ambient <= 1000 and 1 <= args.white_lux <= 10000):
        parser.error('Invalid lighting parameters')
    if not all(math.isfinite(v) for v in (args.size,args.height,args.span,args.thickness,args.bridge,args.source_diameter)):
        parser.error('Dimensions must be finite')
    if not (60 <= args.size <= 300 and 40 <= args.height <= 400 and args.size*2 <= args.span <= args.size*15 and 1 <= args.thickness <= 5 and 0.8 <= args.bridge <= 5 and 0 <= args.source_diameter <= 10 and 40 <= args.resolution <= 300 and 0 <= args.threshold <= 255):
        parser.error('Invalid geometry or resolution')
    if not all(math.isfinite(v) and abs(v) <= 40 for v in (args.box_x,args.box_y)):
        parser.error('Box offsets must be between -40 and 40 percent')
    if max(abs(args.box_x),abs(args.box_y))/100*args.span + args.size/2 > args.span/2:
        parser.error('Box must fit inside the image; reduce offsets or box size')
    args.min_feature = max(args.min_feature,3*args.nozzle/(1-2*args.thickness/args.size))
    args.bridge = max(args.bridge,args.min_feature)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.demo:
        args.image = args.out/'input.png'
        demo(args.image)
    if not args.image:
        parser.error('Provide image or --demo')
    (args.out/'request.json').write_text(json.dumps(
        {k: str(v.resolve()) if isinstance(v, Path) else v for k,v in vars(args).items()},
        ensure_ascii=False, indent=2)+'\n')
    image = load_image(args.image, SimpleNamespace(resolution=600,invert=args.invert))
    image = image.point(lambda v: 0 if v < args.threshold else 255)
    print("STAGE:Расчёт четырёх стенок", flush=True)
    ps, counts = profiles(image,args.size,args.height,args.span,args.resolution,args.bridge,5,args.min_feature,args.smoothing,args.box_x,args.box_y)
    print("STAGE:Построение 3D-модели", flush=True)
    meshes = [side_mesh(p,i,args.size,args.height,args.thickness) for i,p in enumerate(ps)]
    meshes = [miter_wall(m,i,args.size,args.height) for i,m in enumerate(meshes)]
    # Bottom meets all tapered panels. Hollow post, with emitter 15 mm above it.
    base = solid_box([args.size,args.size,3],[0,0,1.5])
    post_height = args.height-15
    post = trimesh.creation.cylinder(5,post_height,sections=48)
    post.apply_translation([0,0,post_height/2])
    bore = trimesh.creation.cylinder(3,post_height+2,sections=48)
    bore.apply_translation([0,0,post_height/2])
    body = union([base,*meshes,post])
    # Feed the LED leads from underneath; the wire recess lies in the base's wall face.
    cable = solid_box([4,args.size/2+2,1.6],[0,-args.size/4,0.7])
    body = difference(body,[bore,cable])
    if not body.is_volume or len(body.split()) != 1:
        raise ValueError('Generated frame is not one connected solid')
    body = export_printable(body, args.out/'radial-box.stl')
    for i,(p,m) in enumerate(zip(ps,meshes)):
        m.export(args.out/f'side-{i+1}-assembled.stl')
        (args.out/f'side-{i+1}.wkt').write_text(p.wkt)
    print('STAGE:Подготовка деталей для Creality K1', flush=True)
    print_report = export_kit(body,meshes,args,args.out,export_printable)
    print("STAGE:Расчёт превью тени", flush=True)
    pixels = args.preview_pixels
    ideal = projection(ps,args.size,args.height,args.thickness,args.span,pixels=pixels,mitered=True,box_x=args.box_x,box_y=args.box_y)
    finite = projection(ps,args.size,args.height,args.thickness,args.span,args.source_diameter,pixels=pixels,mitered=True,box_x=args.box_x,box_y=args.box_y)
    print('STAGE:Расчёт освещённости стены', flush=True)
    lux = projection(ps,args.size,args.height,args.thickness,args.span,args.source_diameter,
                     pixels=pixels,mitered=True,box_x=args.box_x,box_y=args.box_y, lighting=dict(flux=args.flux,beam_angle=args.beam_angle,model=args.light_model))
    np.save(args.out/'illuminance-lux.npy',lux)
    lit = light_preview(lux,args.ambient,args.white_lux)
    lit.save(args.out/'projection-lighting.png')
    light_report = diagnostics(args.height,args.span,args.flux,args.beam_angle,args.light_model,args.ambient,box_x=args.box_x,box_y=args.box_y)
    ideal.save(args.out/'projection-point.png')
    finite.save(args.out/'projection-finite.png')
    sheet = Image.new('RGB',(2400,650),'#e9e5dd')
    d = ImageDraw.Draw(sheet)
    for i,(im,label) in enumerate([(image,'INPUT'),(ideal,'AROUND BOX / POINT SOURCE'),(finite,'AROUND BOX / FINITE SOURCE'),(lit,'LIGHTING / FIXED EXPOSURE')]):
        sheet.paste(im.resize((600,600)),(i*600,40))
        d.text((i*600+15,14),label,fill='black')
    sheet.save(args.out/'preview.png')
    mask = np.asarray(ideal)<128
    target = np.asarray(image.resize((pixels,pixels), Image.Resampling.NEAREST))<128
    coords = (np.arange(pixels)+.5)/pixels-.5
    x,y = np.meshgrid(coords-args.box_x/100,coords-args.box_y/100)
    outside = (abs(x)*args.span > args.size/2) | (abs(y)*args.span > args.size/2)
    report = {'mode':'radial side walls','settings':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},'bridges_per_side':counts,'watertight':bool(body.is_watertight),'components':len(body.split()),'volume_mm3':float(body.volume),'outside_box_binary_agreement':float(np.mean(mask[outside]==target[outside])),'coordinates':'wall z=0, emitter (0,0,height), observer on +Z, image up +Y','holder':'6 mm bore; emitter must extend 15 mm above post; adapt to actual LED','limitations':['box footprint occupies selected image area','automatic bridges and 5 mm base rail alter image','finite preview samples side walls, omits holder and room lighting','one-piece side walls may require printing supports','physical assembly and heat not tested']}
    report['printing'] = print_report
    report['lighting'] = light_report
    report['limitations'].append('Lighting assumes cos^n emission toward wall, no measured LED photometry, reflections, holder or heatsink shadows')
    (args.out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(report,indent=2,ensure_ascii=False))

if __name__ == '__main__':
    main()
