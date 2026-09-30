from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms

from hdrimg.color import (
    adjust_oklab_chroma,
    linear_srgb_to_oklab,
    rec2020_to_linear_display_p3,
    refine_phone_color,
)
from hdrimg.phone_tone import (
    correct_phone_shadow_red,
    guided_log_luminance_base,
    highlight_shoulder,
    lift_midtones,
    phone_hdr_luminance,
    phone_illuminant_gains,
    phone_scene_decision,
    phone_shadow_red_offset,
    phone_sdr_luminance,
    resize_base_rows,
    restore_display_detail,
)
from hdrimg.pipeline import RenderOptions
from hdrimg.render import render_pair
from hdrimg.style import DEFAULT_STYLE, DEFAULT_STYLE_NAME, NATURAL_STYLE, resolve_style


def test_night_red_offset_requires_blue_shadow_support_and_preserves_lit_areas():
    raw = np.broadcast_to(np.array([.011, .0015, .008], dtype=np.float32), (64, 64, 3)).copy()
    offset = phone_shadow_red_offset(raw, dark_weight=1, dark_fraction=.5)
    assert .008 < offset < .012
    assert phone_shadow_red_offset(raw, dark_weight=0, dark_fraction=.5) == 0
    assert phone_shadow_red_offset(raw, dark_weight=1, dark_fraction=.01) == 0
    red = raw.copy(); red[..., 2] = .001
    assert phone_shadow_red_offset(red, dark_weight=1, dark_fraction=.5) == 0
    gray = np.full_like(raw, .005)
    assert phone_shadow_red_offset(gray, dark_weight=1, dark_fraction=.5) == 0
    raw[32:] = [.2, .3, .4]
    corrected = correct_phone_shadow_red(raw, offset=offset, development_ev=0)
    assert np.array_equal(corrected[32:], raw[32:])
    assert np.array_equal(corrected[..., 1:], raw[..., 1:])
    assert np.all(corrected >= 0)
    assert np.allclose(correct_phone_shadow_red(raw/4, offset=offset, development_ev=-2)*4, corrected)
    assert np.isfinite(phone_shadow_red_offset(np.full_like(raw, np.nan), dark_weight=1, dark_fraction=.5))


def test_style_defaults_and_overrides():
    natural = resolve_style(
        "natural", midtone_lift_ev=None, highlight_rolloff=None,
        local_contrast=None, vibrance=None, sdr_gamut=None,
    )
    phone = resolve_style(
        "phone-natural", midtone_lift_ev=None, highlight_rolloff=None,
        local_contrast=None, vibrance=None, sdr_gamut=None,
    )
    assert natural == NATURAL_STYLE
    assert phone.midtone_lift_ev == 0.38
    assert phone.highlight_rolloff == 0.42
    assert phone.local_contrast == 0
    assert phone.vibrance == 0.23
    assert phone.algorithm_version == 8
    assert phone.sdr_gamut == "display-p3"
    clear = resolve_style(
        "phone-clear", midtone_lift_ev=None, highlight_rolloff=None,
        local_contrast=None, vibrance=None, sdr_gamut=None,
    )
    assert clear.midtone_lift_ev == 0.38
    assert clear.highlight_rolloff == 0.42
    assert clear.local_contrast == 0.22
    assert clear.vibrance == 0.23
    assert clear.sdr_gamut == "display-p3"
    assert clear.algorithm_version == 8
    assert DEFAULT_STYLE_NAME == "phone-clear"
    assert DEFAULT_STYLE == clear
    assert RenderOptions(output=Path("out")).style == DEFAULT_STYLE_NAME
    custom = resolve_style(
        "phone-natural", midtone_lift_ev=0.1, highlight_rolloff=0,
        local_contrast=0, vibrance=0, sdr_gamut="srgb",
    )
    assert custom.midtone_lift_ev == 0.1
    assert custom.sdr_gamut == "srgb"


@pytest.mark.parametrize("field,value", [
    ("midtone_lift_ev", 1.1),
    ("highlight_rolloff", -0.1),
    ("local_contrast", 0.31),
    ("vibrance", 0.31),
    ("sdr_gamut", "adobe-rgb"),
    ("style", "unknown"),
    ("sdr_exposure_ev", -2.1),
    ("sdr_adaptation_strength", 1.1),
    ("hdr_midtone_gain", 0.9),
    ("hdr_shoulder_strength", -0.1),
    ("subject_adaptation_strength", 1.1),
    ("skin_protection_strength", 1.1),
    ("raw_denoise_strength", 1.1),
])
def test_phone_options_reject_invalid_values(field, value):
    with pytest.raises(Exception):
        RenderOptions(output=Path("out"), **{field: value}).validate()


