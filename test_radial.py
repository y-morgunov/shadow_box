import unittest
import io
import tempfile
from pathlib import Path
import trimesh
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box
from radial_shadow import profiles, side_mesh, projection, export_printable, smooth_profile

class RadialTest(unittest.TestCase):
    def test_offset_moves_box_and_covers_far_image_edge(self):
        for bx,by in [(15,20),(-15,-20)]:
            white = Image.new('L',(200,200),255)
            ps,_ = profiles(white,100,70,600,100,1.4,5,box_x=bx,box_y=by)
            result = np.asarray(projection(ps,100,70,2,600,pixels=200,box_x=bx,box_y=by))
            self.assertEqual(result[round(100+by*2),round(100+bx*2)],0)
            self.assertEqual(result[100,100],255)
            black = Image.new('L',(200,200),0)
            ps,_ = profiles(black,100,70,600,180,1.4,5,box_x=bx,box_y=by)
            result = np.asarray(projection(ps,100,70,2,600,pixels=200,box_x=bx,box_y=by))
            # The far side needs a taller panel than a centered image does.
            self.assertGreater(np.mean(result[5:-5,5:-5] == 0),.98)
            for side,p in enumerate(ps):
                self.assertTrue(side_mesh(p,side,100,70,2).is_volume)

    def test_smoothing_keeps_circle_curved(self):
        from shapely.geometry import Point
        circle = Point(0,0).buffer(20,quad_segs=256)
        smooth = smooth_profile(circle,.6)
        v = np.asarray(smooth.exterior.coords)
        edges = np.diff(v,axis=0)
        edges /= np.linalg.norm(edges,axis=1)[:,None]
        turns = np.degrees(np.arccos(np.clip(
            np.sum(edges*np.roll(edges,1,axis=0),axis=1),-1,1)))
        # The previous RDP smoothing turned this circle into a polygon, with
        # ~20-degree corners. Dense, smooth arcs must survive the STL contour.
        self.assertLess(turns.max(),5)
        self.assertLess(np.max(np.abs(np.linalg.norm(v,axis=1)-20)),.05)

    def test_smoothing_removes_stairs_without_losing_large_hole(self):
        from shapely.ops import unary_union
        stairs = unary_union([box(i*.5,0,(i+1)*.5,5+i*.5) for i in range(40)])
        stairs = stairs.difference(box(3,2,6,5))
        smooth = smooth_profile(stairs,.4)
        self.assertTrue(smooth.is_valid)
        self.assertEqual(len(smooth.interiors),1)
        self.assertLess(smooth.length,stairs.length*.95)
        self.assertLess(smooth.symmetric_difference(stairs).area,stairs.area*.03)
        self.assertTrue(smooth_profile(stairs,0).equals_exact(stairs,0))
        self.assertTrue(side_mesh(smooth,0,100,70,2).is_volume)

    def test_thickness_follows_rays(self):
        for side in range(4):
            mesh=side_mesh(box(-20,5,25,40),side,100,70,2)
            self.assertTrue(mesh.is_volume)
            vertices=mesh.vertices
            k=70/(70-vertices[:,2])
            projected=vertices[:,:2]*k[:,None]
            unique=np.unique(np.round(projected,6),axis=0)
            self.assertEqual(len(unique),4)

    def test_disconnected_shadow_is_attached(self):
        image=Image.new('L',(200,200),255)
        ImageDraw.Draw(image).ellipse((80,10,120,35),fill=0)
        ps,counts=profiles(image,100,70,600,60,1.4,5)
        self.assertGreater(sum(counts),0)
        for p in ps:
            self.assertEqual(p.geom_type,'Polygon')

    def test_bridges_can_be_disabled_and_fragments_exported(self):
        image = Image.new('L', (200, 200), 255)
        ImageDraw.Draw(image).ellipse((80, 10, 120, 35), fill=0)
        ps, counts = profiles(image, 100, 70, 600, 100, 1.4, 5, supports=False)
        self.assertEqual(counts, [0, 0, 0, 0])
        detached = next(p for p in ps if p.geom_type == 'MultiPolygon')
        mesh = side_mesh(detached, 0, 100, 70, 2)
        components = len(mesh.split())
        self.assertGreater(components, 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'fragments.stl'
            with self.assertRaises(ValueError):
                export_printable(mesh, path)
            checked = export_printable(mesh, path, require_connected=False)
            self.assertTrue(checked.is_volume)
            self.assertEqual(len(checked.split()), components)

    def test_astronaut_retains_sky_details(self):
        from shadow_cube import load_image
        from types import SimpleNamespace
        from parameter_specs import CONTROL_SPECS
        defaults = {s[0]: s[4] for s in CONTROL_SPECS}
        image_path = Path(__file__).parent/'examples/astronaut/input.png'
        scores = []
        for resolution, smoothing, feature, pixels in [(180, .8, 1.25, 600),
                (defaults['resolution'], defaults['smoothing'], defaults['min_feature'], 720)]:
            image = load_image(image_path, SimpleNamespace(resolution=pixels, invert=False))
            image = image.point(lambda v: 0 if v < 128 else 255)
            ps, _ = profiles(image, 100, 70, 600, resolution, 1.4, 5,
                             feature, smoothing, box_y=14)
            actual = np.asarray(projection(ps, 100, 70, 2, 600,
                                pixels=600, mitered=True, box_y=14)) < 128
            target = np.asarray(image.resize((600, 600), Image.Resampling.NEAREST)) < 128
            # Sky above the astronaut includes stars, the moon and planet rings.
            scores.append(np.mean(actual[:200] == target[:200]))
        self.assertGreater(scores[1], .93)
        self.assertGreater(scores[1] - scores[0], .03)

    def test_detailed_profiles_form_volumes(self):
        # Seed 0, side 0 used to produce an open Earcut extrusion despite a
        # valid Shapely polygon. Check all sides and their assembled STL.
        from shadow_cube import solid_box, union
        image = Image.fromarray(np.uint8(np.random.default_rng(0).random((24,24)) > .45)*255)
        ps, _ = profiles(image,100,70,600,80,1.4,5)
        walls = [side_mesh(p,i,100,70,2) for i,p in enumerate(ps)]
        for wall in walls:
            self.assertTrue(wall.is_volume)
        body = union([solid_box([100,100,3],[0,0,1.5]), *walls])
        self.assertTrue(body.is_volume)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'result.stl'
            export_printable(body, path)
            loaded = trimesh.load_mesh(path)
        self.assertTrue(loaded.is_volume)
        self.assertEqual(len(loaded.split()),1)

    def test_hole_is_preserved(self):
        profile = box(-20,5,20,40).difference(box(-5,15,5,25))
        wall = side_mesh(profile,0,100,70,2)
        # Project every mesh vertex back to the outer reference plane.
        v = wall.vertices
        coordinates = np.column_stack((v[:,0]/(v[:,1]/50)/1.001,
                                        70+(v[:,2]-70)/(v[:,1]/50)))
        self.assertTrue(np.any(np.all(np.isclose(coordinates,[-5,15]),axis=1)))
        # Exact volume of a radially tapered extrusion, including the hole.
        factor = ((50**3-48**3)/3)/(50**2)*1.001
        self.assertAlmostEqual(wall.volume,profile.area*factor,places=4)

    def test_empty_input_only_shadows_center(self):
        image=Image.new('L',(200,200),255)
        ps,_=profiles(image,100,70,600,60,1.4,5)
        projected=np.asarray(projection(ps,100,70,2,600,pixels=200))
        self.assertEqual(projected[100,100],0)
        self.assertEqual(projected[20,100],255)
        self.assertGreater(np.mean(projected==255),.95)

if __name__=='__main__': unittest.main()
