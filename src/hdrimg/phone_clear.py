"""Clear-specific, bounded tone fields. No filenames or calibration ROIs enter here.

The common exposure correction is smooth and extends through person boundaries.
Only extra texture contrast is attenuated at uncertain edges; setting the whole
subject gain to zero at its contour would itself create a dark ring.
"""
from dataclasses import replace

import numpy as np
from PIL import Image

from .color import DISPLAY_P3_TO_XYZ, SRGB_TO_XYZ
from .phone_tone import PhoneSceneDecision, _box_mean, _smoothstep, phone_scene_decision
from .tone import luminance_rec2020


def daylit_dark_weight(dark_weight: float, dark_fraction: float) -> float:
    """Separate a shadowed daytime scene from a frame dominated by night blacks."""
    return dark_weight * float(1-_smoothstep((dark_fraction-.05)/.10))


def compress_clear_illumination(luminance: np.ndarray, **kwargs) -> np.ndarray:
    """Reserve highlight detail instead of stacking strong daytime shoulders."""
    from .phone_tone import compress_dark_scene_illumination
    day = daylit_dark_weight(kwargs['dark_weight'], kwargs['dark_fraction'])
    kwargs['dark_weight'] *= 1-.65*day
    return compress_dark_scene_illumination(luminance, **kwargs)


def clear_display_curve(y: np.ndarray, daylit_dark: float) -> np.ndarray:
    """A slower SDR shoulder retains bright material texture in dark daylight."""
    from .tone import sdr_curve
    values = np.maximum(np.asarray(y, np.float32), 0)
    return (1-daylit_dark)*sdr_curve(values)+daylit_dark*values/(1+values)


def scene_decision(reference: np.ndarray, *, development_ev: float, peak_nits: float) -> PhoneSceneDecision:
    """Meter a fixed camera-WB reference, never the selected creative WB result."""
    decision = phone_scene_decision(reference, development_ev=development_ev,
                                    peak_nits=peak_nits, refine_diffuse=True)
    raw = np.maximum(np.nan_to_num(reference, nan=0, posinf=0, neginf=0), 0) * 2.0**-development_ev
    y = luminance_rec2020(raw)
    relative = np.ptp(raw, axis=-1) / np.maximum(np.max(raw, axis=-1), .01)
    # Soft, relative color support replaces an absolute RGB-spread threshold.
    neutral = float(np.mean(_smoothstep((y-.35)/.3) * (1-_smoothstep((relative-.2)/.4))))
    high = float(_smoothstep((decision.center_ratio-1.55)/.30)
                 * _smoothstep((neutral-.045)/.075)
                 * _smoothstep((decision.raw_p90-1)/.25))
    change = high-decision.high_key_weight
    return replace(decision, high_key_weight=high,
                   sdr_auto_ev=float(np.clip(decision.sdr_auto_ev+1.2*change, -2, 1)),
                   hdr_midtone_gain=float(np.clip(decision.hdr_midtone_gain+.4*change, 1, 2.5)),
                   hdr_peak_ratio=float(np.clip(decision.hdr_peak_ratio+change, 1.15, peak_nits/203)))


def smooth_exposure(field: np.ndarray, radius: int) -> np.ndarray:
    """Smooth the adjustment, not the photo. Convex averaging cannot overshoot."""
    result = np.asarray(field, np.float32)
    for _ in range(3):
        result = _box_mean(result, max(1, radius))
    return result.astype(np.float32)


