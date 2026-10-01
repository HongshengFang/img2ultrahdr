import io
import json
import struct
from argparse import Namespace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms, MpoImagePlugin

pytest.importorskip('torch')
pytest.importorskip('cv2')
from hdrimg.jpeg_hdr import pipeline
from hdrimg.jpeg_hdr.codec import Codec
from hdrimg.jpeg_hdr.metadata import normalize_iso_metadata, ISO_NAMESPACE
from hdrimg.jpeg_hdr.appearance import phone_gain_ev, constrain_gain
from hdrimg.errors import DependencyError


def jpeg(image, **kwargs):
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=100, **kwargs)
    return buf.getvalue()


def test_guided_filter_preserves_constant_and_reduces_edge_bleed():
    low = np.zeros((64, 64), np.float32); low[:, 32:] = 1
    high = np.zeros((256, 256), np.float32); high[:, 128:] = 1
    guided = pipeline.fast_guided(low, low * 2, high, eps=0.0001)
    bilinear = pipeline.cv2.resize(low * 2, (256, 256))
    assert guided[:, 127].mean() < bilinear[:, 127].mean() * 0.1
    assert np.allclose(pipeline.fast_guided(low, low * 0 + .7, high), .7, atol=1e-5)


@pytest.fixture
def codec():
    try:
        return Codec()
    except DependencyError as exc:
        pytest.skip(str(exc))


@pytest.mark.integration
@pytest.mark.parametrize('max_ev', [1., 2.5])
@pytest.mark.parametrize('sample', [0, 153, 255])
def test_jpeg_gainmap_roundtrip_preserves_base_and_metadata(codec, sample, max_ev):
    color = np.zeros((64, 96, 3), np.uint8); color[:] = [180, 140, 100]
    exif = Image.Exif(); exif[274] = 6
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    base = jpeg(Image.fromarray(color), progressive=True, exif=exif, icc_profile=profile)
    gain = jpeg(Image.fromarray(np.full((64, 96), sample, np.uint8)))
    encoded = codec.encode(base, gain, max_ev)
    report = codec.verify(encoded, base, max_ev)
    assert report['jpeg_scan_identical'] and report['sdr_pixels_identical']
    assert report['exif_identical'] and report['icc_identical']
    assert report['google_ultrahdr_v1_1_xmp'] and report['gcontainer_mpf_agree']
    assert report['hdr_rgb_abs_error_p99'] < .025
    assert report['decoded_gain_ev_max'] == pytest.approx(sample / 255 * max_ev, abs=1e-4)
    # Pillow independently follows the MPF index, including the expanded
    # gain JPEG's declared length. Skia reads this fixed rational-pair layout.
    container = Image.open(io.BytesIO(encoded))
    # Pillow recognizes the Ultra HDR XMP signature and deliberately returns
    # an SDR-only JPEG object; explicitly use its MPF reader for this check.
    container = MpoImagePlugin.MpoImageFile.adopt(container)
    container.seek(1)
    assert container.size == (96, 64)
    assert np.asarray(container.convert('L')).mean() == pytest.approx(sample, abs=1)
    iso = next((payload for marker, payload in container.applist
                if marker == 'APP2' and payload.startswith(b'urn:iso:std:iso:ts:21496:-1\0')), None)
    if iso is not None:
        metadata = iso[28:]
        assert len(metadata) == 61  # version, flags, two headrooms, five channel fields
        assert metadata[4] & 0x3f == 0  # reserved bits must be zero
        values = [struct.unpack_from('>II', metadata, i) for i in range(5, 61, 8)]
        assert all(denominator > 0 for _, denominator in values)
        assert values[1][0] / values[1][1] == pytest.approx(max_ev)
        assert values[3][0] / values[3][1] == pytest.approx(max_ev)
    assert normalize_iso_metadata(encoded) == encoded
    # Hide ISO from the independent reference decoder without shifting any
    # JPEG offsets. Successful reconstruction must then use Google v1.1 XMP.
    xmp_only = encoded.replace(ISO_NAMESPACE, b'x' * len(ISO_NAMESPACE))
    xmp_report = codec.verify(xmp_only, base, max_ev)
    assert xmp_report['decoded_gain_ev_max'] == pytest.approx(report['decoded_gain_ev_max'])
    assert xmp_report['hdr_linear_rgb_max'] == pytest.approx(report['hdr_linear_rgb_max'])
    with pytest.raises(ValueError, match='already contains'):
        codec.encode(encoded, gain, max_ev)


