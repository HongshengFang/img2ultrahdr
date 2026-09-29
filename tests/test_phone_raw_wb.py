from pathlib import Path
import shutil

import numpy as np
from PIL import Image
import pytest
import tifffile

from hdrimg import cli, pipeline, render
from hdrimg.color import linear_srgb_to_rec2020, oklab_to_linear_srgb
from hdrimg.errors import ProcessingError
from hdrimg.phone_raw_wb import guard_skin_chroma, preserve_raw_skin
from hdrimg.pipeline import RenderOptions
from hdrimg.tone import luminance_rec2020, scale_rgb_to_luminance
from hdrimg.tools import ToolPaths


def colors(relative, hue=50, lightness=.6):
    relative = np.asarray(relative)
    angle = np.deg2rad(hue)
    lab = np.stack([np.full_like(relative, lightness),
                    lightness * relative * np.cos(angle),
                    lightness * relative * np.sin(angle)], axis=-1).astype(np.float32)
    return linear_srgb_to_rec2020(oklab_to_linear_srgb(lab))


def test_raw_wb_default_is_style_specific_and_manual_wb_is_respected():
    args = cli._parser().parse_args(['render', 'photo.CR2'])
    assert args.white_balance is None
    assert RenderOptions(output=Path('out')).resolved_white_balance() == 'auto'
    assert RenderOptions(output=Path('out'), style='natural').resolved_white_balance() == 'camera'
    assert RenderOptions(output=Path('out'), style='phone-natural').resolved_white_balance() == 'auto'
    for wb in ['camera', 'auto', 'custom']:
        options = RenderOptions(output=Path('out'), white_balance=wb, temperature_k=5600)
        options.validate()
        assert options.resolved_white_balance() == wb
    for extra in [{'white_balance': 'camera'}, {'white_balance': 'custom'},
                  {'skin_protection_strength': 0}, {'auto_look': False},
                  {'contrast': 1.3}, {'saturation': 1.1}, {'style': 'natural'}]:
        assert RenderOptions(output=Path('out'), **extra).resolved_raw_skin_strength() == 0


def test_guard_retains_pale_skin_without_restoring_strong_cast_or_exposure():
    camera = colors([.09, .09, .30, .02])
    automatic = colors([.055, .075, .10, .02])
    result, weight = guard_skin_chroma(automatic, camera * 4, 1, strength=1)
    assert .1 < weight[0] < .85
    np.testing.assert_allclose(result[1:], automatic[1:], atol=1e-7)
    np.testing.assert_allclose(luminance_rec2020(result), luminance_rec2020(automatic), atol=1e-7)
    # Adaptation is a bounded blend and retains part of the automatic WB.
    reference = scale_rgb_to_luminance(camera, luminance_rec2020(automatic))
    assert np.linalg.norm(result[0] - automatic[0]) < np.linalg.norm(reference[0] - automatic[0])
    for person, strength in [(0, 1), (1, 0)]:
        unchanged, _ = guard_skin_chroma(automatic, camera, person, strength=strength)
        np.testing.assert_array_equal(unchanged, automatic)


def test_guard_excludes_neutral_clothing_and_has_no_change_without_chroma_loss():
    automatic = np.concatenate([colors([.0, .012]), colors([.07, .10])])
    camera = np.concatenate([colors([.065, .09]), colors([.07, .05])])
    actual, weight = guard_skin_chroma(automatic, camera, 1, strength=1)
    np.testing.assert_allclose(actual, automatic, atol=1e-7)
    np.testing.assert_allclose(weight, 0, atol=1e-6)
    dark, _ = guard_skin_chroma(automatic / 4, camera / 4, 1, strength=1)
    np.testing.assert_allclose(dark, actual / 4, atol=1e-7)