def test_midtone_and_highlight_curves_remain_monotone():
    y = np.geomspace(1e-4, 16, 20000, dtype=np.float32)
    lifted = lift_midtones(y, low=0.05, center=0.4, high=2.0, ev=0.30)
    assert np.all(np.diff(lifted) >= -1e-6)
    assert np.isclose(lifted[0], y[0])
    assert lifted[np.argmin(abs(y - 0.4))] > y[np.argmin(abs(y - 0.4))]
    assert np.isclose(lifted[-1], y[-1])
    compressed = highlight_shoulder(lifted, start=1.0, span=3.0, strength=0.35)
    assert np.all(np.diff(compressed) >= -1e-6)
    assert np.allclose(compressed[lifted < 1], lifted[lifted < 1])
    assert compressed[-1] < lifted[-1]


def test_phone_sdr_curve_separates_indoor_shadows_and_highkey_shadows():
    y = np.linspace(0, 1, 10001, dtype=np.float32)
    indoor = phone_sdr_luminance(y, contrast=0.35, pivot=0.22, shadow_lift=0)
    highkey = phone_sdr_luminance(y, contrast=0, pivot=0.32, shadow_lift=1)
    assert np.all(np.diff(indoor) >= -1e-6)
    assert np.all(np.diff(highkey) >= -1e-6)
    assert indoor[1000] < y[1000]
    assert highkey[1000] > y[1000]
    assert np.isclose(indoor[-1], 1)
    assert np.isclose(highkey[-1], 1)
    dark = phone_sdr_luminance(
        y, contrast=0, pivot=0.32, shadow_lift=0,
        source=y, dark_shoulder=2.5, source_start=0.5,
        source_anchor=0.95,
    )
    assert np.all(np.diff(dark) >= -1e-6)
    assert dark[5000] < 0.5
    assert np.isclose(dark[-1], 1)


def test_local_base_is_finite_and_chunk_resizing_is_seamless():
    y = np.ones((48, 64), dtype=np.float32) * 0.2
    y[:, 32:] = 1.2
    base = guided_log_luminance_base(y)
    assert base.shape == y.shape
    assert np.all(np.isfinite(base))
    image = Image.fromarray(base, mode="F")
    full = resize_base_rows(image, width=128, full_height=257, start=0, stop=257)
    chunks = np.concatenate([
        resize_base_rows(image, width=128, full_height=257, start=a, stop=b)
        for a, b in ((0, 71), (71, 188), (188, 257))
    ])
    assert np.max(abs(full - chunks)) < 0.02


def test_vibrance_preserves_neutral_and_protects_skin():
    rgb = np.array([[[0.4, 0.4, 0.4], [0.65, 0.28, 0.20], [0.10, 0.25, 0.65]]], dtype=np.float32)
    adjusted = adjust_oklab_chroma(rgb, target="srgb", amount=1.0, vibrance=0.15)
    assert np.max(abs(adjusted[0, 0] - rgb[0, 0])) < 1e-4
    before = linear_srgb_to_oklab(rgb)
    after = linear_srgb_to_oklab(adjusted)
    gain = np.hypot(after[..., 1], after[..., 2]) / np.hypot(before[..., 1], before[..., 2])
    assert gain[0, 2] > gain[0, 1]


def test_phone_scene_and_hdr_separate_dark_midtones_from_light_sources():
    scene = np.full((96, 96, 3), 0.01, dtype=np.float32)
    scene[:, 88:] = 1.8
    decision = phone_scene_decision(scene, development_ev=0, peak_nits=1000)
    assert decision.dark_weight > 0.9
    assert decision.sdr_auto_ev < -1
    sdr_y = np.full(3, 0.25, dtype=np.float32)
    hdr = phone_hdr_luminance(
        sdr_y, source_y=np.array([0.2, 2.0, 8.0], dtype=np.float32),
        source_highlight_start=1.0, source_highlight_anchor=8.0,
        dark_weight=1.0, dark_highlight_power=1.5,
        manual_highlight_bonus=0.0, manual_highlight_start=1.0,
        manual_highlight_anchor=8.0,
        midtone_gain=1.2, peak_target=4.0, peak_limit=4.93,
        highlight_anchor=0.8, shoulder_strength=0.7, hdr_strength=1.0,
    )
    assert hdr[0] < hdr[1] < hdr[2]
    assert hdr[0] < 0.4
    assert hdr[2] > 3.0


