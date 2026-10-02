from dataclasses import asdict, replace
import hashlib
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest
import tifffile

from hdrimg.editor import EditRecipe, EditorStore, atomic_json
from hdrimg.errors import InputError, ProcessingError
from hdrimg.local_adjustments import LocalAdjustment, adjust_rgb, field_rows, resolve_masks
from hdrimg.render import render_pair
from hdrimg.style import STYLE_PRESETS


def test_old_recipe_migrates_without_changing_global_settings():
    old = {'schema_version': 1, 'exposure_ev': .3, 'white_ev': .2}
    value = EditRecipe.from_dict(old)
    assert value.schema_version == 2 and not value.local_adjustments
    assert value.exposure_ev == .3 and value.white_ev == .2
    region = LocalAdjustment('one', amount=.7)
    value = replace(value, local_adjustments=(region,))
    assert EditRecipe.from_dict(asdict(value)) == value
    assert value.development_key() == replace(value, local_adjustments=()).development_key()


@pytest.mark.parametrize('change', [{'amount': 2}, {'amount': float('nan')}, {'center_x': -.1},
    {'radius_y': 0}, {'enabled': 1}, {'mask_ref': '../outside'}, {'mode': 'sky'}, {'id': 'bad/path'}])
def test_invalid_regions_are_rejected(change):
    with pytest.raises(InputError):
        EditRecipe.from_dict({'local_adjustments': [{**asdict(LocalAdjustment('one')), **change}]})


def test_region_limit_and_duplicate_ids():
    eight = tuple(LocalAdjustment(str(i)) for i in range(8))
    replace(EditRecipe(), local_adjustments=eight).validate()
    for regions in [eight+(LocalAdjustment('nine'),), eight+(eight[0],), (eight[0], eight[0])]:
        with pytest.raises(InputError): replace(EditRecipe(), local_adjustments=regions).validate()


def test_composition_is_order_independent_bounded_and_cancels():
    regions = tuple(LocalAdjustment(str(i), amount=1) for i in range(8))
    field = field_rows(regions, {}, 100, 80, 0, 80)
    assert field.min() >= 0 and field.max() == .5
    assert np.array_equal(field, field_rows(regions[::-1], {}, 100, 80, 0, 80))
    pair = (regions[0], replace(regions[0], id='dark', direction='darken'))
    assert not field_rows(pair, {}, 100, 80, 0, 80).any()
    rgb = np.random.default_rng(8).random((80,100,3), dtype=np.float32)
    zero = field_rows(pair, {}, 100, 80, 0, 80)
    assert np.array_equal(adjust_rgb(rgb, zero, 1), rgb)


@pytest.mark.parametrize('upper', [1, 1000/203])
@pytest.mark.parametrize('ev', [-.5, .25, .5])
def test_lighting_preserves_color_black_and_highlight_headroom(upper, ev):
    y = np.linspace(0, upper, 1001, dtype=np.float32)
    rgb = y[:,None,None]*np.array([1,.6,.3], np.float32)
    result = adjust_rgb(rgb, np.full((1001,1), ev, np.float32), upper)
    assert np.isfinite(result).all() and not result[0].any()
    assert result.max() <= upper and np.min(np.diff(result[:,0,0])) >= -1e-6
    if ev < 0: assert np.all(result <= rgb) and result[-1,0,0] < upper
    else: assert np.all(result >= rgb) and result[-1,0,0] == upper
    np.testing.assert_allclose(result[1:,:,1]/result[1:,:,0], .6, atol=2e-7)


def test_rotated_ellipse_and_block_boundaries():
    r = LocalAdjustment('one', center_x=.22, center_y=.73, radius_x=.12, radius_y=.26, rotation=47, amount=1)
    whole = field_rows((r,), {}, 133, 97, 0, 97)
    blocked = np.concatenate([field_rows((r,), {}, 133,97,s,min(s+19,97)) for s in range(0,97,19)])
    assert np.array_equal(whole, blocked)
    assert whole[70,29] > .49 and whole[0,0] == 0


def save_mask(support, sha, values):
    import io
    data = io.BytesIO();Image.fromarray(np.asarray(values, np.uint16)).save(data,format='PNG')
    checksum = hashlib.sha256(data.getvalue()).hexdigest()
    root = support/'selection-assets'/sha;root.mkdir(parents=True, exist_ok=True)
    path = root/(checksum+'.png');path.write_bytes(data.getvalue())
    return checksum,path


