from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

from hdrimg import render
from hdrimg.color import (
    linear_srgb_to_oklab, linear_srgb_to_rec2020, oklab_to_linear_srgb,
    rec2020_to_linear_srgb, refine_phone_color,
)
from hdrimg.phone_skin import build_skin_context, protect_skin_illuminant
from hdrimg.style import DEFAULT_STYLE, STYLE_PRESETS
from hdrimg.tone import luminance_rec2020, scale_rgb_to_luminance


def skin_samples():
    # Pale through dark skin with the same hue family, plus cream and orange.
    lightness = np.array([.78, .62, .38, .22, .8, .65], np.float32)
    chroma = lightness * [.07, .09, .11, .09, .02, .23]
    hue = np.deg2rad([55, 50, 50, 50, 80, 65])
    lab = np.stack([lightness, chroma*np.cos(hue), chroma*np.sin(hue)], axis=-1)
    return linear_srgb_to_rec2020(oklab_to_linear_srgb(lab.astype(np.float32)))


def relative_chroma(rgb):
    lab = linear_srgb_to_oklab(rec2020_to_linear_srgb(rgb))
    return np.hypot(lab[..., 1], lab[..., 2]) / np.maximum(lab[..., 0], .02)


def test_pale_and_dark_skin_keep_color_but_still_receive_white_correction():
    rgb = skin_samples()
    gains = np.array([.90, .98, 1.13], np.float32)
    full = scale_rgb_to_luminance(rgb*gains, luminance_rec2020(rgb))
    protected, mask = protect_skin_illuminant(rgb, gains, 1., strength=1)
    before, after = relative_chroma(rgb), relative_chroma(protected)
    assert np.all(after[:4] > .72*before[:4])
    assert np.all(after[:4] < .95*before[:4])
    assert np.all(after[:4] > relative_chroma(full)[:4])
    np.testing.assert_allclose(luminance_rec2020(protected), luminance_rec2020(rgb), atol=1e-7)
    # White clothing still gets full correction; a strong original orange cast
    # is not frozen by a relative-to-source chroma floor.
    np.testing.assert_allclose(protected[4:], full[4:], atol=1e-7)
    assert mask[4] == 0


def test_background_and_zero_strength_match_full_white_correction():
    rgb = skin_samples()
    gains = np.array([.90, .98, 1.13], np.float32)
    full = scale_rgb_to_luminance(rgb*gains, luminance_rec2020(rgb))
    for person, strength in [(0., 1.), (1., 0.)]:
        protected, mask = protect_skin_illuminant(rgb, gains, person, strength=strength)
        np.testing.assert_array_equal(protected, full)
        assert not np.any(mask)


def test_warm_gray_clothing_is_not_mistaken_for_skin_inside_person():
    hue = np.deg2rad(39.)
    lab = np.array([[.65, .65*.055*np.cos(hue), .65*.055*np.sin(hue)]], np.float32)
    rgb = linear_srgb_to_rec2020(oklab_to_linear_srgb(lab))
    gains = np.array([.8814, 1.0008, 1.1336], np.float32)
    actual, mask = protect_skin_illuminant(rgb, gains, 1., strength=1)
    full = scale_rgb_to_luminance(rgb*gains, luminance_rec2020(rgb))
    assert mask[0] < .05
    np.testing.assert_allclose(actual, full, atol=1e-4)


def test_skin_protection_does_not_add_color_when_no_white_correction_is_needed():
    rgb = skin_samples()
    protected, _ = protect_skin_illuminant(rgb, np.ones(3, np.float32), 1., strength=1)
    np.testing.assert_allclose(protected, rgb, atol=1e-7)
    black, mask = protect_skin_illuminant(np.zeros((2, 3), np.float32),
        np.array([.9, 1, 1.1], np.float32), 1., strength=1)
    assert np.isfinite(black).all() and not black.any() and not mask.any()


def test_color_protection_is_independent_of_brightness_and_has_soft_boundaries():
    rgb = skin_samples()[:4]
    gains = np.array([.90, .98, 1.13], np.float32)
    expected, weight = protect_skin_illuminant(rgb, gains, 1., strength=1)
    for scale in (.08, .5, 2., 8.):
        actual, actual_weight = protect_skin_illuminant(rgb*scale, gains, 1., strength=1)
        np.testing.assert_allclose(actual/scale, expected, atol=2e-6)
        np.testing.assert_allclose(actual_weight, weight, atol=5e-6)
    ramp = np.linspace(0, 1, 1001, dtype=np.float32)
    output, _ = protect_skin_illuminant(np.broadcast_to(rgb[0], (1001, 3)),
        gains, ramp, strength=1)
    assert np.max(abs(np.diff(output, axis=0))) < 1e-4


