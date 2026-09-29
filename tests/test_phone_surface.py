from pathlib import Path
import numpy as np
import tifffile

from hdrimg.color import linear_srgb_to_rec2020
from hdrimg.phone_surface import denoise_blue_surfaces, smooth_blue_support


def _write(path, data):
    profile = b"test icc payload"
    tifffile.imwrite(path, data, photometric="rgb", extratags=[(34675,'B',len(profile),profile,False)])


def test_blue_support_rejects_skin_neutral_and_structured_edges():
    neutral=np.full((64,64,3),.1,np.float32)
    assert np.max(smooth_blue_support(neutral,development_ev=-2)) == 0
    skin=linear_srgb_to_rec2020(np.broadcast_to([.2,.12,.07],(64,64,3)))
    assert np.max(smooth_blue_support(skin,development_ev=-2)) == 0
    blue=linear_srgb_to_rec2020(np.broadcast_to([.05,.10,.2],(64,64,3)))
    assert np.mean(smooth_blue_support(blue,development_ev=-2)) > .9
    blue[:,32:]*=.03
    mask=smooth_blue_support(blue,development_ev=-2)
    assert mask[:,29:35].mean() < mask[:,:20].mean()*.25


def test_filter_reduces_noise_preserves_mean_icc_and_input(tmp_path: Path):
    rng=np.random.default_rng(871)
    rgb=np.broadcast_to([.06,.11,.22],(128,160,3)).copy()
    noisy=linear_srgb_to_rec2020((rgb+rng.normal(0,.002,rgb.shape)).astype(np.float32))
    source=tmp_path/'source.tif';target=tmp_path/'target.tif';_write(source,noisy)
    before=source.read_bytes()
    result=denoise_blue_surfaces(source,target)
    filtered=tifffile.imread(target)
    assert result['applied']
    assert filtered.std(axis=(0,1)).mean() < noisy.std(axis=(0,1)).mean()*.65
    np.testing.assert_allclose(filtered.mean(axis=(0,1)),noisy.mean(axis=(0,1)),atol=1e-4)
    assert source.read_bytes() == before
    with tifffile.TiffFile(target) as tif:assert tif.pages[0].tags[34675].value == b"test icc payload"


def test_chunk_boundaries_are_equivalent(tmp_path):
    rng=np.random.default_rng(817)
    rgb=linear_srgb_to_rec2020((np.broadcast_to([.04,.09,.2],(129,111,3))+rng.normal(0,.003,(129,111,3))).astype(np.float32))
    source=tmp_path/'source.tif';_write(source,rgb)
    a,b=tmp_path/'a.tif',tmp_path/'b.tif'
    denoise_blue_surfaces(source,a,chunk_rows=17)
    denoise_blue_surfaces(source,b,chunk_rows=256)
    np.testing.assert_allclose(tifffile.imread(a),tifffile.imread(b),atol=1e-7)


def test_disabled_and_unsupported_surfaces_are_exact_bypasses(tmp_path):
    source=tmp_path/'neutral.tif';target=tmp_path/'out.tif'
    _write(source,np.full((64,64,3),.1,np.float32))
    assert not denoise_blue_surfaces(source,target,strength=0)['applied']
    assert not target.exists()
    assert not denoise_blue_surfaces(source,target)['applied']
    assert not target.exists()