@pytest.mark.parametrize('pale_boundaries', [False, True])
def test_scene_guard_preserves_icc_geometry_detail_and_chunk_independence(monkeypatch, tmp_path, pale_boundaries):
    import hdrimg.phone_raw_wb as wb
    h, w = 32, 48
    noise = np.linspace(.8, 1.2, w, dtype=np.float32)[None, :, None]
    scene = np.broadcast_to(colors([.055])[0], (h, w, 3)).copy() * noise
    camera = np.broadcast_to(colors([.09])[0], scene.shape).copy() * 3
    source, reference = tmp_path/'auto.tif', tmp_path/'camera.tif'
    icc = b'test-icc-preservation'
    tifffile.imwrite(source, scene, photometric='rgb', extratags=[(34675, 'B', len(icc), icc, False)])
    tifffile.imwrite(reference, camera, photometric='rgb')
    monkeypatch.setattr(wb, 'build_skin_context', lambda *a, **k:
        (Image.fromarray(np.ones((h, w), np.float32)), {'status': 'applied'}))
    results = []
    for chunk in [7, 32]:
        destination = tmp_path / f'out-{chunk}.tif'
        record, context = preserve_raw_skin(source, reference, destination, chunk_rows=chunk,
                                            pale_boundaries=pale_boundaries)
        assert record['version'] == (2 if pale_boundaries else 1)
        assert record['applied'] and record['affected_fraction'] > .9
        assert context[1]['status'] == 'applied'
        with tifffile.TiffFile(destination) as tif:
            assert tif.pages[0].tags[34675].value == icc
        result = tifffile.imread(destination)
        assert result.shape == scene.shape and result.dtype == np.float32
        np.testing.assert_allclose(luminance_rec2020(result), luminance_rec2020(scene), atol=1e-7)
        results.append(result)
    np.testing.assert_allclose(*results, atol=1e-7)
    tifffile.imwrite(reference, camera[:10], photometric='rgb')
    with pytest.raises(ValueError, match='geometry'):
        preserve_raw_skin(source, reference, tmp_path/'bad.tif')


def test_raw_boundary_guard_includes_faint_skin_without_changing_neutrals_or_background():
    automatic = colors([.018, .025, .04, .0, .012, .018])
    camera = colors([.045, .05, .06, .065, .09, .045])
    person = np.array([1, 1, 1, 1, 1, 0], np.float32)
    old, old_weight = guard_skin_chroma(automatic, camera, person, strength=1)
    new, weight = guard_skin_chroma(automatic, camera, person, strength=1, pale_boundaries=True)
    assert old_weight[0] == 0 and weight[0] > .1
    assert np.all(weight[:3] > old_weight[:3])
    np.testing.assert_allclose(new[3:], automatic[3:], atol=1e-7)
    np.testing.assert_allclose(luminance_rec2020(new), luminance_rec2020(automatic), atol=1e-7)
    for scale in [.05, .5, 4.]:
        scaled, scaled_weight = guard_skin_chroma(automatic*scale, camera*scale, person,
            strength=1, pale_boundaries=True)
        np.testing.assert_allclose(scaled/scale, new, atol=1e-6)
        np.testing.assert_allclose(scaled_weight, weight, atol=1e-5)
    disabled, _ = guard_skin_chroma(automatic, camera, person, strength=0, pale_boundaries=True)
    np.testing.assert_array_equal(disabled, automatic)


def test_guard_skips_no_person_and_dark_scenes_and_bounds_detector_fallback(monkeypatch, tmp_path):
    import hdrimg.phone_raw_wb as wb
    source, reference = tmp_path/'auto.tif', tmp_path/'camera.tif'
    scene = np.broadcast_to(colors([.055])[0], (24, 32, 3)).copy()
    camera = np.broadcast_to(colors([.09])[0], scene.shape).copy()
    tifffile.imwrite(source, scene, photometric='rgb')
    tifffile.imwrite(reference, camera, photometric='rgb')
    monkeypatch.setattr(wb, 'build_skin_context', lambda *a, **k:
        (Image.fromarray(np.zeros((24, 32), np.float32)), {'status': 'no_person'}))
    record, _ = preserve_raw_skin(source, reference, tmp_path/'none.tif')
    assert record['reason'] == 'no_person' and not (tmp_path/'none.tif').exists()
    monkeypatch.setattr(wb, 'build_skin_context', lambda *a, **k:
        (None, {'status': 'color_only_fallback', 'reason': 'test unavailable'}))
    record, _ = preserve_raw_skin(source, reference, tmp_path/'fallback.tif')
    assert record['person_detection']['status'] == 'color_only_fallback'
    expected, _ = guard_skin_chroma(scene, camera, .35, strength=1)
    np.testing.assert_allclose(tifffile.imread(tmp_path/'fallback.tif'), expected, atol=1e-7)
    tifffile.imwrite(source, scene * .001, photometric='rgb')
    record, context = preserve_raw_skin(source, reference, tmp_path/'dark.tif')
    assert record['reason'] == 'dark_scene' and context is None
    assert not (tmp_path/'dark.tif').exists()


