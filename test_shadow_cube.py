import tempfile
import unittest
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
import shapely
import trimesh
from shadow_cube import Settings, stencil, tapered_hole, generate, load_image, transmission


class GeometryTests(unittest.TestCase):
    def test_island_connected_and_aperture_direction(self):
        c = Settings(resolution=48)
        image = Image.new('L', (48, 48), 255)
        ImageDraw.Draw(image).ellipse((16, 16, 32, 32), fill=0)
        holes, bridges, _ = stencil(image, c)
        self.assertGreaterEqual(bridges, 1)
        # Single dark disk must block the central ray, while a corner is bright.
        self.assertFalse(shapely.contains_xy(holes, 0, 0))
        self.assertTrue(shapely.contains_xy(holes, 35, 35))
        poly = next(iter(holes.geoms)) if hasattr(holes, 'geoms') else holes
        cutter = tapered_hole(poly, c)
        self.assertTrue(cutter.is_volume)
        v = cutter.vertices
        # Every cutter boundary vertex projects onto the original aperture boundary.
        projected = np.column_stack((v[:, 0]*60/v[:, 1], (v[:, 2]-60)*60/v[:, 1]))
        from shapely.geometry import Point
        self.assertLess(max(poly.boundary.distance(Point(p)) for p in projected), 1e-6)

    def test_actual_stl_and_assembly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A controlled stencil fixture keeps this legacy geometry test
            # independent of the bundled detailed illustration.
            image = Image.new('L', (200,200), 255)
            ImageDraw.Draw(image).ellipse((50,40,150,160), fill=0)
            image.save(root/'example.png')
            report = generate(root/'example.png', root/'out', Settings(resolution=48))
            self.assertEqual(report['magnification'], 10)
            self.assertEqual(report['geometric_blur_diameter_mm'], 18)
            parts = []
            for name in ('body', 'panel', 'lid'):
                printed = trimesh.load_mesh(root/'out'/f'{name}.stl')
                self.assertTrue(printed.is_volume, name)
                self.assertEqual(len(printed.split()), 1, name)
                self.assertAlmostEqual(printed.bounds[0, 2], 0, places=5)
                parts.append(trimesh.load_mesh(root/'out'/f'{name}-assembled.stl'))
            for a, b in ((0, 1), (0, 2), (1, 2)):
                overlap = trimesh.boolean.intersection([parts[a], parts[b]], engine='manifold')
                self.assertLess(abs(overlap.volume), 1e-3)
            bounds = trimesh.util.concatenate(parts).bounds
            np.testing.assert_allclose(bounds, [[-60,-60,0],[60,60,120]], atol=1e-5)

    def test_image_orientation_alpha_and_aspect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'alpha.png'
            image = Image.new('RGBA', (80, 40), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((0, 0, 20, 20), fill=(0,0,0,255))
            image.save(path)
            c = Settings(resolution=80)
            loaded = load_image(path, c)
            self.assertEqual(loaded.getpixel((5, 25)), 0)
            self.assertEqual(loaded.getpixel((70, 25)), 255)
            holes, _, _ = stencil(loaded, c)
            self.assertFalse(shapely.contains_xy(holes, -45, 15))
            self.assertTrue(shapely.contains_xy(holes, 40, 15))

    def test_preview_fits_and_softens(self):
        c = Settings(resolution=48, source_diameter=10)
        image = Image.new('L', (48, 48), 255)
        ImageDraw.Draw(image).rectangle((12,12,36,36), fill=0)
        holes, _, _ = stencil(image, c)
        sharp, extent = transmission(holes, c, pixels=120)
        soft, _ = transmission(holes, c, samples=17, pixels=120)
        self.assertGreater(extent, c.size * 10)
        self.assertTrue(np.any((np.asarray(soft)>0) & (np.asarray(soft)<255)))
        self.assertFalse(np.any((np.asarray(sharp)>0) & (np.asarray(sharp)<255)))

    def test_invalid_and_blank(self):
        for c in [Settings(size=0), Settings(wall_distance=50), Settings(bridge=0),
                  Settings(size=float('nan')), Settings(source_diameter=-1)]:
            with self.assertRaises(ValueError):
                c.validate()
        with self.assertRaises(ValueError):
            stencil(Image.new('L', (100,100), 0), Settings())


if __name__ == '__main__':
    unittest.main()