def edge_limited_exposure(y: np.ndarray, requested: np.ndarray, *, slope: float = .35) -> tuple[np.ndarray, dict]:
    """Constrain gain gradients using the actual image, on both sides of edges.

    |delta EV| <= slope * |delta log2(Y)| preserves at least (1-slope)
    of the original log-luminance gradient under the bounded exposure curve.
    Flat connected areas receive a constant gain, rather than a painted ring.
    Min/max geodesic envelopes transport exposure over the image graph. Row
    and column scans use float64 cumulative distances to avoid cancellation.
    This runs on complete images, independently of encoder chunk boundaries.
    """
    guide = np.log2(np.maximum(np.asarray(y, np.float32), 1e-7))
    values = np.asarray(requested, np.float32)
    lower, upper = values.copy(), values.copy()
    maximum_change = 0.
    from .accelerator import envelopes
    accelerated = envelopes(guide, lower, upper, slope)
    if accelerated is not None:
        iteration, maximum_change = accelerated
    else:
        for iteration in range(512):
            maximum_change = 0.
            for axis in (1, 0):
                g = guide if axis == 1 else guide.T
                lo = lower if axis == 1 else lower.T
                hi = upper if axis == 1 else upper.T
                for start in range(0, len(g), 96):
                    stop = start+96
                    distances = np.empty(g[start:stop].shape, np.float64)
                    distances[:, 0] = 0
                    np.cumsum(slope*np.abs(np.diff(g[start:stop].astype(np.float64), axis=1)),
                              axis=1, out=distances[:, 1:])
                    a, b = lo[start:stop].astype(np.float64), hi[start:stop].astype(np.float64)
                    new_lo = np.minimum(
                        distances+np.minimum.accumulate(a-distances, axis=1),
                        -distances+np.minimum.accumulate((a+distances)[:, ::-1], axis=1)[:, ::-1])
                    new_hi = np.maximum(
                        -distances+np.maximum.accumulate(b+distances, axis=1),
                        distances+np.maximum.accumulate((b-distances)[:, ::-1], axis=1)[:, ::-1])
                    maximum_change = max(maximum_change, float(np.max(a-new_lo)), float(np.max(new_hi-b)))
                    lo[start:stop], hi[start:stop] = new_lo, new_hi
            if maximum_change < 2e-7:
                break
    result = (lower+upper)*.5
    del lower, upper
    # If a difficult graph has not converged, contract the *whole* residual
    # toward one constant gain. Never turn gain off along a subject contour.
    contraction = 1.
    for axis in (0, 1):
        difference = np.abs(np.diff(result, axis=axis))
        allowance = slope*np.abs(np.diff(guide, axis=axis))
        active = difference > 2e-7
        if np.any(active):
            contraction = min(contraction, float(np.min((allowance[active]+1e-8)/difference[active])))
    if contraction < 1:
        center = float(np.mean(result, dtype=np.float64))
        result = center+(result-center)*max(0., contraction*.999)
    return result.astype(np.float32), {
        'method': 'image-gradient-bounded geodesic exposure envelopes',
        'max_gain_gradient_ratio': slope, 'iterations': iteration+1,
        'last_envelope_change_ev': maximum_change,
        'residual_contraction': contraction,
        'requested_ev_range': [float(values.min()), float(values.max())],
        'applied_ev_range': [float(result.min()), float(result.max())],
    }


def raw_temperature_bias(automatic: np.ndarray, camera: np.ndarray,
                         decision: PhoneSceneDecision) -> tuple[float, dict]:
    """Bound native Auto temperature bias using warm outdoor-light evidence.

    RawTherapee applies TemperatureBias inside its Auto WB estimator. We do not
    need to invent an absolute Kelvin value or blend developed image colors.
    Camera WB is a fallible prior: indoor, dark and plain neutral scenes abstain.
    """
    a = np.maximum(np.nan_to_num(automatic, nan=0, posinf=0, neginf=0), 0)
    c = np.maximum(np.nan_to_num(camera, nan=0, posinf=0, neginf=0), 0)
    y = luminance_rec2020(c)
    relative = np.ptp(c, axis=-1)/np.maximum(np.max(c, axis=-1), 1e-5)
    valid = (np.min(a,axis=-1)>.001) & (np.min(c,axis=-1)>.001)
    lo, hi = np.percentile(y, [25,95])
    # Compare corresponding linear pixels, including colored materials. A
    # sandstone scene need not contain a large neutral patch to reveal the
    # difference between two RAW WB developments.
    support = valid & (y>lo) & (y<hi) & (relative<.85)
    warm = (c[...,0]>1.12*c[...,1]) & (c[...,1]>1.1*c[...,2]) & (y>lo)
    warm_fraction = float(np.mean(warm))
    normalized = c/np.maximum(y[...,None],1e-5)
    diversity = float(np.max(np.percentile(normalized[support],90,axis=0)-
                             np.percentile(normalized[support],10,axis=0))) if np.any(support) else 0.
    varied_materials = float(_smoothstep((diversity-.15)/.35))
    ambient_support = max(decision.high_key_weight, varied_materials,
                          float(1-_smoothstep((decision.raw_p50-.65)/.25)))
    # Dark daytime canyons differ from a night scene dominated by black areas.
    night = decision.dark_weight*float(_smoothstep((decision.dark_fraction-.05)/.15))
    confidence = ((1-decision.indoor_weight)*(1-night)
                  * float(_smoothstep((np.mean(support)-.02)/.08))
                  * ambient_support)
    shift = float(np.median(np.log2(c[...,0][support]/c[...,2][support])
                           - np.log2(a[...,0][support]/a[...,2][support]))) if np.any(support) else 0.
    bias = float(np.clip(.45*shift, 0, .20)*confidence)
    return bias, {'method':'bounded native Auto TemperatureBias from warm camera-WB prior',
                  'temperature_bias':bias, 'confidence':float(confidence),
                  'warm_fraction':warm_fraction, 'ambient_support':ambient_support,
                  'material_color_diversity':diversity, 'night_abstention_weight':night,
                  'neutral_support':float(np.mean(support)),
                  'camera_minus_auto_rb_ev':shift, 'absolute_kelvin':'not_exposed_by_engine_adapter',
                  'reason':'warm_daylight_evidence' if bias>0 else 'insufficient_warm_daylight_evidence'}


