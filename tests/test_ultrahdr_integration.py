from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from hdrimg.render import write_rgba16f
from hdrimg.tools import ToolPaths, find_tool
from hdrimg.ultrahdr import encode_ultrahdr, validate_ultrahdr


@pytest.mark.integration
def test_libultrahdr_round_trip(tmp_path: Path):
    ultra = find_tool("ultrahdr_app")
    if not ultra:
        pytest.skip("ultrahdr_app is not installed")
    width, height = 96, 48
    ramp = np.linspace(0.0, 1.0, width, dtype=np.float32)
    sdr_linear = np.empty((height, width, 3), dtype=np.float32)
    sdr_linear[:16] = np.repeat(ramp[None, :, None], 3, axis=-1)
    sdr_linear[16:32, :, 0] = ramp
    sdr_linear[16:32, :, 1] = 0.04
    sdr_linear[16:32, :, 2] = 0.12
    sdr_linear[32:, :, 0] = ramp**2
    sdr_linear[32:, :, 1] = np.sqrt(ramp)
    sdr_linear[32:, :, 2] = ramp
    sdr_encoded = np.where(
        sdr_linear <= 0.0031308,
        sdr_linear * 12.92,
        1.055 * np.power(sdr_linear, 1 / 2.4) - 0.055,
    )
    sdr = tmp_path / "sdr.jpg"
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.fromarray((sdr_encoded * 255 + 0.5).astype(np.uint8)).save(
        sdr, quality=95, subsampling=0, icc_profile=profile
    )
    hdr = tmp_path / "hdr.raw"
    hdr_scale = np.array([2.0, 2.5, 3.0], dtype=np.float32)
    write_rgba16f(hdr, sdr_linear * hdr_scale)
    output = tmp_path / "ultrahdr.jpg"
    tools = ToolPaths(Path("rawtherapee-cli"), ultra, Path("exiftool"))
    encode_ultrahdr(
        sdr,
        hdr,
        output,
        width=width,
        height=height,
        peak_nits=1000,
        max_boost=1000 / 203,
        gainmap_quality=95,
        tools=tools,
    )
    result = validate_ultrahdr(
        output, width=width, height=height, work_dir=tmp_path, tools=tools
    )
    assert result.sdr_decoded_bytes == width * height * 4
    assert result.hdr_decoded_bytes == width * height * 8
    assert result.hdr_peak_luminance > 1.0
    assert result.hdr_above_reference_fraction > 0.0
    decoded = np.fromfile(tmp_path / "decoded_hdr.rgba16f", dtype="<f2")
    decoded = decoded.reshape(height, width, 4)[..., :3].astype(np.float32)
    expected = sdr_linear * hdr_scale
    mse = float(np.mean((decoded - expected) ** 2))
    psnr = 10.0 * np.log10(9.0 / max(mse, 1e-12))
    assert psnr > 30.0
