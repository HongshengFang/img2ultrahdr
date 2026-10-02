from pathlib import Path
import json
import os
import numpy as np
import pytest
import tifffile
from hdrimg.render import render_pair, _quantize_sdr
from hdrimg.style import STYLE_PRESETS
from hdrimg.editor import EditorStore, atomic_json


@pytest.mark.parametrize('value', [-1., float('nan'), float('inf')])
def test_packet_with_no_valid_luminance_still_has_finite_quantiles(tmp_path, value):
    from hdrimg.editor import EditRecipe
    from hdrimg.preview import make_packet
    scene = np.full((4, 4, 3), value, np.float32)
    source = tmp_path / 'scene.tif'
    tifffile.imwrite(source, scene, photometric='rgb')
    sample = tmp_path / 'sample.npy'
    np.save(sample, scene)
    packet = make_packet(tmp_path, tmp_path,
                         {'preview': str(source), 'analysis': {'sample': str(sample)}},
                         EditRecipe(), {'tone_mapping': {}, 'exposure': {}, 'look': {}})
    assert packet['luminance_quantiles'] == [0.] * 1025
    json.dumps(packet, allow_nan=False)


def test_analysis_reference_skips_only_its_unused_hdr_twin(tmp_path):
    scene = np.geomspace(.00001, 4, 96*64*3).reshape(64,96,3).astype('float32')
    tifffile.imwrite(tmp_path/'scene.tif', scene, photometric='rgb')
    options = dict(auto_exposure=True, exposure_ev=None, highlight_ev=.2, shadow_ev=.3,
        hdr_strength=.8, peak_nits=1000, development_ev=-2, style=STYLE_PRESETS['phone-clear'],
        _allow_subject=False, _allow_histogram=False, subject_adaptation_strength=0,
        _skin_context=(None, {}))
    render_pair(tmp_path/'scene.tif', tmp_path/'before.jpg', tmp_path/'before.raw',
        _linear_sdr_output=tmp_path/'before.tif', **options)
    result = render_pair(tmp_path/'scene.tif', tmp_path/'after.jpg', tmp_path/'unused.raw',
        _linear_sdr_output=tmp_path/'after.tif', _reference_sdr_only=True, **options)
    assert result is None and not (tmp_path/'unused.raw').exists()
    assert (tmp_path/'before.jpg').read_bytes() == (tmp_path/'after.jpg').read_bytes()
    assert np.array_equal(tifffile.imread(tmp_path/'before.tif'), tifffile.imread(tmp_path/'after.tif'))


@pytest.mark.parametrize('style', ['phone-clear', 'phone-natural'])
def test_float_packet_capture_does_not_change_exact_render(tmp_path, style):
    scene = np.geomspace(.00001, 2, 96*64*3).reshape(64,96,3).astype('float32')
    tifffile.imwrite(tmp_path/'scene.tif', scene, photometric='rgb')
    options = dict(auto_exposure=True, exposure_ev=None, highlight_ev=.2,
        shadow_ev=.25, hdr_strength=.8, peak_nits=1000, development_ev=-2,
        style=STYLE_PRESETS[style], _skin_context=(None, {}))
    render_pair(tmp_path/'scene.tif', tmp_path/'a.jpg', tmp_path/'a.raw', **options)
    render_pair(tmp_path/'scene.tif', tmp_path/'b.jpg', tmp_path/'b.raw',
        _preview_output=tmp_path/'sdr.raw', **options)
    assert (tmp_path/'a.jpg').read_bytes() == (tmp_path/'b.jpg').read_bytes()
    assert (tmp_path/'a.raw').read_bytes() == (tmp_path/'b.raw').read_bytes()
    render_pair(tmp_path/'scene.tif', tmp_path/'unused.jpg', tmp_path/'c.raw',
        _preview_output=tmp_path/'sdr2.raw', _skip_sdr_jpeg=True, **options)
    assert not (tmp_path/'unused.jpg').exists()
    assert (tmp_path/'b.raw').read_bytes() == (tmp_path/'c.raw').read_bytes()
    assert (tmp_path/'sdr.raw').read_bytes() == (tmp_path/'sdr2.raw').read_bytes()
    pixels = np.fromfile(tmp_path/'sdr.raw', dtype='<f2').reshape(64,96,4)
    assert np.isfinite(pixels).all() and np.all(pixels[...,3] == 1)


def test_native_lease_prevents_cache_eviction(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID', 'test')
    monkeypatch.setattr('hdrimg.editor.CACHE_LIMIT', 1)
    store = EditorStore(cache=tmp_path/'cache', support=tmp_path/'support')
    active=store.cache/'renders/active';active.mkdir();(active/'pixels').write_bytes(b'active')
    inactive=store.cache/'renders/inactive';inactive.mkdir();(inactive/'pixels').write_bytes(b'expired')
    atomic_json(store.cache/'leases/native.json', {'pid':os.getpid(), 'paths':[str(active)]})
    store.prune()
    assert active.exists() and not inactive.exists()


def test_protocol_errors_are_stable_translatable_codes():
    from hdrimg.worker import error_code
    from hdrimg.errors import DependencyError, DiskSpaceError, InputError
    assert error_code(DependencyError('localized detail')) == 'missing_dependency'
    assert error_code(DiskSpaceError('space')) == 'disk_full'
    assert error_code(OSError(28, 'space')) == 'disk_full'
    assert error_code(PermissionError('directory')) == 'permission_denied'
    assert error_code(InputError('corrupt RAW')) == 'invalid_input'
