"""Photometric approximation: axis along -Z, dimensions mm, flux lm, output lx."""
import math
import numpy as np
from PIL import Image


def illuminance(x, y, height, flux=100, beam_angle=120, model='led'):
    distance2 = (np.asarray(x)**2 + np.asarray(y)**2 + height**2) / 1e6
    cosine = height / (np.sqrt(distance2)*1000)
    if model == 'isotropic':
        intensity = flux / (4*math.pi)
    elif model == 'led':
        # Full angle between half-intensity directions, NOT a hard cutoff.
        exponent = math.log(.5) / math.log(math.cos(math.radians(beam_angle/2)))
        intensity = flux*(exponent+1)/(2*math.pi) * cosine**exponent
    else:
        raise ValueError('Unknown light model')
    return intensity * cosine / distance2


def preview(lux, ambient=1, white_lux=100):
    # Fixed exposure across designs, sRGB encoding; never normalize each image.
    linear = np.clip((lux+ambient)/white_lux, 0, 1)
    srgb = np.where(linear <= .0031308, 12.92*linear, 1.055*linear**(1/2.4)-.055)
    return Image.fromarray(np.uint8(np.rint(srgb*255)))


def diagnostics(height, span, flux, beam_angle, model, ambient, box_x=0, box_y=0):
    half = span/2
    far_x, far_y = half+abs(box_x)*span/100, half+abs(box_y)*span/100
    center, edge, corner = [float(illuminance(x,y,height,flux,beam_angle,model))
                            for x,y in [(0,0),(max(far_x,far_y),0),(far_x,far_y)]]
    warnings = []
    if corner/center < .05:
        warnings.append('Углы получают менее 5% осевой освещённости; поле сильно неравномерное.')
    if corner < max(1, 3*ambient):
        warnings.append('Слабое освещение углов: затемните комнату или увеличьте расстояние источника от стены.')
    if model == 'isotropic':
        warnings.append('Всесторонний источник — теоретическое сравнение, не обычный LED на плате.')
    return dict(axis_lux=center, edge_lux=edge, corner_lux=corner,
                corner_axis_ratio=corner/center,
                corner_angle_deg=math.degrees(math.atan(math.hypot(far_x,far_y)/height)),
                warnings=warnings)