def test_neutral_desaturation_spares_skin_without_protecting_identical_background():
    rgb = np.broadcast_to(skin_samples()[0], (2, 3)).copy()
    ordinary = refine_phone_color(rgb, target="rec2020", dark_weight=0,
        indoor_weight=0, neutral_protection=True)
    protected = refine_phone_color(rgb, target="rec2020", dark_weight=0,
        indoor_weight=0, neutral_protection=True, skin_protection=np.array([1., 0.]))
    assert relative_chroma(protected)[0] > relative_chroma(ordinary)[0]
    np.testing.assert_array_equal(protected[1], ordinary[1])


def test_unavailable_detector_records_color_only_fallback(monkeypatch):
    monkeypatch.setattr("hdrimg.phone_skin._vision_helper", lambda: None)
    mask, record = build_skin_context(np.full((16, 16, 3), .1, np.float32), exposure_ev=0)
    assert mask is None and record["status"] == "color_only_fallback"


def test_same_independent_reference_reuses_matte_without_sharing_mutable_state(monkeypatch):
    import hdrimg.phone_skin as module
    module._PERSON_CACHE.clear()
    monkeypatch.setattr(module, "_vision_helper", lambda: Path("/fake/vision"))
    calls = []
    def run(args, **kwargs):
        calls.append(1)
        prefix = Path(args[2])
        prefix.with_suffix(".json").write_text(json.dumps({"faces": []}))
        Image.fromarray(np.full((16, 16), 255, np.uint8)).save(str(prefix)+"_person.png")
    monkeypatch.setattr(module.subprocess, "run", run)
    scene = np.full((16, 16, 3), .15, np.float32)
    image, record = build_skin_context(scene, exposure_ev=0)
    image.putpixel((0, 0), 0)
    record["status"] = "mutated"
    image, record = build_skin_context(scene, exposure_ev=0)
    assert len(calls) == 1 and image.getpixel((0, 0)) == 1
    assert record["reference_cache_hit"] and record["status"] == "applied"
    for i in range(6):
        build_skin_context(scene, exposure_ev=i*.1)
    assert len(module._PERSON_CACHE) == 4
    monkeypatch.setattr(module, "_vision_helper", lambda: None)
    assert build_skin_context(scene, exposure_ev=0)[1]["status"] == "color_only_fallback"
    module._PERSON_CACHE.clear()


@pytest.mark.parametrize("style", [DEFAULT_STYLE, STYLE_PRESETS["phone-natural"]])
def test_renderer_reuses_independent_person_mask_and_preserves_hdr_contract(tmp_path, monkeypatch, style):
    scene = np.broadcast_to(skin_samples()[0], (48, 64, 3)).copy()
    scene *= np.linspace(.3, 2, 64, dtype=np.float32)[None, :, None]
    source = tmp_path / "scene.tif"
    tifffile.imwrite(source, scene, photometric="rgb")
    calls = []
    def detect(array, **kwargs):
        calls.append(1)
        person = np.zeros((48, 64), np.float32)
        person[:, 16:48] = 1
        return Image.fromarray(person), {"status": "applied"}
    monkeypatch.setattr(render, "build_skin_context", detect)
    monkeypatch.setattr(render, "detect_subject_fields",
        lambda *a, **k: (None, None, {"status": "no_reliable_subject"}))
    common = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0,
        hdr_strength=1, peak_nits=1000)
    for name, sdr_ev in [("base", 0), ("darker", -.5)]:
        before = len(calls)
        info = render.render_pair(source, tmp_path/(name+".jpg"), tmp_path/(name+".raw"),
            sdr_exposure_ev=sdr_ev, style=style, **common)
        assert len(calls) == before + 1
        assert info.tone_mapping["phone_skin"]["status"] == "applied"
        if style.name == "phone-natural":
            assert info.tone_mapping["phone_edge_protection"]["status"] == "inactive"
            assert info.tone_mapping["phone_display_detail_strength"] == 0
            assert "phone_histogram" not in info.tone_mapping
            assert info.tone_mapping["phone_skin"]["pale_color_protection"]["preserves_luminance"]
        else:
            assert info.tone_mapping["phone_edge_protection"]["status"] == "applied"
    hdr = np.fromfile(tmp_path/"base.raw", dtype="<f2").astype(np.float32)
    darker = np.fromfile(tmp_path/"darker.raw", dtype="<f2").astype(np.float32)
    np.testing.assert_allclose(hdr, darker, atol=.004)
    for name, extra in [("disabled", {"skin_protection_strength": 0,
                                      "style": replace(DEFAULT_STYLE, algorithm_version=5)}),
                         ("legacy", {"style": replace(DEFAULT_STYLE, algorithm_version=4)})]:
        render.render_pair(source, tmp_path/(name+".jpg"), tmp_path/(name+".raw"), **common, **extra)
    np.testing.assert_array_equal(np.fromfile(tmp_path/"disabled.raw", dtype="<f2"),
                                  np.fromfile(tmp_path/"legacy.raw", dtype="<f2"))