def test_phone_color_refinement_protects_neutral_and_separates_blue_from_warm():
    rgb = np.array([[[0.3, 0.3, 0.3], [0.08, 0.22, 0.58], [0.48, 0.20, 0.10]]], dtype=np.float32)
    adjusted = refine_phone_color(rgb, target="srgb", dark_weight=0, indoor_weight=0)
    assert np.max(abs(adjusted[0, 0] - rgb[0, 0])) < 1e-4
    old = linear_srgb_to_oklab(rgb)
    new = linear_srgb_to_oklab(adjusted)
    old_hue = np.degrees(np.arctan2(old[..., 2], old[..., 1])) % 360
    new_hue = np.degrees(np.arctan2(new[..., 2], new[..., 1])) % 360
    assert new_hue[0, 1] > old_hue[0, 1] + 5
    assert new_hue[0, 2] > old_hue[0, 2] + 5


def test_smooth_hdr_highlight_join_has_no_slope_jump():
    y = np.linspace(0.418, 0.422, 4001, dtype=np.float32)
    result = phone_hdr_luminance(
        y, source_y=y, source_highlight_start=0.6, source_highlight_anchor=1,
        dark_weight=0, dark_highlight_power=1, manual_highlight_bonus=0,
        manual_highlight_start=0.8, manual_highlight_anchor=1,
        midtone_gain=1.9, peak_target=2.5, peak_limit=4.93,
        highlight_anchor=0.8, shoulder_strength=0.7, hdr_strength=1,
        smooth_highlights=True,
    )
    left = (result[2000]-result[1900])/(y[2000]-y[1900])
    right = (result[2100]-result[2000])/(y[2100]-y[2000])
    assert abs(right-left) < 0.04
    assert np.all(np.diff(result) >= 0)


def test_display_detail_preserves_endpoints_and_restores_texture():
    source = np.array([[0.4, 0.6, 0.4, 0.6]], dtype=np.float32)
    base = np.full_like(source, np.log2(0.5))
    mapped = np.array([[0, .78, .75, 1]], dtype=np.float32)
    restored = restore_display_detail(mapped, source_y=source, base_log=base, strength=.65)
    assert restored[0, 0] == 0
    assert restored[0, -1] == 1
    assert restored[0, 1] > mapped[0, 1]
    assert restored[0, 2] < mapped[0, 2]
    assert np.all(np.isfinite(restored))
    assert np.all((restored >= 0) & (restored <= 1))


def test_neutral_protection_reduces_weak_warm_cast_without_desaturating_skin():
    rgb = np.array([[[.60, .54, .49], [.65, .28, .20], [.35, .35, .35]]],dtype=np.float32)
    old = refine_phone_color(rgb, target="srgb", dark_weight=0, indoor_weight=0)
    new = refine_phone_color(rgb, target="srgb", dark_weight=0, indoor_weight=0, neutral_protection=True)
    old_lab, new_lab = linear_srgb_to_oklab(old), linear_srgb_to_oklab(new)
    assert np.linalg.norm(new_lab[0,0,1:]) < .75 * np.linalg.norm(old_lab[0,0,1:])
    np.testing.assert_allclose(np.linalg.norm(new_lab[0,1,1:]), np.linalg.norm(old_lab[0,1,1:]), rtol=1e-4)
    assert np.max(abs(new[0,2] - rgb[0,2])) < 1e-4


def test_night_illuminant_requires_dark_scene_and_supported_neutrals():
    scene = np.full((80, 80, 3), .003, dtype=np.float32)
    scene[30:70] = np.linspace(.15, .7, 40, dtype=np.float32)[:, None, None] * np.array([1.1, 1, .7],dtype=np.float32)
    for dark, fraction in ((0, .5), (1, .03)):
        gains, strength = phone_illuminant_gains(scene,dark_weight=dark,dark_fraction=fraction)
        assert np.array_equal(gains,np.ones(3,dtype=np.float32))
        assert strength == 0
    gains, strength = phone_illuminant_gains(scene,dark_weight=1,dark_fraction=.5)
    assert strength > 0
    assert gains[2] > gains[1] > gains[0]
    assert np.all((gains>=2**-.65)&(gains<=2**.65))