@pytest.mark.integration
def test_full_jpeg_pipeline_with_controlled_prediction(codec, monkeypatch, tmp_path):
    source = tmp_path / 'source.jpg'
    rgb = np.zeros((128, 192, 3), np.uint8)
    rgb[:, :96] = 110; rgb[:, 96:] = 250
    source.write_bytes(jpeg(Image.fromarray(rgb)))
    original = source.read_bytes()
    def fake_hdr(linear, **kwargs):
        scale = np.ones(linear.shape[:2], np.float32)
        scale[:, 96:] = 4
        return linear * scale[..., None]
    def fake_protection(image, prompts, *args):
        assert prompts[0]['points'] == [[150., 40.]]
        cap = np.full(image.shape[:2], np.inf, np.float32)
        cap[:64, 96:] = .25
        return cap
    monkeypatch.setattr(pipeline, 'predict_hdr', fake_hdr)
    monkeypatch.setattr(pipeline, 'sam_protect', fake_protection)
    protection = tmp_path / 'protect.json'
    protection.write_text(json.dumps({'regions': [{'points': [[150, 40]], 'max_ev': .25}]}))
    out = tmp_path / 'out.jpg'
    args = Namespace(input=source, output=out, ai_size=768, max_ev=2.5, strength=1,
                     protect=protection, fp32=False, overwrite=False)
    pipeline.run(args)
    report = json.loads((tmp_path / 'out_diagnostics/report.json').read_text())
    gain = np.load(tmp_path / 'out_diagnostics/gain_ev_full.npy')
    assert source.read_bytes() == original
    assert report['verification']['sdr_pixels_identical']
    assert gain[:64, 96:].max() <= .25
    assert gain[80:, 130:].mean() > 1
    with pytest.raises(FileExistsError):
        pipeline.run(args)


@pytest.mark.integration
def test_subject_floor_brightens_midtones_and_protection_wins(codec, monkeypatch, tmp_path):
    source = tmp_path / 'source.jpg'
    source.write_bytes(jpeg(Image.fromarray(np.full((64, 96, 3), 180, np.uint8))))
    monkeypatch.setattr(pipeline, 'predict_hdr', lambda linear, **kwargs: linear.copy())
    def fake_constraints(image, prompts, *args, return_floor=False):
        assert return_floor
        cap = np.full(image.shape[:2], .8, np.float32)
        cap[:, :32] = .03
        return cap, np.full(image.shape[:2], .7, np.float32)
    monkeypatch.setattr(pipeline, 'sam_protect', fake_constraints)
    protection = tmp_path / 'protect.json'
    protection.write_text(json.dumps({'regions': [{'points': [[50, 32]], 'min_ev': .7, 'max_ev': .8}]}))
    args = Namespace(input=source, output=tmp_path / 'adapted.jpg', ai_size=768,
                     max_ev=1, strength=1, protect=protection, fp32=False, overwrite=False)
    pipeline.run(args)
    gain = np.load(tmp_path / 'adapted_diagnostics/gain_ev_full.npy')
    assert gain[:, :32].max() <= .03
    assert gain[:, 40:].mean() == pytest.approx(.7, abs=.001)
    args.strength = 0
    args.output = tmp_path / 'zero.jpg'
    pipeline.run(args)
    assert np.max(np.load(tmp_path / 'zero_diagnostics/gain_ev_full.npy')) == 0


