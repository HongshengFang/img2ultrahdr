from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image
import tifffile

from hdrimg.phone_clear import apply_exposure_field, edge_limited_exposure, local_exposure_field, scene_decision, smooth_exposure, raw_temperature_bias, refine_clear_color
from hdrimg.style import PHONE_CLEAR_CANDIDATE, STYLE_PRESETS
from hdrimg.render import render_pair


def test_daylight_shoulder_preserves_texture_without_changing_night_curve():
    from hdrimg.phone_clear import daylit_dark_weight, compress_clear_illumination, clear_display_curve
    from hdrimg.phone_tone import compress_dark_scene_illumination
    from hdrimg.tone import sdr_curve
    y = np.geomspace(.0001, 64, 10001, dtype=np.float32)
    kwargs = dict(base_log=np.log2(y), exposure_ev=-1.4, dark_weight=1.,
                  dark_fraction=.01, highlight_ratio=7., high_key_weight=0.)
    old = compress_dark_scene_illumination(y, **kwargs)
    actual = compress_clear_illumination(y, **kwargs)
    assert np.all(np.diff(actual) > 0)
    region = (y > 1) & (y < 8)
    elasticity = np.diff(np.log2(actual[region]))/np.diff(np.log2(y[region]))
    assert np.min(elasticity) > .65
    assert np.ptp(actual[region]) > 2*np.ptp(old[region])
    for night_fraction in [.16,.30,.7]:
        kwargs['dark_fraction'] = night_fraction
        assert daylit_dark_weight(1.,night_fraction) == 0
        np.testing.assert_array_equal(compress_clear_illumination(y,**kwargs),
                                      compress_dark_scene_illumination(y,**kwargs))
        np.testing.assert_array_equal(clear_display_curve(y,0), sdr_curve(y))
    for weight in [0.,.1,.5,1.]:
        mapped=clear_display_curve(y,weight)
        assert np.all(np.diff(mapped) >= 0) and np.all((mapped>=0)&(mapped<=1))


def test_historical_v7_recipe_and_natural_stay_isolated_from_new_default():
    assert PHONE_CLEAR_CANDIDATE.algorithm_version == 7
    assert PHONE_CLEAR_CANDIDATE.clear_v7
    assert STYLE_PRESETS['phone-clear'].algorithm_version == 8
    assert STYLE_PRESETS['phone-natural'].algorithm_version == 8
    assert not STYLE_PRESETS['phone-natural'].clear_v7
    assert PHONE_CLEAR_CANDIDATE.pale_boundaries
    assert STYLE_PRESETS['phone-clear'].pale_boundaries


def test_bounded_curve_preserves_black_peak_and_monotone_edges():
    for upper in [1., 1000/203]:
        ramp = np.linspace(0, upper, 10001, dtype=np.float32)
        for ev in [-1, -.5, 0, .5, 1]:
            actual = apply_exposure_field(ramp, np.full_like(ramp, ev), upper=upper)
            assert actual[0] == 0
            assert np.isclose(actual[-1], upper)
            assert np.all(np.diff(actual) >= -1e-6)
            assert np.all(actual >= 0) and actual.max() <= upper+1e-6


def test_smoothing_changes_adjustment_without_ringing():
    step = np.zeros((64, 96), np.float32); step[:, 48:] = .5
    field = smooth_exposure(step, 3)
    assert field.min() >= -1e-7 and field.max() <= .500001
    assert np.all(np.diff(field, axis=1) >= -1e-6)
    assert np.max(np.abs(np.diff(field, axis=1))) < .08
    assert np.allclose(smooth_exposure(np.full_like(step, .5), 3), .5)


def test_spatial_exposure_preserves_actual_edges_and_flat_surfaces():
    yy, xx = np.mgrid[:72, :96]
    requested = .5*np.sin(xx/13)*np.cos(yy/17)
    scenes = [np.where(xx < 48, .12, .7), np.where(yy < 36, .7, .12),
              np.where(xx+yy < 72, .55, .65),
              .12+.58*np.clip((xx-40)/12, 0, 1),
              .3+.15*np.sin(xx/11)*np.cos(yy/13)]
    for original in scenes:
        field, record = edge_limited_exposure(original, requested)
        assert field.min() >= -.500001 and field.max() <= .500001
        for upper in (1., 1000/203):
            result = apply_exposure_field(original, field, upper=upper)
            for axis in (0, 1):
                before, after = np.diff(original, axis=axis), np.diff(result, axis=axis)
                assert np.all(np.sign(before)*after >= -1e-6)
                flat = np.abs(before) < 1e-8
                assert np.max(np.abs(after[flat]), initial=0) < 1e-6
        # The guard transports a gain; it does not blur any original pixels.
        constant, _ = edge_limited_exposure(original, np.full_like(original, .3))
        np.testing.assert_allclose(constant, .3, atol=1e-7)


