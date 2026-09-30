"""Phone Clear V8 R5 luminance allocation, the accepted Clear default.

No calibration filenames, coordinates, final JPEGs or reference-photo pixels
are used here. Color and RAW development retain the accepted V7 safeguards.
"""
from dataclasses import dataclass, replace, asdict
import numpy as np
from PIL import Image
from .color import DISPLAY_P3_TO_XYZ, SRGB_TO_XYZ
from .tone import luminance_rec2020
from .phone_tone import _smoothstep
from .phone_clear import (scene_decision as v7_scene_decision, local_exposure_field as baseline_local_exposure_field,
                          edge_limited_exposure, apply_exposure_field, smooth_exposure)

@dataclass(frozen=True)
class Recipe:
    meter_outdoor_evidence: bool = True
    hdr_separation_ev: float = 0.0
    sdr_separation_ev: float = 0.0
    subject_relation: float = 0.0
    material_strength: float = 0.0

RECIPE = Recipe(hdr_separation_ev=.55, material_strength=.15)


def outdoor_evidence(reference: np.ndarray, *, development_ev: float) -> dict:
    """Substantial vegetation/blue-sky color is contrary evidence to indoor tone.

    These are weak scene cues, not semantic certainty. Brightness statistics
    alone cannot distinguish a shaded garden from a dim room. Continuous
    support avoids a hard class switch near a threshold or small reframing.
    """
    from .color import linear_srgb_to_oklab, rec2020_to_linear_srgb
    raw = np.maximum(np.nan_to_num(reference, nan=0, posinf=0, neginf=0), 0)*2.0**-development_ev
    y = luminance_rec2020(raw)
    lab = linear_srgb_to_oklab(rec2020_to_linear_srgb(raw))
    relative = np.linalg.norm(lab[...,1:],axis=-1)/np.maximum(lab[...,0],.02)
    hue = np.degrees(np.arctan2(lab[...,2],lab[...,1])) % 360
    color = _smoothstep((relative-.045)/.045)
    green = _smoothstep((hue-95)/20)*(1-_smoothstep((hue-150)/25))*color*_smoothstep((y-.02)/.08)
    blue = _smoothstep((hue-205)/15)*(1-_smoothstep((hue-255)/20))*color*_smoothstep((y-.12)/.3)
    green_support = float(np.mean(green));blue_support=float(np.mean(blue))
    confidence = float(max(_smoothstep((green_support-.025)/.06),_smoothstep((blue_support-.025)/.06)))
    return {'green_support':green_support,'blue_support':blue_support,'outdoor_counterevidence':confidence}


def scene_decision(reference: np.ndarray, *, development_ev: float, peak_nits: float):
    decision = v7_scene_decision(reference, development_ev=development_ev,peak_nits=peak_nits)
    if not RECIPE.meter_outdoor_evidence:
        return decision
    evidence = outdoor_evidence(reference,development_ev=development_ev)
    indoor = decision.indoor_weight*(1-evidence['outdoor_counterevidence'])
    removed = decision.indoor_weight-indoor
    return replace(decision, indoor_weight=indoor,
        sdr_auto_ev=float(np.clip(decision.sdr_auto_ev+1.6*removed,-2,1)),
        hdr_midtone_gain=float(np.clip(decision.hdr_midtone_gain+.3*removed,1,2.5)),
        hdr_peak_ratio=float(np.clip(decision.hdr_peak_ratio+.9*removed,1.15,peak_nits/203)))


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
    budget_min, budget_max = 0., 0.
    with final_hdr.open('wb') as destination:
        for start in range(0, h, chunk_rows):
            stop = min(h, start+chunk_rows)
            a = np.asarray(sdr[start:stop], np.float32)
            b = np.array(hdr[start:stop, :, :3], np.float32)
            unadjusted_b = luminance_rec2020(b)
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
            if enabled and RECIPE.hdr_separation_ev > 0:
                before = luminance_rec2020(b)
                upper = 1+(peak_nits/203-1)*reference_options['hdr_strength']
                requested = redistribution_curve(before, upper=upper,
                    amount=RECIPE.hdr_separation_ev*reference_options['hdr_strength'],
                    dark_daylight=tone.get('phone_clear_daylit_dark_weight',0.),
                    indoor=tone['phone_indoor_weight'])
                # For this first allocation family, the stricter skin budget
                # applies to ALL pixels. Composition is measured against the
                # float image before any local field, not reset between stages.
                adjusted = np.minimum(requested,unadjusted_b*np.float32(2**.5))
                adjusted = np.maximum(adjusted,unadjusted_b*np.float32(2**-.5))
                b = compress_gamut(b*np.divide(adjusted,before,out=np.ones_like(before),where=before>1e-8)[...,None],
                                   target='rec2020',upper=peak_nits/203)
            actual_ev = np.log2(np.maximum(luminance_rec2020(b),1e-7)/np.maximum(unadjusted_b,1e-7))
            budget_min = min(budget_min,float(actual_ev.min()))
            budget_max = max(budget_max,float(actual_ev.max()))
            in_sdr = b @ REC2020_TO_DISPLAY_P3.T if gamut == 'display-p3' else rec2020_to_linear_srgb(b)
            maximum = max(maximum, float(np.max((np.maximum(in_sdr, 0)+1/64)/(a+1/64))))
            rgba = np.concatenate((b, np.ones((*b.shape[:2], 1), np.float32)), axis=-1)
            destination.write(rgba.astype('<f2').tobytes())
    del hdr
    record['experimental_recipe'] = asdict(RECIPE)
    record['composed_hdr_ev_min_max'] = [budget_min,budget_max]
    record['composed_budget_reference'] = 'float HDR before all local exposure and redistribution'
    record['redistribution'] = 'monotone diffuse-luminance curve; no spatial edge gates'
    return maximum, record


def redistribution_curve(y: np.ndarray, *, upper: float, amount: float,
                         dark_daylight: float, indoor: float) -> np.ndarray:
    """Monotone diffuse-light expansion, with deep shadows and peak fixed.

    Scene-adaptive thresholds express where reflected light becomes prominent.
    Ordinary middle tones stay below lit diffuse surfaces; very bright sources
    retain the existing finite peak. No spatial matte gates this curve, so a
    weak segmentation cannot create a contour or suppress the whole frame.
    """
    values = np.maximum(np.asarray(y,np.float32),0)
    if amount <= 0 or indoor >= 1:
        return values
    low = 2.0**((1-dark_daylight)*np.log2(.20)+dark_daylight*np.log2(.045))
    high = 2.0**((1-dark_daylight)*np.log2(1.0)+dark_daylight*np.log2(.24))
    position = np.log2(np.maximum(values,1e-7)/low)/np.log2(high/low)
    ev = amount*(1-indoor)*_smoothstep(position)
    return apply_exposure_field(values,ev,upper=upper)


def local_exposure_field(*args,**kwargs):
    if RECIPE.subject_relation>0 or RECIPE.material_strength>0:
        return _alternative_local_field(*args,**kwargs)
    return baseline_local_exposure_field(*args,**kwargs)

def _alternative_local_field(rgb: np.ndarray, *, person: Image.Image | None,
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
        relation = float(_smoothstep((context/max(median,.02)-1)/2))
        alternate_lift = lift + .35*(1-high_key)*relation
        alternate_trim = trim*(1-relation)
        request = (1-RECIPE.subject_relation)*(lift-trim) + RECIPE.subject_relation*(alternate_lift-alternate_trim)
        ev = float(np.clip(request, -.5, .5))*subject_strength*(1-dark)
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
    material = np.clip((.20+RECIPE.material_strength)*(log_y-base), -.30, .30)
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