def test_phone_clear_new_controls_keep_sdr_and_hdr_independent(tmp_path: Path):
    if not Path("/System/Library/ColorSync/Profiles/Display P3.icc").is_file():
        pytest.skip("macOS Display P3 profile unavailable")
    scene = np.full((64, 96, 3), 0.035, dtype=np.float32)
    scene[:, 70:] = 0.35
    source = tmp_path / "dark-scene.tif"
    tifffile.imwrite(source, scene, photometric="rgb")
    common = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0, hdr_strength=1, peak_nits=1000)
    render_pair(source, tmp_path / "auto.jpg", tmp_path / "auto.raw", **common)
    render_pair(
        source, tmp_path / "without-adaptation.jpg", tmp_path / "without-adaptation.raw",
        sdr_adaptation_strength=0, **common,
    )
    auto = np.asarray(Image.open(tmp_path / "auto.jpg"), dtype=np.float32)
    without = np.asarray(Image.open(tmp_path / "without-adaptation.jpg"), dtype=np.float32)
    assert np.median(auto) < np.median(without)
    render_pair(
        source, tmp_path / "more-hdr.jpg", tmp_path / "more-hdr.raw",
        hdr_midtone_gain=2.5, **common,
    )
    assert (tmp_path / "auto.jpg").read_bytes() == (tmp_path / "more-hdr.jpg").read_bytes()
    old_hdr = np.fromfile(tmp_path / "auto.raw", dtype="<f2").reshape(64, 96, 4)
    new_hdr = np.fromfile(tmp_path / "more-hdr.raw", dtype="<f2").reshape(64, 96, 4)
    assert np.median(new_hdr[..., 1]) > np.median(old_hdr[..., 1])
    render_pair(
        source, tmp_path / "darker-sdr.jpg", tmp_path / "darker-sdr.raw",
        sdr_exposure_ev=-0.45, **common,
    )
    assert (tmp_path / "auto.jpg").read_bytes() != (tmp_path / "darker-sdr.jpg").read_bytes()
    assert (tmp_path / "auto.raw").read_bytes() == (tmp_path / "darker-sdr.raw").read_bytes()


def test_default_render_matches_phone_clear_and_natural_remains_selectable(tmp_path: Path):
    if not Path("/System/Library/ColorSync/Profiles/Display P3.icc").is_file():
        pytest.skip("macOS Display P3 profile unavailable")
    x = np.geomspace(0.005, 2.0, 96, dtype=np.float32)
    scene = np.broadcast_to(x[None, :, None], (64, 96, 3)).copy()
    scene[16:48, 30:65] *= np.array([0.6, 0.8, 1.2], dtype=np.float32)
    source = tmp_path / "scene.tif"
    tifffile.imwrite(source, scene, photometric="rgb")
    options = dict(auto_exposure=False, exposure_ev=0, highlight_ev=0, hdr_strength=1, peak_nits=1000)
    default_info = render_pair(source, tmp_path / "default.jpg", tmp_path / "default.raw", **options)
    render_pair(source, tmp_path / "clear.jpg", tmp_path / "clear.raw", style=DEFAULT_STYLE, **options)
    assert default_info.style["name"] == "phone-clear"
    assert (tmp_path / "default.jpg").read_bytes() == (tmp_path / "clear.jpg").read_bytes()
    render_pair(source, tmp_path / "natural.jpg", tmp_path / "natural.raw", style=NATURAL_STYLE, **options)
    assert (tmp_path / "natural.jpg").read_bytes() != (tmp_path / "default.jpg").read_bytes()
    with Image.open(tmp_path / "natural.jpg") as image:
        profile = ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"]))
        assert "sRGB" in ImageCms.getProfileName(profile)
    phone = resolve_style("phone-natural", midtone_lift_ev=None, highlight_rolloff=None, local_contrast=None, vibrance=None, sdr_gamut=None)
    info = render_pair(source, tmp_path / "phone.jpg", tmp_path / "phone.raw", style=phone, **options)
    with Image.open(tmp_path / "phone.jpg") as image:
        profile = ImageCms.ImageCmsProfile(BytesIO(image.info["icc_profile"]))
        assert "Display P3" in ImageCms.getProfileName(profile)
    assert info.style["sdr_gamut"] == "display-p3"
    neutral = rec2020_to_linear_display_p3(np.array([[0.3, 0.3, 0.3]], dtype=np.float32))
    assert np.allclose(neutral, [[0.3, 0.3, 0.3]], atol=1e-4)