def refine_clear_color(rgb: np.ndarray, *, target: str, dark_weight: float,
                       indoor_weight: float, neutral_protection: bool = False,
                       skin_protection=None, person_protection=None) -> np.ndarray:
    """Retain RAW material chroma while preserving the validated hue correction.

    A faint warm rock, leaf or fabric is not evidence of a white object. RAW WB
    already corrected the illuminant. Reducing the legacy desaturation avoids
    turning those materials gray while keeping Natural's pale-skin safeguards.
    """
    from .color import (REC2020_TO_XYZ, DISPLAY_P3_TO_SRGB, SRGB_TO_DISPLAY_P3,
                        rec2020_to_linear_srgb, linear_srgb_to_rec2020,
                        linear_srgb_to_oklab, oklab_to_linear_srgb)
    from .phone_skin import refine_phone_skin_color
    corrected = refine_phone_skin_color(rgb, target=target, dark_weight=dark_weight,
        indoor_weight=indoor_weight, neutral_protection=neutral_protection,
        skin_protection=skin_protection, person_protection=person_protection)
    values = np.asarray(rgb, np.float32)
    to_srgb = (lambda x:x) if target == 'srgb' else (
        (lambda x:x@DISPLAY_P3_TO_SRGB.T) if target == 'display-p3' else rec2020_to_linear_srgb)
    from_srgb = (lambda x:x) if target == 'srgb' else (
        (lambda x:x@SRGB_TO_DISPLAY_P3.T) if target == 'display-p3' else linear_srgb_to_rec2020)
    original_lab, adjusted_lab = linear_srgb_to_oklab(to_srgb(values)), linear_srgb_to_oklab(to_srgb(corrected))
    before_c, after_c = [np.linalg.norm(lab[...,1:],axis=-1) for lab in (original_lab,adjusted_lab)]
    # Keep the existing warm hue correction; restoring saturation must not
    # rotate sandstone back toward magenta. Bound chroma removal independently.
    retained_c = np.maximum(after_c, .85*before_c)
    adjusted_lab[...,1:] *= np.divide(retained_c,after_c,out=np.ones_like(after_c),where=after_c>1e-8)[...,None]
    result = from_srgb(oklab_to_linear_srgb(adjusted_lab))
    coefficients = {'srgb': SRGB_TO_XYZ[1], 'display-p3': DISPLAY_P3_TO_XYZ[1],
                    'rec2020': REC2020_TO_XYZ[1]}[target]
    before, after = values@coefficients, result@coefficients
    result *= np.divide(before, after, out=np.ones_like(before), where=after>1e-8)[...,None]
    return result


