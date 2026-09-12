import numpy as np

from hdrimg.look import (
    DEFAULT_CONTRAST,
    DEFAULT_EXPOSURE_EV,
    DEFAULT_SATURATION,
    resolve_look,
)


def _gradient_scene(*, colorful: bool) -> np.ndarray:
    ramp = np.geomspace(0.02, 0.8, 256, dtype=np.float32)
    gray = np.broadcast_to(ramp, (128, 256))[..., None]
    if not colorful:
        return np.repeat(gray, 3, axis=-1).copy()
    color = np.array([1.35, 0.75, 0.35], dtype=np.float32)
    return gray * color


def test_auto_look_uses_scene_measurements_and_records_sources():
    decision = resolve_look(
        _gradient_scene(colorful=False),
        base_scene_adjustment_ev=0.0,
        enabled=True,
        auto_exposure=True,
        exposure_ev=None,
        contrast=None,
        saturation=None,
    )
    assert decision.exposure_source == "auto"
    assert decision.contrast_source == "auto"
    assert decision.saturation_source == "auto"
    assert -0.35 <= decision.exposure_ev <= 0.35
    assert 1.30 <= decision.contrast <= 1.65
    assert 1.05 <= decision.saturation <= 1.18
    assert decision.metrics["dynamic_range_stops"] > 0


def test_auto_saturation_is_more_conservative_for_colorful_scenes():
    neutral = resolve_look(
        _gradient_scene(colorful=False),
        base_scene_adjustment_ev=0.0,
        enabled=True,
        auto_exposure=True,
        exposure_ev=None,
        contrast=None,
        saturation=None,
    )
    colorful = resolve_look(
        _gradient_scene(colorful=True),
        base_scene_adjustment_ev=0.0,
        enabled=True,
        auto_exposure=True,
        exposure_ev=None,
        contrast=None,
        saturation=None,
    )
    assert neutral.saturation > colorful.saturation


def test_manual_values_override_auto_look_individually():
    decision = resolve_look(
        _gradient_scene(colorful=False),
        base_scene_adjustment_ev=0.0,
        enabled=True,
        auto_exposure=True,
        exposure_ev=0.25,
        contrast=1.4,
        saturation=1.2,
    )
    assert decision.exposure_ev == 0.25
    assert decision.contrast == 1.4
    assert decision.saturation == 1.2
    assert decision.exposure_source == "manual"
    assert decision.contrast_source == "manual"
    assert decision.saturation_source == "manual"

    partial = resolve_look(
        _gradient_scene(colorful=False),
        base_scene_adjustment_ev=0.0,
        enabled=True,
        auto_exposure=True,
        exposure_ev=None,
        contrast=1.4,
        saturation=None,
    )
    assert partial.exposure_source == "auto"
    assert partial.contrast_source == "manual"
    assert partial.saturation_source == "auto"


def test_disabled_auto_look_uses_fixed_baseline():
    decision = resolve_look(
        _gradient_scene(colorful=False),
        base_scene_adjustment_ev=0.0,
        enabled=False,
        auto_exposure=True,
        exposure_ev=None,
        contrast=None,
        saturation=None,
    )
    assert decision.exposure_ev == DEFAULT_EXPOSURE_EV
    assert decision.contrast == DEFAULT_CONTRAST
    assert decision.saturation == DEFAULT_SATURATION
    assert decision.exposure_source == "default"
    assert decision.contrast_source == "default"
    assert decision.saturation_source == "default"


def test_no_auto_exposure_prevents_automatic_look_exposure():
    decision = resolve_look(
        _gradient_scene(colorful=False),
        base_scene_adjustment_ev=0.0,
        enabled=True,
        auto_exposure=False,
        exposure_ev=None,
        contrast=None,
        saturation=None,
    )
    assert decision.exposure_ev == 0.0
    assert decision.exposure_source == "default"
