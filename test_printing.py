import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image, ImageDraw
import trimesh
from radial_shadow import profiles, side_mesh, export_printable
from printing import split_parts, oriented, export_kit, miter_wall
from shadow_cube import solid_box, union, difference


class PrintingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image=Image.new('L',(120,120),255)
        d=ImageDraw.Draw(image);d.ellipse((10,10,110,110),fill=0);d.ellipse((35,35,85,85),fill=255)
        cls.ps,_=profiles(image,100,120,1000,60,1.4,5,1.25)
        cls.walls=[miter_wall(side_mesh(p,i,100,120,2),i,100,120) for i,p in enumerate(cls.ps)]
        post=trimesh.creation.cylinder(5,105,sections=48);post.apply_translation([0,0,52.5])
        bore=trimesh.creation.cylinder(3,110,sections=48);bore.apply_translation([0,0,52.5])
        cls.body=difference(union([solid_box([100,100,3],[0,0,1.5]),post,*cls.walls]),[bore])

    def test_parts_reconstruct_body_without_overlap(self):
        parts=split_parts(self.body,self.walls,100,120,2)
        self.assertEqual(len(parts),6)
        restored=[]
        for name,part,side in parts:
            self.assertTrue(part.is_volume,name)
            self.assertEqual(len(part.split()),1,name)
            laid,t=oriented(part,side)
            self.assertAlmostEqual(laid.bounds[0,2],0,places=5)
            if name == 'post': self.assertTrue(np.allclose(laid.extents[:2],[10,10]))
            if side is not None: self.assertLessEqual(laid.extents[2],2.001)
            laid.apply_transform(np.linalg.inv(t));restored.append(laid)
        assembly=union(restored)
        self.assertLess(abs(sum(p.volume for p in restored)-self.body.volume),.02)
        delta=trimesh.boolean.difference([self.body,assembly],engine='manifold')
        self.assertLess(abs(delta.volume),.02)

    def test_export_and_printer_limits(self):
        args=SimpleNamespace(size=100,height=120,thickness=2,nozzle=.4,min_feature=1.25)
        with tempfile.TemporaryDirectory() as directory:
            data=export_kit(self.body,self.walls,args,Path(directory),export_printable)
            self.assertTrue(data['all_parts_fit'])
            self.assertTrue((Path(directory)/'print-k1.zip').exists())
            for rec in data['parts']:
                mesh=trimesh.load_mesh(Path(directory)/'print-k1'/rec['file'])
                self.assertTrue(mesh.is_volume)
            # Increase only the post above z=3, leaving the assembly joints intact.
            tall_post=trimesh.creation.cylinder(5,300,sections=48); tall_post.apply_translation([0,0,150])
            tall_bore=trimesh.creation.cylinder(3,310,sections=48); tall_bore.apply_translation([0,0,150])
            body=difference(union([self.body,tall_post]),[tall_bore])
            args.height=315
            data=export_kit(body,self.walls,args,Path(directory),export_printable)
            self.assertFalse(data['all_parts_fit'])

    def test_sub_nozzle_sliver_removed(self):
        image=Image.new('L',(600,600),255)
        ImageDraw.Draw(image).rectangle((299,0,300,220),fill=0)
        ps,_=profiles(image,100,120,1000,180,1.4,5,1.25)
        # Only the continuous bottom rail remains, without a hair-thin tower.
        self.assertTrue(all(p.bounds[3] <= 5.01 for p in ps))