def local_exposure_field(rgb: np.ndarray, *, person: Image.Image | None,
                         faces: list[dict], gamut: str, high_key: float,
                         indoor: float, dark: float, subject_strength: float,
                         local_strength: float, dynamic_range: float = 0.) -> tuple[Image.Image, dict]:
    """One low-frequency EV budget; no histogram/detail overshoot at contours.

    Faces supply reliable subject evidence. A broad Gaussian extends correction
    through the soft contour, without multiplying it by a narrow boundary band.
    Pointwise background contrast is a monotone curve, not unsharp masking.
    """
    rgb = np.maximum(np.asarray(rgb, np.float32), 0)
    h, w = rgb.shape[:2]
    y = rgb @ (DISPLAY_P3_TO_XYZ[1] if gamut == "display-p3" else SRGB_TO_XYZ[1])
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    xx /= w; yy /= h
    matte = np.zeros((h, w), np.float32) if person is None else np.asarray(
        person.resize((w, h), Image.Resampling.BILINEAR), np.float32)
    matte = np.clip(np.nan_to_num(matte), 0, 1)
    subject = np.zeros_like(y)
    head = np.zeros_like(y)
    body = np.zeros_like(y)
    records = []
    for face in faces:
        vals = [face.get(k, 0) for k in ('x', 'y', 'width', 'height', 'confidence')]
        if not np.all(np.isfinite(vals)):
            continue
        x, top, bw, bh, confidence = vals
        if confidence < .55 or min(bw, bh) <= 0 or min(x, top) < 0 or x+bw > 1.001 or top+bh > 1.001:
            continue
        xs = slice(round((x+.2*bw)*w), round((x+.8*bw)*w))
        ys = slice(round((top+.2*bh)*h), round((top+.9*bh)*h))
        area = y[ys, xs]
        if area.size < 4 or np.mean(matte[ys, xs]) < .3:
            continue
        median = float(np.median(area))
        # Correct exposure error relative to surrounding light, with only a
        # bounded preference for brighter backlit faces. Never normalize skin.
        ring = ((xx-x-bw/2)**2/(bw*2)**2 + (yy-top-bh/2)**2/(bh*2)**2) < 1
        surrounding = y[ring & (matte < .2)]
        context = float(np.median(surrounding)) if surrounding.size >= 16 else median
        lift = .5*high_key*_smoothstep((context/ max(median,.02)-1)/2)
        trim = .5*_smoothstep((median-.25)/.25)*(1-.6*high_key)
        ev = float(np.clip(lift-trim, -.5, .5))*subject_strength*(1-dark)
        weight = np.exp(-.5*(((xx-x-bw/2)/bw)**2 + ((yy-top-.5*bh)/(1.3*bh))**2))
        # An overlap average prevents duplicated detections adding exposure.
        subject += weight*ev
        head += weight
        # Broad body envelope is anchored by the face, not the silhouette.
        # A slightly shifted matte must never drag a bright/dark outline.
        body = np.maximum(body, np.exp(-.5*(
            ((xx-x-bw/2)/(2.5*bw))**4 + ((yy-top-3*bh)/(5*bh))**4)))
        records.append({'reference_face_y': median, 'surrounding_y': context, 'ev': ev})
    subject = np.divide(subject, np.maximum(head, 1), out=np.zeros_like(subject))
    # The matte confirms the face only. It does not stencil exposure gains.
    radius = max(1, round(min(h, w)*.008))
    subject = smooth_exposure(subject, radius)
    garment = np.zeros_like(y)
    garment_confidence = 0.
    if records and high_key > 0 and subject_strength > 0:
        from .color import DISPLAY_P3_TO_SRGB, linear_srgb_to_oklab
        lab = linear_srgb_to_oklab(rgb @ DISPLAY_P3_TO_SRGB.T if gamut == 'display-p3' else rgb)
        relative = np.linalg.norm(lab[...,1:], axis=-1)/np.maximum(lab[...,0], .02)
        neutral = 1-_smoothstep((relative-.035)/.055)
        light = _smoothstep((y-.06)/.12)
        # Confirm a substantial light garment inside the reliable person area.
        # A few wrongly included background pixels cannot turn this on.
        inside = (matte > .8) & (head < .2)
        supported = inside & (y > .15) & (relative < .06)
        fraction = np.count_nonzero(supported)/max(np.count_nonzero(inside), 1)
        garment_confidence = float(_smoothstep((fraction-.15)/.25))
        # Reliable body support and a smooth color/light gate; no hard cutout.
        garment = body*(1-np.clip(head*2,0,1))*neutral*light*high_key*subject_strength*garment_confidence
        garment = smooth_exposure(garment, max(2, round(min(h,w)*.012)))
    # Darken indoor shadows gently, without expanding existing edge shadows.
    # This field is pointwise monotone after applying the odds transform.
    background = -.25*indoor*(1-dark)*(1-_smoothstep((y-.06)/.35))*local_strength
    if records:
        background -= .35*high_key*(1-body)*local_strength
    background *= 1-np.clip(head, 0, 1)
    background = smooth_exposure(background, radius)
    # Broad sun/shade compression is a smooth luminance curve. It restores
    # dark foliage without making illuminated rocks the same brightness.
    outdoor_contrast = float(_smoothstep((dynamic_range-3)/2))*(1-dark)*(1-indoor)
    background += outdoor_contrast*local_strength*(
        .35*(1-_smoothstep((y-.025)/.12))-.45*_smoothstep((y-.25)/.30))
    from .phone_tone import guided_log_luminance_base
    log_y = np.log2(np.maximum(y, 1e-4))
    base = guided_log_luminance_base(y, radius_fraction=.025, epsilon=.20)
    material = np.clip(.20*(log_y-base), -.30, .30)
    material *= local_strength*(1-dark)*(1-.85*np.clip(head,0,1))*_smoothstep((y-.015)/.045)
    # Uncertain person contours receive less extra texture, while their broad
    # subject exposure remains intact. The image-gradient constraint follows.
    material *= 1-.65*4*matte*(1-matte)
    total = np.clip(subject + garment + np.clip(background, -.5, .5) + material, -1, 1).astype(np.float32)
    # Foreground skin/face must keep its smaller exposure budget, even when a
    # pale pixel also qualifies as near-neutral clothing.
    if records:
        from .color import DISPLAY_P3_TO_SRGB, linear_srgb_to_oklab
        lab = linear_srgb_to_oklab(rgb @ DISPLAY_P3_TO_SRGB.T if gamut == 'display-p3' else rgb)
        warm_skin = _smoothstep((lab[...,1]/np.maximum(lab[...,0],.02)-.002)/.012)
        limit = 1-.5*np.maximum(warm_skin*body,np.clip(head,0,1))
        total = np.minimum(total, smooth_exposure(limit, radius))
    # Conservative first-round semantic budget also survives gain transport.
    total = np.clip(total, -.5, .5)
    total, boundary = edge_limited_exposure(y, total)
    return Image.fromarray(total), {
        'method': 'single exposure budget constrained by real luminance edges',
        'face_count': len(records), 'faces': records,
        'subject_status': 'applied' if records else 'no_reliable_subject',
        'ev_min': float(total.min()), 'ev_max': float(total.max()),
        'subject_ev_limit': .5, 'background_ev_limit': .5, 'combined_ev_limit': 1.,
        'garment_ev_limit':1., 'garment_support_mean':float(np.mean(garment)),
        'garment_confidence':garment_confidence,
        'boundary_policy': 'broad subject gain continues through contour; no repainting',
        'texture_enhancement': 'bounded EV request under the shared image-gradient constraint',
        'texture_ev_range': [float(material.min()),float(material.max())],
        'outdoor_contrast_weight': outdoor_contrast,
        'image_edge_constraint': boundary,
    }


