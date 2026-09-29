from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from hdrimg.render import open_scene, render_pair, write_rgba16f
from hdrimg.style import NATURAL_STYLE


def test_write_rgba16f_layout(tmp_path: Path):
    rgb = np.array([[[0.25, 0.5, 1.0], [2.0, 3.0, 4.0]]], dtype=np.float32)
    path = tmp_path / "pixels.raw"
    write_rgba16f(path, rgb)
    assert path.stat().st_size == 2 * 8
    decoded = np.fromfile(path, dtype="<f2").reshape(1, 2, 4)
    assert np.allclose(decoded[..., :3], rgb)
    assert np.all(decoded[..., 3] == 1)


def test_render_synthetic_float_tiff(tmp_path: Path):
    width, height = 128, 64
    ramp = np.geomspace(1e-4, 16, width, dtype=np.float32)
    scene = np.broadcast_to(ramp, (height, width))[..., None]
    scene = np.repeat(scene, 3, axis=-1).copy()
    scene_path = tmp_path / "scene.tif"
    tifffile.imwrite(scene_path, scene, photometric="rgb")
    sdr = tmp_path / "sdr.jpg"
    hdr = tmp_path / "hdr.raw"
    info = render_pair(
        scene_path,
        sdr,
        hdr,
        auto_exposure=False,
        exposure_ev=0,
        highlight_ev=0,
        hdr_strength=1,
        peak_nits=1000,
        chunk_rows=17,
    )
    assert info.scene.width == width
    assert info.look["exposure_source"] == "manual"
    assert info.look["contrast_source"] == "auto"
    assert info.look["saturation_source"] == "auto"
    assert info.style["name"] == "phone-clear"
    assert hdr.stat().st_size == width * height * 8
    with Image.open(sdr) as image:
        assert image.size == (width, height)
        assert image.info.get("icc_profile")


def test_open_scene_rejects_integer_tiff(tmp_path: Path):
    path = tmp_path / "integer.tif"
    tifffile.imwrite(path, np.zeros((2, 2, 3), dtype=np.uint16), photometric="rgb")
    try:
        open_scene(path)
    except Exception as exc:
        assert "32-bit float" in str(exc)
    else:
        raise AssertionError("integer TIFF should be rejected")


def test_sdr_exposure_adjustment_leaves_hdr_rendition_unchanged(tmp_path: Path):
    ramp = np.linspace(0.05, 2.0, 48, dtype=np.float32)
    scene = np.broadcast_to(ramp[None, :, None], (32, 48, 3)).copy()
    source = tmp_path / "scene.tif"
    tifffile.imwrite(source, scene, photometric="rgb")
    settings = dict(
        auto_exposure=False,
        exposure_ev=1.0,
        highlight_ev=0.0,
        hdr_strength=1.0,
        peak_nits=1000,
        auto_look=False,
        style=NATURAL_STYLE,
    )
    render_pair(source, tmp_path / "base.jpg", tmp_path / "base.raw", **settings)
    render_pair(
        source,
        tmp_path / "darker.jpg",
        tmp_path / "darker.raw",
        sdr_exposure_ev=-0.4,
        **settings,
    )
    assert (tmp_path / "base.raw").read_bytes() == (tmp_path / "darker.raw").read_bytes()
    with Image.open(tmp_path / "base.jpg") as base, Image.open(tmp_path / "darker.jpg") as darker:
        assert np.asarray(darker, dtype=np.float32).mean() < np.asarray(base, dtype=np.float32).mean()
