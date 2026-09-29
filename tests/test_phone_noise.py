import numpy as np

from hdrimg.phone_noise import phone_denoise_decision, residual_shadow_noise


def test_dark_flat_noise_enables_extra_denoise_but_daylight_does_not():
    rng = np.random.default_rng(983)
    raw_y = np.maximum(.04+rng.normal(0, .012, (512, 512)), .001).astype(np.float32)
    scene = np.repeat((raw_y*.25)[..., None], 3, axis=-1)
    dark = phone_denoise_decision(scene, development_ev=-2)
    assert dark["flat_tiles"] >= 16
    assert dark["extra_denoise_weight"] > .5
    day = phone_denoise_decision(scene+.25, development_ev=-2)
    assert day["extra_denoise_weight"] == 0


def test_smooth_structure_and_insufficient_support_do_not_trigger():
    ramp = np.linspace(.005, .018, 512, dtype=np.float32)[None, :, None]
    scene = np.broadcast_to(ramp, (512, 512, 3))
    assert phone_denoise_decision(scene, development_ev=-2)["extra_denoise_weight"] == 0
    for scene in (np.zeros((32, 32, 3), np.float32),
                  np.full((128, 128, 3), .01, np.float32),
                  np.full((512, 512, 3), np.nan, np.float32)):
        data = residual_shadow_noise(scene, development_ev=-2)
        assert data["relative_noise"] == 0
        assert np.all(np.isfinite(list(data.values())))