def apply_exposure_field(y: np.ndarray, ev: np.ndarray, *, upper: float) -> np.ndarray:
    """Monotone bounded exposure curve: black and peak stay fixed."""
    y = np.clip(y, 0, upper)
    gain = np.exp2(np.clip(ev, -1, 1))
    return y*gain/(1+(y/upper)*(gain-1))


def finish_float_render(*, scene, sdr, hdr_path, final_hdr, work, chunk_rows, gamut,
                        peak_nits, tone, person_context, local_strength,
                        subject_strength, enabled, reference_options):
    """Adjust float SDR/HDR once, then write the encoder's half-float input."""
    import tifffile
    from .color import REC2020_TO_DISPLAY_P3, rec2020_to_linear_srgb, compress_gamut
    from .phone_subject import detect_subject_fields
    from .render import render_pair

    h, w = sdr.shape[:2]
    field = None
    record = {'status': 'disabled_or_reference'}
    if enabled and (local_strength > 0 or subject_strength > 0):
        scale = min(1., 1024/max(h, w))
        size = (max(1, round(w*scale)), max(1, round(h*scale)))
        preview = np.stack([np.asarray(Image.fromarray(scene[..., c]).resize(
            size, Image.Resampling.BOX)) for c in range(3)], axis=-1)
        source = work/'reference-scene.tif'
        tifffile.imwrite(source, preview, photometric='rgb')
        reference, linear = work/'reference.jpg', work/'reference-linear.tif'
        render_pair(source, reference, work/'reference.rgba16f',
                    sdr_exposure_ev=0, subject_adaptation_strength=0,
                    _allow_subject=False, _allow_histogram=False,
                    _linear_sdr_output=linear, **reference_options)
        detection = {'status': 'disabled', 'faces': []}
        if subject_strength > 0 and tone['phone_dark_weight'] < .5:
            _, _, detection = detect_subject_fields(reference,
                high_key_weight=tone['phone_high_key_weight'], strength=1,
                gamut=gamut, indoor_weight=tone['phone_indoor_weight'])
        field, record = local_exposure_field(tifffile.imread(linear),
            person=person_context[0] if person_context else None,
            faces=detection.get('faces', []), gamut=gamut,
            high_key=tone['phone_high_key_weight'], indoor=tone['phone_indoor_weight'],
            dark=tone['phone_dark_weight'], subject_strength=subject_strength,
            local_strength=local_strength,
            dynamic_range=float(np.log2(max(tone['phone_raw_p90'],1e-5)/max(tone['phone_raw_p10'],1e-5))))
        record['detection'] = detection
        record['reference'] = 'float32, independent of SDR-only exposure'
    hdr = np.memmap(hdr_path, dtype='<f4', mode='r', shape=(h, w, 4))
    coefficients = DISPLAY_P3_TO_XYZ[1] if gamut == 'display-p3' else SRGB_TO_XYZ[1]
    sdr_field = hdr_field = None
    if field is not None:
        requested = np.asarray(field.resize((w, h), Image.Resampling.BILINEAR), np.float32)
        # Reapply the constraint at native resolution; interpolating a safe
        # preview field alone would not protect small hairs or narrow shadows.
        sdr_field, sdr_boundary = edge_limited_exposure(sdr @ coefficients, requested)
        hdr_field, hdr_boundary = edge_limited_exposure(luminance_rec2020(hdr[..., :3]), requested)
        record['native_sdr_edge_constraint'] = sdr_boundary
        record['native_hdr_edge_constraint'] = hdr_boundary
        del requested
    maximum = 1.
    with final_hdr.open('wb') as destination:
        for start in range(0, h, chunk_rows):
            stop = min(h, start+chunk_rows)
            a = np.asarray(sdr[start:stop], np.float32)
            b = np.array(hdr[start:stop, :, :3], np.float32)
            if field is not None:
                ay, by = a @ coefficients, luminance_rec2020(b)
                new_a = apply_exposure_field(ay, sdr_field[start:stop], upper=1)
                hdr_upper = 1+(peak_nits/203-1)*reference_options['hdr_strength']
                new_b = apply_exposure_field(by, hdr_field[start:stop], upper=hdr_upper)
                a = compress_gamut(a*np.divide(new_a, ay, out=np.ones_like(ay), where=ay>1e-8)[..., None],
                                   target=gamut, upper=1)
                b = compress_gamut(b*np.divide(new_b, by, out=np.ones_like(by), where=by>1e-8)[..., None],
                                   target='rec2020', upper=peak_nits/203)
                sdr[start:stop] = a
            in_sdr = b @ REC2020_TO_DISPLAY_P3.T if gamut == 'display-p3' else rec2020_to_linear_srgb(b)
            maximum = max(maximum, float(np.max((np.maximum(in_sdr, 0)+1/64)/(a+1/64))))
            rgba = np.concatenate((b, np.ones((*b.shape[:2], 1), np.float32)), axis=-1)
            destination.write(rgba.astype('<f2').tobytes())
    del hdr
    return maximum, record
