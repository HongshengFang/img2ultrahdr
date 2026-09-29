import numpy as np
import pytest
from PIL import Image

from hdrimg.phone_edges import person_boundary_protection
from hdrimg.phone_tone import apply_local_contrast, restore_display_detail


def boundary():
    mask = np.zeros((256, 256), np.float32)
    mask[:, :128] = 1
    return np.asarray(person_boundary_protection(Image.fromarray(mask)))


def test_boundary_band_covers_both_sides_and_feathers_to_zero():
    band = boundary()
    assert band.dtype == np.float32
    assert np.min(band[:, 126:130]) > .9
    assert np.max(band) <= .95
    np.testing.assert_array_equal(band[:, :90], 0)
    np.testing.assert_array_equal(band[:, 166:], 0)
    assert np.max(np.abs(np.diff(band, axis=1))) < .16
    np.testing.assert_allclose(band, band[:, ::-1], atol=.01)


@pytest.mark.parametrize('value', [0, .35, 1])
def test_uniform_or_missing_person_has_no_boundary(value):
    assert person_boundary_protection(None) is None
    assert person_boundary_protection(Image.fromarray(np.full((64, 64), value, np.float32))) is None


@pytest.mark.parametrize('display', [False, True])
def test_protection_reduces_both_dark_and_bright_halos_without_changing_remote_detail(display):
    source = np.full((256, 256), .25, np.float32)
    source[:, 128:] = .6
    # The smoothed base mixes a dark subject and its brighter background.
    base = np.full(source.shape, np.log2(.4), np.float32)
    band = boundary()
    def apply(protection):
        if display:
            return restore_display_detail(source, source_y=source, base_log=base,
                                          strength=.9, protection=protection)
        return apply_local_contrast(source, base_log=base, strength=.22,
                                    protection=protection)
    plain, protected = apply(None), apply(band)
    assert np.max(np.abs(protected[:, 126:130] - source[:, 126:130])) < .1 * np.max(
        np.abs(plain[:, 126:130] - source[:, 126:130]))
    np.testing.assert_array_equal(protected[:, :90], plain[:, :90])
    np.testing.assert_array_equal(apply(np.zeros_like(band)), plain)
    np.testing.assert_allclose(apply(np.ones_like(band)), source, atol=1e-7)