@pytest.mark.parametrize('wb,guard', [(None, True), ('auto', True), ('camera', False), ('custom', False)])
@pytest.mark.parametrize('style', ['phone-clear', 'phone-natural'])
def test_pipeline_wb_reference_is_shared_by_denoise_and_color_passes(monkeypatch, tmp_path, wb, guard, style):
    source = tmp_path/'photo.CR2'
    source.write_bytes(b'fixture')
    monkeypatch.setattr(pipeline, 'resolve_tools', lambda **kw: ToolPaths(Path('raw'), Path('hdr'), Path('exif')))
    calls = []
    def develop(source, destination, *, white_balance, profile_overlay=None, **kwargs):
        calls.append((white_balance, profile_overlay.read_text() if profile_overlay else None))
        tifffile.imwrite(destination, np.full((24, 32, 3), .2, np.float32), photometric='rgb')
    monkeypatch.setattr(pipeline, 'develop_raw', develop)
    monkeypatch.setattr(pipeline, 'phone_denoise_decision', lambda *a, **kw: {'extra_denoise_weight': 0})
    def guard_scene(source, reference, destination, **kwargs):
        assert guard
        assert kwargs['pale_boundaries'] == (style == 'phone-natural')
        shutil.copy2(source, destination)
        return {'applied': True}, (None, {'status': 'color_only_fallback'})
    monkeypatch.setattr(pipeline, 'preserve_raw_skin', guard_scene)
    def capture(scene, *args, **kwargs):
        assert (scene.name == 'scene-raw-skin.tif') == guard
        assert (kwargs['_skin_context'] is not None) == (guard and style == 'phone-clear')
        raise ProcessingError('captured')
    monkeypatch.setattr(pipeline, 'render_pair', capture)
    with pytest.raises(ProcessingError, match='captured'):
        pipeline.render_raw(source, RenderOptions(output=tmp_path/'out', style=style, white_balance=wb,
            temperature_k=5600, surface_denoise_strength=0))
    expected = wb or 'auto'
    assert [c[0] for c in calls] == [expected] * 3 + (['camera'] if guard else [])
    if guard:
        assert calls[1][1] == calls[3][1]  # Matching final NR and sharpening.


def test_v6_never_reestimates_white_balance_after_raw(monkeypatch, tmp_path):
    source = tmp_path/'scene.tif'
    tifffile.imwrite(source, np.broadcast_to(colors([.08])[0], (32, 32, 3)), photometric='rgb')
    def forbidden(*args, **kwargs):
        raise AssertionError('duplicate white balance correction')
    monkeypatch.setattr(render, 'phone_illuminant_gains', forbidden)
    monkeypatch.setattr(render, 'phone_shadow_red_offset', forbidden)
    info = render.render_pair(source, tmp_path/'sdr.jpg', tmp_path/'hdr.raw',
        auto_exposure=True, exposure_ev=None, highlight_ev=0, hdr_strength=1,
        peak_nits=1000, skin_protection_strength=0, subject_adaptation_strength=0)
    assert info.style['algorithm_version'] == 6
    assert info.tone_mapping['phone_illuminant_strength'] == 0
    for channel in ['red', 'green', 'blue']:
        assert info.tone_mapping[f'phone_illuminant_{channel}_gain'] == 1
