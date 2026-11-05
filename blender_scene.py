"""blender -b -t 4 --python blender_scene.py -- examples/cat [--render]"""
import bpy
import json
import sys
from pathlib import Path
from mathutils import Vector

args = sys.argv[sys.argv.index('--')+1:]
root = Path(args[0]).resolve()
c = json.loads((root/'report.json').read_text())['settings']
s = c['size']/1000
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.length_unit = 'MILLIMETERS'


def material(name, color):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = (*color, 1)
    node.inputs['Roughness'].default_value = 0.7
    return mat


black = material('Opaque graphite plastic', (0.025, 0.03, 0.04))
white = material('Matte wall', (0.75, 0.72, 0.67))
for name in ('body', 'panel', 'lid'):
    bpy.ops.wm.stl_import(filepath=str(root/f'{name}-assembled.stl'))
    obj = bpy.context.object
    obj.name = name
    obj.scale = (0.001,)*3
    obj.data.materials.append(black)

# The real source is at the cube centre. No decorative light sphere occluder.
bpy.ops.object.light_add(type='POINT', location=(0, 0, s/2))
light = bpy.context.object
light.name = 'Central LED (replace diameter with measured emitting area)'
light.data.energy = 2
light.data.shadow_soft_size = c['source_diameter']/2000
light.data.color = (1, 0.86, 0.68)
distance = c['wall_distance']/1000
window = (c['size']-2*c['border'])/1000 * distance/(s/2)
# wall is perpendicular to +Y
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, distance+0.01, s/2))
wall = bpy.context.object
wall.name = 'Projection wall'
wall.scale = (window*1.5, 0.02, window*1.5)
wall.data.materials.append(white)

bpy.ops.object.camera_add(location=(window*0.10, -distance*0.6, s/2+window*0.10))
camera = bpy.context.object
camera.rotation_euler = (Vector((0, distance, s/2))-camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.type = 'ORTHO'
camera.data.ortho_scale = window*1.35
scene.camera = camera
scene.render.engine = 'CYCLES'
scene.cycles.samples = 48
scene.cycles.use_denoising = True
scene.world.use_nodes = True
scene.world.node_tree.nodes['Background'].inputs[0].default_value = (0.015, 0.015, 0.015, 1)
scene.world.node_tree.nodes['Background'].inputs[1].default_value = 0.05
scene.render.resolution_x = 900
scene.render.resolution_y = 900
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.filepath = str(root/'blender-projection.png')
scene.view_settings.view_transform = 'AgX'
scene.view_settings.exposure = 1
bpy.ops.wm.save_as_mainfile(filepath=str(root/'shadow-cube.blend'))
if '--render' in args or '--assembly' in args:
    if '--render' in args:
        bpy.ops.render.render(write_still=True)
    scene.cycles.samples = 16
    scene.render.resolution_x = 720
    scene.render.resolution_y = 720
    # Separate explanatory exploded view; optical .blend above stays assembled.
    wall.hide_render = True
    light.hide_render = True
    bpy.data.objects['lid'].location.z += s*0.45
    bpy.data.objects['panel'].location.y += s*0.30
    camera.location = (s*2.1, s*3.1, s*2.4)
    camera.rotation_euler = (Vector((0, s*0.1, s*0.65))-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.ortho_scale = s*2.5
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (0.3, 0.3, 0.3, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = 0.5
    for location, energy in [((s*2, s*3, s*4), 35), ((-s*2, 0, s*2), 15)]:
        bpy.ops.object.light_add(type='AREA', location=location)
        obj = bpy.context.object
        obj.data.energy = energy
        obj.data.shape = 'DISK'
        obj.data.size = s*3
        obj.rotation_euler = (Vector((0, 0, s/2))-obj.location).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = str(root/'assembly.png')
    bpy.ops.render.render(write_still=True)