def test_subject_gain_reaches_contour_and_is_stable_to_matte_jitter():
    rgb = np.full((96, 96, 3), .35, np.float32); rgb[:, 24:72] = .10
    face = dict(x=.4, y=.25, width=.2, height=.2, confidence=.9)
    fields = []
    for shift in [-2, 0, 2]:
        matte = np.zeros((96, 96), np.float32); matte[:, 24+shift:72+shift] = 1
        field, record = local_exposure_field(rgb, person=Image.fromarray(matte), faces=[face],
            gamut='srgb', high_key=1, indoor=0, dark=0, subject_strength=1, local_strength=1)
        fields.append(np.asarray(field))
        assert record['face_count'] == 1
        assert np.max(fields[-1]) <= .5
        # No exposure dropout exactly at the segmentation boundary.
        assert fields[-1][34, 24+shift] > 0
    assert np.max(np.abs(fields[0]-fields[2])) < .03
    mask = Image.fromarray(np.uint8(matte*255))
    from PIL import ImageFilter
    for changed in [mask.filter(ImageFilter.GaussianBlur(1.5)),
                    mask.resize((48,48)).resize((96,96),Image.Resampling.BILINEAR)]:
        field, _ = local_exposure_field(rgb,
            person=Image.fromarray(np.asarray(changed,np.float32)/255),faces=[face],
            gamut='srgb',high_key=1,indoor=0,dark=0,subject_strength=1,local_strength=1)
        assert np.max(np.abs(np.asarray(field)-fields[1])) < .03


def test_unreliable_subject_and_disabled_strength_do_not_invent_lift():
    rgb = np.full((64, 64, 3), .2, np.float32)
    for person, faces in [(None, []), (Image.fromarray(np.ones((64,64), np.float32)), [])]:
        field, record = local_exposure_field(rgb, person=person, faces=faces, gamut='srgb',
            high_key=1, indoor=0, dark=0, subject_strength=1, local_strength=1)
        np.testing.assert_array_equal(field, 0)
        assert record['subject_status'] == 'no_reliable_subject'


def test_camera_reference_metering_is_continuous_and_bounded():
    rng = np.random.default_rng(8)
    scene = rng.uniform(.01, .5, (64, 64, 3)).astype(np.float32)
    a = scene_decision(scene, development_ev=-2, peak_nits=1000)
    b = scene_decision(scene*1.001, development_ev=-2, peak_nits=1000)
    assert abs(a.sdr_auto_ev-b.sdr_auto_ev) < .01
    assert 1 <= a.hdr_midtone_gain <= 2.5
    assert a.hdr_peak_ratio <= 1000/203


def test_clear_color_retains_faint_material_chroma_without_brightness_change():
    from hdrimg.color import linear_srgb_to_oklab, oklab_to_linear_srgb, SRGB_TO_XYZ
    # Pale warm skin/stone and muted olive leaves, next to actual neutral white.
    lab = np.array([[[.7,.018,.018],[.7,-.012,.025],[.85,0,0]]],np.float32)
    original = oklab_to_linear_srgb(lab)
    for person in (None,np.array([[1.,0,0]],np.float32)):
        actual = refine_clear_color(original,target='srgb',dark_weight=0,indoor_weight=0,
            neutral_protection=True,skin_protection=np.zeros((1,3),np.float32),person_protection=person)
        np.testing.assert_allclose(actual@SRGB_TO_XYZ[1],original@SRGB_TO_XYZ[1],atol=1e-6)
        after = linear_srgb_to_oklab(actual)
        assert np.all(np.linalg.norm(after[0,:2,1:],axis=-1) >= .80*np.linalg.norm(lab[0,:2,1:],axis=-1))
        np.testing.assert_allclose(actual[0,2],original[0,2],atol=2e-6)


def test_native_warm_prior_abstains_for_indoor_dark_and_neutral_scenes():
    ramp = np.linspace(.04,.4,4096,dtype=np.float32).reshape(64,64)
    camera = ramp[...,None]*np.array([1.25,1.,.85],np.float32)
    auto = ramp[...,None]*np.ones(3,np.float32)
    decision = scene_decision(camera,development_ev=-2,peak_nits=1000)
    daylight = replace(decision, indoor_weight=0.,dark_weight=0.,high_key_weight=1.)
    bias, record = raw_temperature_bias(auto,camera,daylight)
    assert 0 < bias <= .20
    assert record['absolute_kelvin'] == 'not_exposed_by_engine_adapter'
    for d in [replace(daylight,indoor_weight=1.),replace(daylight,dark_weight=1.,dark_fraction=.3)]:
        assert raw_temperature_bias(auto,camera,d)[0] == 0
    assert raw_temperature_bias(auto,auto,daylight)[0] == 0
    assert raw_temperature_bias(auto,camera,replace(daylight,high_key_weight=0.,raw_p50=.4))[0] > 0
    assert raw_temperature_bias(auto,camera,replace(daylight,high_key_weight=0.,raw_p50=1.))[0] == 0
    assert raw_temperature_bias(auto,camera,replace(daylight,dark_weight=1.,dark_fraction=.01))[0] > 0


