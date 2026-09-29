from pathlib import Path

import numpy as np
import pytest

from hdrimg import color
from hdrimg.phone_skin import refine_phone_skin_color
from hdrimg.pipeline import RenderOptions


@pytest.mark.parametrize("target", ["srgb", "display-p3", "rec2020"])
def test_pale_skin_retains_chroma_without_changing_luminance_or_background(target):
    # Identical pale warm colors, including a protected subject and an
    # unprotected wall. One neutral cloth sample must stay unchanged too.
    lab = np.array([[.7, .023, .025]] * 5 + [[.7, 0, 0]], np.float32)
    srgb = color.oklab_to_linear_srgb(lab)
    matrix = {"srgb": np.eye(3, dtype=np.float32),
              "display-p3": color.SRGB_TO_DISPLAY_P3,
              "rec2020": color.SRGB_TO_REC2020}[target]
    y_coeff = {"srgb": color.SRGB_TO_XYZ[1], "display-p3": color.DISPLAY_P3_TO_XYZ[1],
               "rec2020": color.REC2020_TO_XYZ[1]}[target]
    rgb = srgb @ matrix.T
    mask = np.array([0, .04, .25, .64, 1, 0], np.float32)
    kwargs = dict(target=target, dark_weight=0, indoor_weight=0, neutral_protection=True)
    old = color.refine_phone_color(rgb, skin_protection=mask, **kwargs)
    new = refine_phone_skin_color(rgb, skin_protection=mask, **kwargs)
    np.testing.assert_array_equal(new[[0, 5]], old[[0, 5]])
    np.testing.assert_allclose(new @ y_coeff, old @ y_coeff, atol=1e-7)
    def relative_chroma(values):
        lab = color.linear_srgb_to_oklab(values @ np.linalg.inv(matrix).T)
        return np.hypot(lab[:, 1], lab[:, 2]) / lab[:, 0]
    assert np.all(relative_chroma(new)[1:4] > relative_chroma(old)[1:4])
    assert np.all(relative_chroma(new)[:5] <= relative_chroma(new)[4] + 1e-6)
    np.testing.assert_array_equal(new[4], old[4])
    for absent in [None, np.zeros_like(mask)]:
        np.testing.assert_array_equal(refine_phone_skin_color(rgb, skin_protection=absent, **kwargs),
                                      color.refine_phone_color(rgb, skin_protection=absent, **kwargs))


def test_pale_skin_transition_is_continuous_and_black_stays_black():
    mask = np.linspace(0, 1, 10001, dtype=np.float32)
    rgb = np.broadcast_to([.36, .29, .26], (len(mask), 3)).astype(np.float32)
    kwargs = dict(target="srgb", dark_weight=0, indoor_weight=0, neutral_protection=True)
    result = refine_phone_skin_color(rgb, skin_protection=mask, **kwargs)
    assert np.max(np.abs(np.diff(result, axis=0))) < .001
    np.testing.assert_array_equal(refine_phone_skin_color(np.zeros_like(rgb), skin_protection=mask, **kwargs), 0)


def test_phone_natural_raw_defaults_and_explicit_controls():
    options = RenderOptions(output=Path("out"), style="phone-natural")
    assert options.resolved_white_balance() == "auto"
    for resolve in ["resolved_raw_skin_strength", "resolved_raw_denoise_strength",
                    "resolved_raw_detail_strength", "resolved_surface_denoise_strength"]:
        assert getattr(options, resolve)() == 1
        for override in [{"auto_look": False}, {"contrast": 1.3}, {"saturation": 1.1}]:
            assert getattr(RenderOptions(output=Path("out"), style="phone-natural", **override), resolve)() == 0
    for wb in ["camera", "custom"]:
        explicit = RenderOptions(output=Path("out"), style="phone-natural", white_balance=wb, temperature_k=5600)
        explicit.validate()
        assert explicit.resolved_white_balance() == wb
        assert explicit.resolved_raw_skin_strength() == 0
    manual = RenderOptions(output=Path("out"), style="phone-natural", auto_look=False,
                           raw_denoise_strength=.5, raw_detail_strength=.4, surface_denoise_strength=.3)
    manual.validate()
    assert manual.resolved_raw_denoise_strength() == .5
    assert manual.resolved_raw_detail_strength() == .4
    assert manual.resolved_surface_denoise_strength() == .3