def test_phone_rolloff_keeps_extreme_sdr_lights_below_white(tmp_path: Path):
    scene = np.full((64, 128, 3), 0.25, dtype=np.float32)
    scene[:, -20:] = np.linspace(1.0, 16.0, 20, dtype=np.float32)[None, :, None]
    source = tmp_path / "extreme-lights.tif"
    tifffile.imwrite(source, scene, photometric="rgb")
    phone = resolve_style(
        "phone-natural", midtone_lift_ev=None, highlight_rolloff=None,
        local_contrast=None, vibrance=None, sdr_gamut=None,
    )
    render_pair(
        source, tmp_path / "sdr.jpg", tmp_path / "hdr.raw",
        auto_exposure=False, exposure_ev=0, highlight_ev=0,
        hdr_strength=1, peak_nits=1000, auto_look=False, style=phone,
    )
    with Image.open(tmp_path / "sdr.jpg") as image:
        encoded = np.asarray(image, dtype=np.float32) / 255
    linear = np.where(
        encoded <= 0.04045, encoded / 12.92,
        ((encoded + 0.055) / 1.055) ** 2.4,
    )
    assert float(np.max(linear[:, -20:])) < 0.93


def test_dark_intermediate_hdr_lift_preserves_order_and_peak_budget():
    source = np.geomspace(.001, 60, 20000, dtype=np.float32)
    base = np.full_like(source, .3)
    kwargs = dict(source_y=source, source_highlight_start=2.4, source_highlight_anchor=50,
        dark_weight=1, dark_highlight_power=2, manual_highlight_bonus=0,
        manual_highlight_start=1, manual_highlight_anchor=2, midtone_gain=1,
        peak_target=4.9, peak_limit=4.93, highlight_anchor=.8,
        shoulder_strength=.7, hdr_strength=1, smooth_highlights=True)
    original = phone_hdr_luminance(base, **kwargs)
    corrected = phone_hdr_luminance(base, dark_intermediate_lift=.12, **kwargs)
    assert np.min(np.diff(corrected)) >= -1e-6
    assert np.all(np.isfinite(corrected))
    assert np.array_equal(corrected[source<1.2], original[source<1.2])
    assert corrected[np.argmin(abs(source-2.4))] > original[np.argmin(abs(source-2.4))]+.10
    assert np.allclose(corrected[source>=50], original[source>=50],atol=1e-6)


def test_warm_white_estimator_requires_neutral_support_and_rejects_colored_wall():
    from hdrimg.phone_tone import phone_warm_white_gains
    ramp = np.linspace(.05, 1, 256, dtype=np.float32)[None,:,None]
    gray = np.repeat(np.broadcast_to(ramp,(128,256,1)),3,axis=-1)
    for rgb in [gray, gray*np.array([1.4,.8,.4]), np.full_like(gray, .3)]:
        gains, strength = phone_warm_white_gains(rgb,dark_weight=0)
        assert strength == 0 and np.array_equal(gains,np.ones(3))
    warm = gray*np.array([1.06,1,.94])
    gains,strength=phone_warm_white_gains(warm,dark_weight=0)
    assert strength > 0 and gains[0] < 1 and gains[2] > 1
    assert np.max(abs(np.log2(gains))) <= .3+1e-6
    assert phone_warm_white_gains(warm,dark_weight=1)[1] == 0


def test_small_outdoor_neutral_patch_cannot_drive_global_color():
    from hdrimg.phone_tone import phone_warm_white_gains
    rgb = np.full((256,256,3), [.4,.2,.07], dtype=np.float32)
    # A small bright neutral patch must not decide the whole outdoor illuminant.
    rgb[:40] = np.linspace(.2,.8,40,dtype=np.float32)[:,None,None]*np.array([1.04,1,.96])
    gains, strength = phone_warm_white_gains(rgb,dark_weight=0,indoor_weight=0)
    _, indoor = phone_warm_white_gains(rgb,dark_weight=0,indoor_weight=1)
    assert 0 <= strength < indoor