def test_smart_asset_survives_cache_pruning_and_detects_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID','local-test')
    store = EditorStore(cache=tmp_path/'cache', support=tmp_path/'support')
    ref,path = save_mask(store.support,'source',np.full((8,12),65535))
    region = LocalAdjustment('one',mode='smart',mask_ref=ref,amount=.5)
    store.prune()
    assert np.all(resolve_masks((region,),store.support,'source')['one']==1)
    with pytest.raises(ProcessingError): resolve_masks((region,),store.support,'other-photo')
    path.write_bytes(b'corrupted')
    with pytest.raises(ProcessingError): resolve_masks((region,),store.support,'source')
    assert not resolve_masks((replace(region,amount=0),),store.support,'source')


@pytest.mark.parametrize('style', ['phone-clear','phone-natural'])
def test_zero_regions_are_bitwise_identical_and_nonzero_is_local(tmp_path, style):
    scene = np.full((64,96,3), [.1,.08,.05],np.float32)
    tifffile.imwrite(tmp_path/'scene.tif',scene,photometric='rgb')
    options = dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,
        peak_nits=1000,style=STYLE_PRESETS[style],_skin_context=(None,{}),
        _allow_subject=False,_allow_histogram=False)
    r=LocalAdjustment('one',radius_x=.1,radius_y=.15)
    def run(name, regions=()):
        return render_pair(tmp_path/'scene.tif',tmp_path/(name+'.jpg'),tmp_path/(name+'.raw'),
            _preview_output=tmp_path/(name+'.sdr'),_local_adjustments=regions,**options)
    run('base');run('zero',(r,))
    for suffix in ['jpg','raw','sdr']: assert (tmp_path/('base.'+suffix)).read_bytes()==(tmp_path/('zero.'+suffix)).read_bytes()
    changed=replace(r,amount=.6)
    run('edited',(changed,))
    field=field_rows((changed,),{},96,64,0,64)
    for suffix in ['raw','sdr']:
        before=np.fromfile(tmp_path/('base.'+suffix),dtype='<f2').reshape(64,96,4)
        after=np.fromfile(tmp_path/('edited.'+suffix),dtype='<f2').reshape(64,96,4)
        assert np.array_equal(before[field==0],after[field==0])
        assert np.any(after[field>.1,:3] > before[field>.1,:3])


@pytest.mark.parametrize('style', ['phone-clear','phone-natural'])
def test_manual_hdr_remains_independent_of_sdr_exposure(tmp_path, style):
    tifffile.imwrite(tmp_path/'scene.tif',np.full((32,48,3),.12,np.float32),photometric='rgb')
    for i,sdr_ev in enumerate([0,-.5]):
        render_pair(tmp_path/'scene.tif',tmp_path/f'{i}.jpg',tmp_path/f'{i}.raw',
            auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=.8,peak_nits=1000,
            sdr_exposure_ev=sdr_ev,style=STYLE_PRESETS[style],_skin_context=(None,{}),
            _allow_subject=False,_allow_histogram=False,_local_adjustments=(LocalAdjustment('one',amount=.5),))
    assert (tmp_path/'0.raw').read_bytes()==(tmp_path/'1.raw').read_bytes()
    assert (tmp_path/'0.jpg').read_bytes()!=(tmp_path/'1.jpg').read_bytes()


