import numpy as np

from hdrimg.color import (
    adjust_oklab_chroma,
    compress_gamut,
    linear_srgb_to_oklab,
    linear_srgb_to_rec2020,
    oklab_to_linear_srgb,
    rec2020_to_linear_srgb,
    srgb_oetf,
)


def test_rgb_space_round_trip():
    colors = np.array(
        [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.2, 0.4, 0.8]], dtype=np.float32
    )
    result = rec2020_to_linear_srgb(linear_srgb_to_rec2020(colors))
    assert np.allclose(result, colors, atol=2e-6)


def test_oklab_round_trip():
    colors = np.array([[0.2, 0.4, 0.8], [1.0, 0.0, 0.0]], dtype=np.float32)
    result = oklab_to_linear_srgb(linear_srgb_to_oklab(colors))
    assert np.allclose(result, colors, atol=2e-6)


def test_gamut_compression_fits_cube_and_preserves_neutral():
    colors = np.array([[1.4, -0.2, 0.3], [0.4, 0.4, 0.4]], dtype=np.float32)
    result = compress_gamut(colors, target="srgb", upper=1.0)
    assert np.all(result >= 0)
    assert np.all(result <= 1)
    assert np.allclose(result[1], colors[1], atol=2e-5)


def test_gamut_compression_preserves_oklab_hue():
    color = np.array([[1.3, -0.15, 0.4]], dtype=np.float32)
    before = linear_srgb_to_oklab(color)[0]
    after_rgb = compress_gamut(color, target="srgb", upper=1.0)
    after = linear_srgb_to_oklab(after_rgb)[0]
    before_hue = np.arctan2(before[2], before[1])
    after_hue = np.arctan2(after[2], after[1])
    assert np.isclose(before_hue, after_hue, atol=2e-3)


def test_srgb_transfer_known_points():
    values = np.array([0.0, 0.0031308, 1.0], dtype=np.float32)
    result = srgb_oetf(values)
    assert np.allclose(result, [0.0, 0.04044994, 1.0], atol=2e-6)


def test_oklab_chroma_adjustment_preserves_neutral_and_increases_chroma():
    colors = np.array([[0.3, 0.3, 0.3], [0.7, 0.2, 0.1]], dtype=np.float32)
    adjusted = adjust_oklab_chroma(colors, target="srgb", amount=1.08)
    assert np.allclose(adjusted[0], colors[0], atol=2e-6)
    before = linear_srgb_to_oklab(colors[1:])
    after = linear_srgb_to_oklab(adjusted[1:])
    assert np.allclose(after[..., 0], before[..., 0], atol=2e-6)
    assert np.linalg.norm(after[..., 1:]) > np.linalg.norm(before[..., 1:])
