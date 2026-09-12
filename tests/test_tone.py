import numpy as np

from hdrimg.tone import (
    SDR_WHITE_NITS,
    apply_exposure_and_highlights,
    contrast_curve,
    exposure_statistics,
    hdr_curve,
    hdr_curve_with_highlight_expansion,
    max_content_boost,
    sdr_curve,
)


def test_tone_curves_are_monotonic_and_bounded():
    y = np.linspace(0.0, 100.0, 10001, dtype=np.float32)
    sdr = sdr_curve(y)
    hdr = hdr_curve(y, peak_nits=1000, hdr_strength=1.0)
    boost = max_content_boost(1000, 1.0)
    assert np.all(np.diff(sdr) >= 0)
    assert np.all(np.diff(hdr) >= 0)
    assert 0 <= sdr.min() <= sdr.max() <= 1.0
    assert 0 <= hdr.min() <= hdr.max() <= boost
    assert np.all(hdr + 1e-6 >= sdr)


def test_zero_hdr_strength_matches_sdr():
    y = np.geomspace(1e-5, 100.0, 500, dtype=np.float32)
    assert np.allclose(
        hdr_curve(y, peak_nits=1000, hdr_strength=0.0), sdr_curve(y)
    )


def test_middle_gray_differs_by_less_than_ten_percent():
    y = np.array([0.18], dtype=np.float32)
    sdr = sdr_curve(y)[0]
    hdr = hdr_curve(y, peak_nits=1000, hdr_strength=1.0)[0]
    assert (hdr - sdr) / sdr < 0.10


def test_default_peak_ratio():
    assert np.isclose(max_content_boost(1000, 1.0), 1000 / SDR_WHITE_NITS)


def test_auto_exposure_maps_log_average_toward_middle_gray():
    image = np.full((64, 64, 3), 0.045, dtype=np.float32)
    stats = exposure_statistics(image, auto_exposure=True, exposure_ev=0.0)
    assert np.isclose(stats.auto_ev, 2.0)
    assert np.isclose(stats.total_ev, 2.0)


def test_development_headroom_is_compensated_without_changing_effective_auto_ev():
    image = np.full((64, 64, 3), 0.18 / 4.0, dtype=np.float32)
    stats = exposure_statistics(
        image, auto_exposure=True, exposure_ev=0.25, development_ev=-2.0
    )
    assert np.isclose(stats.auto_ev, 0.0, atol=1e-6)
    assert np.isclose(stats.total_ev, 0.25, atol=1e-6)
    assert np.isclose(stats.scene_adjustment_ev, 2.25, atol=1e-6)


def test_highlight_adjustment_does_not_change_black_or_middle_gray():
    image = np.array([[[0.0, 0.0, 0.0], [0.18, 0.18, 0.18], [1.0, 1.0, 1.0]]])
    adjusted = apply_exposure_and_highlights(image, total_ev=0, highlight_ev=-1)
    assert np.allclose(adjusted[0, 0], 0)
    assert np.allclose(adjusted[0, 1], image[0, 1], atol=1e-6)
    assert np.allclose(adjusted[0, 2], 0.5, atol=1e-6)


def test_contrast_curve_preserves_middle_gray_and_adds_a_toe():
    y = np.array([0.01, 0.02, 0.18, 0.8], dtype=np.float32)
    mapped = contrast_curve(y, black_luminance=0.01, contrast=1.2)
    assert mapped[0] == 0.0
    assert mapped[1] < y[1]
    assert np.isclose(mapped[2], 0.18, atol=1e-6)
    assert mapped[3] > y[3]
    assert np.all(np.diff(mapped) >= 0.0)


def test_hdr_highlight_expansion_crosses_reference_white_but_protects_mid_gray():
    y = np.array([0.18, 0.4, 0.7, 1.0], dtype=np.float32)
    expanded = hdr_curve_with_highlight_expansion(
        y,
        peak_nits=1000,
        hdr_strength=1.0,
        highlight_start=0.5,
        highlight_anchor=0.7,
        highlight_lift=3.5,
    )
    assert (expanded[0] - sdr_curve(y)[0]) / sdr_curve(y)[0] < 0.1
    assert expanded[2] > 1.0
    assert np.all(np.diff(expanded) >= 0.0)
    assert np.allclose(
        hdr_curve_with_highlight_expansion(
            y,
            peak_nits=1000,
            hdr_strength=0.0,
            highlight_start=0.5,
            highlight_anchor=0.7,
            highlight_lift=3.5,
        ),
        sdr_curve(y),
    )
