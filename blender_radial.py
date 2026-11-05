"""blender -b -t 4 --python blender_radial.py -- examples/astronaut --render"""
import sys,json,math
from pathlib import Path
import bpy
from mathutils import Vector
args=sys.argv[sys.argv.index('--')+1:]
root=Path(args[0]).resolve()
c=json.loads((root/'report.json').read_text())['settings']
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
s=bpy.context.scene
s.unit_settings.system='METRIC'
s.unit_settings.length_unit='MILLIMETERS'
def mat(name,color):
    m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
    m.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value=(*color,1)
    m.node_tree.nodes['Principled BSDF'].inputs['Roughness'].default_value=.8
    return m
bpy.ops.wm.stl_import(filepath=str(root/'radial-box.stl'))
body=bpy.context.object;body.name='Four shaped side walls and LED post';body.scale=(.001,)*3
body.data.materials.append(mat('Opaque dark plastic',(.018,.023,.03)))
bpy.ops.mesh.primitive_plane_add(size=c['span']/1000*2,location=(0,0,-.0002))
bpy.context.object.name='Wall';bpy.context.object.data.materials.append(mat('Matte wall',(.7,.7,.7)))
bpy.ops.object.light_add(type='POINT',location=(0,0,c['height']/1000))
light=bpy.context.object;light.data.energy=3;light.data.shadow_soft_size=c['source_diameter']/2000
bpy.ops.object.camera_add(location=(0,0,1))
camera=bpy.context.object;camera.rotation_euler=(0,0,0);camera.data.type='ORTHO';camera.data.ortho_scale=c['span']/1000
s.camera=camera;s.render.engine='CYCLES';s.cycles.samples=24;s.cycles.use_denoising=True
s.world.use_nodes=True;s.world.node_tree.nodes['Background'].inputs[1].default_value=.008
s.render.resolution_x=720;s.render.resolution_y=720;s.render.resolution_percentage=100
s.render.image_settings.file_format='PNG';s.view_settings.view_transform='AgX'
s.render.filepath=str(root/'blender-projection.png')
bpy.ops.wm.save_as_mainfile(filepath=str(root/'radial-shadow.blend'))
if '--render' in args:
    bpy.ops.render.render(write_still=True)
    camera.location=(.23,-.28,.26)
    camera.rotation_euler=(Vector((0,0,.03))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.ortho_scale=.24
    light.data.energy=.2
    bpy.ops.object.light_add(type='AREA',location=(.1,-.1,.3))
    bpy.context.object.data.energy=12;bpy.context.object.data.size=.3
    s.world.node_tree.nodes['Background'].inputs[1].default_value=.3
    s.render.filepath=str(root/'construction.png')
    bpy.ops.render.render(write_still=True)