def test_native_bias_overlay_cannot_override_explicit_white_balance():
    import pytest
    from hdrimg.raw import _white_balance_overlay
    assert 'TemperatureBias' not in _white_balance_overlay('auto',None,1)
    assert 'TemperatureBias=0.07500000' in _white_balance_overlay('auto',None,1,temperature_bias=.075)
    for mode in ['camera','custom']:
        with pytest.raises(Exception):
            _white_balance_overlay(mode,5600,1,temperature_bias=.075)


def test_float_candidate_chunk_consistency_and_sdr_independent_hdr(tmp_path: Path, monkeypatch):
    import hdrimg.render as renderer
    monkeypatch.setattr(renderer, 'build_skin_context', lambda *a, **k: (None, {'status':'unavailable'}))
    import hdrimg.phone_subject as subject
    monkeypatch.setattr(subject, 'detect_subject_fields', lambda *a, **k: (None, None, {'status':'unavailable'}))
    y = np.geomspace(.005, 2, 96, dtype=np.float32)
    rgb = np.repeat(np.broadcast_to(y[None,:,None], (64,96,1)), 3, axis=-1).copy()
    scene = tmp_path/'scene.tif'; tifffile.imwrite(scene, rgb, photometric='rgb')
    kwargs = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0, hdr_strength=1, peak_nits=1000,
                  style=replace(PHONE_CLEAR_CANDIDATE, sdr_gamut='srgb'))
    for name, chunk, sdr_ev in [('a', 13, 0), ('b', 64, 0), ('c', 17, -.5)]:
        info = render_pair(scene, tmp_path/f'{name}.jpg', tmp_path/f'{name}.raw',
                           chunk_rows=chunk, sdr_exposure_ev=sdr_ev, **kwargs)
        assert info.tone_mapping['precision']['main_processing'] == 'float32'
        assert info.style['algorithm_version'] == 7
        assert info.sdr_quality == 98
    assert (tmp_path/'a.raw').read_bytes() == (tmp_path/'b.raw').read_bytes()
    assert (tmp_path/'a.raw').read_bytes() == (tmp_path/'c.raw').read_bytes()
    assert (tmp_path/'a.jpg').read_bytes() != (tmp_path/'c.jpg').read_bytes()


def test_candidate_hdr_strength_and_peak_range(tmp_path: Path, monkeypatch):
    import hdrimg.render as renderer
    monkeypatch.setattr(renderer,'build_skin_context',lambda *a,**k:(None,{'status':'unavailable'}))
    import hdrimg.phone_subject as subject
    monkeypatch.setattr(subject,'detect_subject_fields',lambda *a,**k:(None,None,{'status':'unavailable'}))
    y=np.geomspace(.005,3,128,dtype=np.float32)
    scene=np.broadcast_to(y[None,:,None],(64,128,3)).copy()
    path=tmp_path/'scene.tif';tifffile.imwrite(path,scene,photometric='rgb')
    for peak in [400,1000,2000]:
        sdr_reference=None
        for strength in [0.,.5,1.]:
            stem=f'{peak}-{strength}'
            render_pair(path,tmp_path/f'{stem}.jpg',tmp_path/f'{stem}.raw',
                auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=strength,peak_nits=peak,
                style=replace(PHONE_CLEAR_CANDIDATE,sdr_gamut='srgb'),
                _linear_sdr_output=tmp_path/f'{stem}.tif')
            hdr=np.memmap(tmp_path/f'{stem}.raw',dtype='<f2',shape=(64,128,4))[...,:3].astype(np.float32)
            assert np.all(np.isfinite(hdr)) and hdr.min() >= 0 and hdr.max() <= peak/203+.002
            sdr=tifffile.imread(tmp_path/f'{stem}.tif')
            if sdr_reference is None:sdr_reference=sdr.copy()
            else:np.testing.assert_array_equal(sdr,sdr_reference)
            if strength == 0:
                # The phone pipeline retains its existing dark-scene toe
                # recovery at zero strength, but must not use HDR headroom.
                assert hdr.max() <= 1.001