def test_cached_render_cannot_hide_a_missing_active_asset(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    raw=tmp_path/'photo.CR2';raw.write_bytes(b'RAW fingerprint')
    source,_,_=store.restore(raw)
    recipe=replace(EditRecipe(),local_adjustments=(LocalAdjustment('one',mode='smart',mask_ref='a'*64,amount=.5),))
    monkeypatch.setattr(store,'prepare',lambda *args:pytest.fail('must fail before cached/uncached rendering'))
    with pytest.raises(ProcessingError): store.render(source,recipe)


@pytest.mark.parametrize('failure', ['unavailable','timeout'])
def test_selection_failures_fall_back_without_saving_recipe(tmp_path, monkeypatch, failure):
    import hdrimg.local_selection as selection
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    rgba=np.ones((4,4,4),'<f2')*.1;rgba.tofile(tmp_path/'sdr.raw')
    monkeypatch.setattr(store,'render',lambda *args,**kwargs:{'key':'reference','preview_packet':{'width':4,'height':4,'sdr':str(tmp_path/'sdr.raw')}})
    monkeypatch.setattr(selection,'_helper',lambda *_:None if failure=='unavailable' else Path('/helper'))
    if failure=='timeout':
        def timeout(*args,**kwargs): raise subprocess.TimeoutExpired('helper',8)
        monkeypatch.setattr(selection.subprocess,'run',timeout)
    result=store.select_region({'sha256':'source'},EditRecipe(),[.5,.5])
    assert result['mode']=='soft' and result['reason']==failure
    assert not (store.support/'edits').exists()


def test_cached_smart_click_has_coverage_background_falls_back_and_never_saves(tmp_path, monkeypatch):
    import hdrimg.local_selection as selection
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    rgba=np.ones((8,12,4),'<f2')*.1;rgba.tofile(tmp_path/'base.raw')
    packet={'width':12,'height':8,'sdr':str(tmp_path/'base.raw')}
    monkeypatch.setattr(store,'render',lambda *args,**kwargs:{'preview_packet':packet})
    from hdrimg.render import _quantize_sdr
    reference=_quantize_sdr(rgba[...,:3].astype(np.float32))
    key=hashlib.sha256(reference.tobytes()+str(reference.shape).encode()+b'local-selection-2').hexdigest()
    root=store.cache/'derived'/store.engine/('selection-'+key);root.mkdir(parents=True)
    atomic_json(root/'complete.json',{'instances':[1]})
    labels=np.zeros((8,12),np.uint16);labels[2:6,3:9]=1
    Image.fromarray(labels).save(root/'labels.png')
    Image.fromarray(labels*65535).save(root/'1.png')
    result=store.select_region({'sha256':'photo'},EditRecipe(),[.5,.5])
    assert result['mode']=='smart'
    region=LocalAdjustment('one',mode='smart',mask_ref=result['mask_ref'],amount=.5)
    assert resolve_masks((region,),store.support,'photo')['one'][4,6]==1
    asset=store.support/'selection-assets'/'photo'/(result['mask_ref']+'.png')
    asset.write_bytes(b'damaged saved selection')
    repaired=store.select_region({'sha256':'photo'},EditRecipe(),[.5,.5])
    assert repaired['mask_ref']==result['mask_ref']
    assert resolve_masks((region,),store.support,'photo')['one'][4,6]==1
    assert store.select_region({'sha256':'photo'},EditRecipe(),[.01,.01])['mode']=='soft'
    Image.fromarray(np.zeros_like(labels)).save(root/'1.png')
    assert store.select_region({'sha256':'photo'},EditRecipe(),[.5,.5])['mode']=='soft'
    assert not (store.support/'edits').exists()


def test_selection_reuses_current_exact_base_without_manual_effects(tmp_path, monkeypatch):
    from hdrimg.local_selection import reference_frame
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    base=tmp_path/'base.raw';base.write_bytes(b'base frame')
    recipe=replace(EditRecipe(),exposure_ev=.2)
    with_locals=replace(recipe,local_adjustments=(LocalAdjustment('one',amount=.5),))
    packet={'version':2,'base_sdr':str(base)}
    atomic_json(store.cache/'renders'/'current'/'complete.json',{'engine':store.engine,'source':{'sha256':'photo'},'recipe':asdict(with_locals),'preview_packet':packet})
    monkeypatch.setattr(store,'render',lambda *args,**kwargs:pytest.fail('no RAW, JPEG or rendering during selection'))
    assert reference_frame(store,{'sha256':'photo'},recipe)==packet


def test_worker_emits_nested_local_recipe_and_v2_packet(tmp_path, monkeypatch, capsys):
    from hdrimg import worker
    recipe=replace(EditRecipe(),local_adjustments=(LocalAdjustment('one',amount=.5),))
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test')
    monkeypatch.setattr(EditorStore,'restore',lambda *args:({'sha256':'photo'},recipe,False))
    monkeypatch.setattr(EditorStore,'remember',lambda *args:None)
    monkeypatch.setattr(EditorStore,'render',lambda *args,**kwargs:{'recipe':asdict(recipe),'preview_packet':{'version':kwargs['preview_version']}})
    worker.job({'command':'preview','id':'request','source':'photo.CR2','recipe':asdict(recipe),'preview_format':'float_v2','cache':str(tmp_path/'cache'),'support':str(tmp_path/'support')})
    import json
    events=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]['event']=='opened' and events[0]['recipe']['local_adjustments'][0]['id']=='one'
    assert events[-1]['result']['preview_packet']['version']==2
