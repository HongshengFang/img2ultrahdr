import numpy as np
from hdrimg.phone_histogram import local_histogram_fields


def test_flat_field_and_zero_strength_are_unchanged():
    y = np.full((64,96),.25,dtype=np.float32)
    h,s,_ = local_histogram_fields(y)
    np.testing.assert_allclose(h,1,atol=1e-6)
    np.testing.assert_allclose(s,1,atol=1e-6)
    y = np.random.default_rng(13).uniform(.1,.7,(64,96)).astype(np.float32)
    h,s,_ = local_histogram_fields(y,0)
    np.testing.assert_allclose(h,1,atol=1e-6)
    np.testing.assert_allclose(s,1,atol=1e-6)


def test_textured_field_is_bounded_and_small_field_falls_back():
    y = np.random.default_rng(12).uniform(.05,.7,(64,96)).astype(np.float32)
    h,s,record = local_histogram_fields(y)
    assert np.isfinite(h).all() and np.isfinite(s).all()
    assert record['max_abs_odds_ev'] <= .65*.65+1e-6
    assert not np.allclose(s,1)
    h,s,record = local_histogram_fields(np.ones((2,3),np.float32)*.3)
    assert record['reason']=='too_small'
    np.testing.assert_array_equal(s,1)


def test_detected_head_is_protected_from_histogram_relighting():
    y=np.random.default_rng(31).uniform(.1,.6,(128,128)).astype(np.float32)
    _,plain,_=local_histogram_fields(y)
    _,protected,_=local_histogram_fields(y,faces=[{'x':.35,'y':.35,'width':.3,'height':.3}])
    np.testing.assert_allclose(np.asarray(protected)[55:65,55:65],1,atol=1e-6)
    assert not np.allclose(np.asarray(plain)[55:65,55:65],1)


def test_zero_local_contrast_skips_histogram_stage(tmp_path, monkeypatch):
    from dataclasses import replace
    import tifffile
    from hdrimg import phone_histogram
    from hdrimg.render import render_pair
    from hdrimg.style import DEFAULT_STYLE

    def unexpected(*args, **kwargs):
        raise AssertionError("Disabled local contrast must not invoke histogram correction")

    monkeypatch.setattr(phone_histogram, "local_histogram_fields", unexpected)
    rgb = np.random.default_rng(24).uniform(.05, .6, (32, 48, 3)).astype(np.float32)
    path = tmp_path / "scene.tif"
    tifffile.imwrite(path, rgb, photometric="rgb")
    result = render_pair(path, tmp_path / "sdr.jpg", tmp_path / "hdr.raw",
        auto_exposure=True, exposure_ev=None, highlight_ev=0, hdr_strength=1,
        peak_nits=1000, subject_adaptation_strength=0,
        style=replace(DEFAULT_STYLE, local_contrast=0))
    assert "phone_histogram" not in result.tone_mapping
