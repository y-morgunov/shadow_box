import math
import unittest
import numpy as np
from lighting import illuminance, preview, diagnostics
from radial_shadow import projection


class LightingTest(unittest.TestCase):
    def test_lambertian_axis_and_inverse_square(self):
        self.assertAlmostEqual(float(illuminance(0,0,1000)),100/math.pi)
        self.assertAlmostEqual(float(illuminance(0,0,2000)),25/math.pi)
        self.assertAlmostEqual(float(illuminance(1000,0,1000)),25/math.pi)

    def test_flux_normalization(self):
        # Integrate E over a large wall using r=h*tan(theta).
        theta = (np.arange(100000)+.5)/100000*(math.pi/2)
        r = np.tan(theta)
        for model, expected in [('led',100),('isotropic',50)]:
            e = illuminance(r*1000,0,1000,model=model)
            flux = np.sum(e*2*math.pi*r/(np.cos(theta)**2))*(math.pi/2)/len(theta)
            self.assertAlmostEqual(float(flux),expected,places=5)

    def test_sampled_light_and_occlusion(self):
        lux = projection([],100,120,2,1000,0,pixels=101,lighting={})
        self.assertEqual(lux[50,50],0)
        coord = (10+.5)/101*1000-500
        self.assertAlmostEqual(lux[10,50],float(illuminance(0,coord,120)))
        finite = projection([],100,120,2,1000,1,pixels=101,lighting={})
        self.assertTrue(np.isfinite(finite).all())
        self.assertLess(abs(finite[10,50]/lux[10,50]-1),.001)

    def test_fixed_exposure_and_ambient(self):
        low = np.asarray(preview(np.array([[1.,10.]]),0,100))
        high = np.asarray(preview(np.array([[10.,100.]]),0,100))
        self.assertTrue((low<high).all())
        self.assertGreater(np.asarray(preview(np.zeros((1,1)),1,100))[0,0],0)

    def test_compact_meter_warning(self):
        result = diagnostics(120,1000,100,120,'led',1)
        self.assertLess(result['corner_axis_ratio'],.001)
        self.assertGreater(len(result['warnings']),0)
        self.assertGreater(diagnostics(300,1000,100,120,'led',1)['corner_lux'],result['corner_lux'])