def test_phone_luminance_curve_is_monotone_and_keeps_shadow_depth():
    y = np.linspace(0, 1, 10001, dtype=np.float32)
    gain = phone_gain_ev(y, peak_nits=1000)
    target = y * np.exp2(gain)
    assert np.all(np.diff(target) >= -1e-6)
    assert target[-1] == pytest.approx(1000 / 203)
    assert gain[y < .015].max() < .01
    assert np.exp2(gain[np.argmin(abs(y - .31))]) > 2.5
    assert np.exp2(gain[np.argmin(abs(y - .19))]) > 2
    # A saturated material must respect the brightest RGB channel as well as
    # luminance; the overlapping flower cap must still win over subject lift.
    rgb = np.array([[[.1, .2, .9], [.9, .9, .9]]], np.float32)
    constrained = constrain_gain(np.full((1, 2), 3.), rgb,
                                 np.array([[3., .03]]), max_ev=3., peak_ratio=4.)
    assert (rgb * np.exp2(constrained)[..., None]).max() <= 4.00001
    assert constrained[0, 1] == pytest.approx(.03)


@pytest.mark.integration
def test_gain_range_and_display_headroom_are_independent(codec):
    base = jpeg(Image.fromarray(np.full((64, 96, 3), 120, np.uint8)))
    gain = jpeg(Image.fromarray(np.full((64, 96), 170, np.uint8)))
    encoded = codec.encode(base, gain, 3., hdr_capacity_max=4.)
    report = codec.verify(encoded, base, 3., hdr_capacity_max=4.)
    assert report['max_content_boost'] == [8.] * 3
    assert report['hdr_capacity_max'] == pytest.approx(4.)
    assert report['gainmap_xmp']['GainMapMax'] == pytest.approx(3.)
    assert report['gainmap_xmp']['HDRCapacityMax'] == pytest.approx(2.)
    assert report['hdr_rgb_abs_error_p99'] < .015
    xmp_only = encoded.replace(ISO_NAMESPACE, b'x' * len(ISO_NAMESPACE))
    xmp_report = codec.verify(xmp_only, base, 3., hdr_capacity_max=4.)
    assert xmp_report['hdr_linear_rgb_max'] == report['hdr_linear_rgb_max']


@pytest.mark.integration
def test_phone_pipeline_lifts_midtones_but_protection_still_wins(codec, monkeypatch, tmp_path):
    rgb = np.full((64, 96, 3), 150, np.uint8)
    rgb[:, :32] = 245
    source = tmp_path / 'source.jpg'
    source.write_bytes(jpeg(Image.fromarray(rgb)))
    monkeypatch.setattr(pipeline, 'predict_hdr', lambda linear, **kwargs: linear)
    def fake_protection(image, prompts, *args):
        cap = np.full(image.shape[:2], np.inf, np.float32)
        cap[:, :32] = .03
        return cap
    monkeypatch.setattr(pipeline, 'sam_protect', fake_protection)
    protection = tmp_path / 'protect.json'
    protection.write_text(json.dumps({'regions': [{'points': [[10, 30]], 'max_ev': .03}]}))
    args = Namespace(input=source, output=tmp_path/'phone.jpg', ai_size=768,
                     max_ev=3, strength=1, protect=protection, fp32=False,
                     overwrite=False, look='phone', peak_nits=1000.)
    pipeline.run(args)
    diagnostic = tmp_path / 'phone_diagnostics'
    gain = np.load(diagnostic/'gain_ev_full.npy')
    report = json.loads((diagnostic/'report.json').read_text())
    assert gain[:, :32].max() <= .03
    assert np.exp2(gain[:, 40:]).mean() > 2.5
    assert report['verification']['sdr_pixels_identical']
    assert report['verification']['hdr_capacity_max'] == pytest.approx(1000/203, rel=1e-5)
    args.strength = 0
    args.output = tmp_path/'disabled.jpg'
    pipeline.run(args)
    assert np.max(np.load(tmp_path/'disabled_diagnostics/gain_ev_full.npy')) == 0
    args.peak_nits = float('nan')
    args.output = tmp_path/'invalid.jpg'
    with pytest.raises(ValueError, match='peak-nits'):
        pipeline.run(args)
