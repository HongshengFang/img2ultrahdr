from dataclasses import asdict, replace
from pathlib import Path
import json
import os
import subprocess
import sys
import time

import numpy as np
import pytest
import tifffile

from hdrimg.editor import EditRecipe, EditorStore, digest, atomic_json, check_source
from hdrimg.errors import InputError
from hdrimg.render import render_pair
from hdrimg.style import STYLE_PRESETS
from hdrimg.tone import apply_exposure_and_highlights


def test_shadow_curve_preserves_blacks_highlights_chroma_and_monotonicity():
    y = np.linspace(0, 2, 100001, dtype=np.float32)
    rgb = np.repeat(y[:, None, None], 3, axis=2)
    for ev in [-2, -1, 0, 1, 2]:
        result = apply_exposure_and_highlights(rgb, total_ev=0, highlight_ev=0, shadow_ev=ev)
        assert np.all(np.isfinite(result))
        assert np.min(np.diff(result[:, 0, 0])) >= 0
        assert np.array_equal(result[y >= .18], rgb[y >= .18])
        assert not result[0].any()
    colored = np.array([[[.01,.02,.03]]],np.float32)
    result = apply_exposure_and_highlights(colored,total_ev=0,highlight_ev=0,shadow_ev=2)
    np.testing.assert_allclose(result/colored, np.broadcast_to((result/colored)[..., :1],result.shape),rtol=1e-6)


@pytest.mark.parametrize('extra', [{'exposure_ev':4},{'shadow_ev':float('nan')},{'saturation':.5},
                                  {'temperature_k':5600.5},{'style':'natural'},{'schema_version':2}])
def test_edit_recipe_rejects_invalid_values(extra):
    with pytest.raises(InputError):
        EditRecipe.from_dict(extra)


def test_edit_cache_keys_ignore_remembered_custom_values_in_auto():
    a=EditRecipe();b=replace(a,temperature_k=9000,tint=40)
    assert a.development_key()==b.development_key()
    assert replace(a,white_balance='custom').development_key()!=replace(b,white_balance='custom').development_key()
    assert replace(a,exposure_ev=.5,shadow_ev=1).development_key()==a.development_key()
    assert a.raw_options(Path('.')).contrast is None
    assert a.raw_options(Path('.')).resolved_raw_skin_strength()==1
    assert a.raw_options(Path('.')).resolved_raw_denoise_strength()==1


@pytest.mark.parametrize('style',['phone-clear','phone-natural'])
def test_ui_adjustments_keep_automatic_protections_and_zero_is_identical(tmp_path,style):
    y=np.geomspace(.001,.5,64*96).reshape(64,96)
    scene=np.repeat(y[...,None],3,axis=2).astype(np.float32)
    tifffile.imwrite(tmp_path/'scene.tif',scene,photometric='rgb')
    opts=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,peak_nits=1000,
              development_ev=-2,style=STYLE_PRESETS[style],_skin_context=(None,{}))
    a=render_pair(tmp_path/'scene.tif',tmp_path/'a.jpg',tmp_path/'a.raw',**opts)
    b=render_pair(tmp_path/'scene.tif',tmp_path/'b.jpg',tmp_path/'b.raw',**opts,
                  edit_exposure_ev=0,shadow_ev=0,saturation_scale=1)
    assert (tmp_path/'a.jpg').read_bytes()==(tmp_path/'b.jpg').read_bytes()
    assert (tmp_path/'a.raw').read_bytes()==(tmp_path/'b.raw').read_bytes()
    c=render_pair(tmp_path/'scene.tif',tmp_path/'c.jpg',tmp_path/'c.raw',**opts,
                  edit_exposure_ev=.3,shadow_ev=.5,saturation_scale=1.1)
    assert c.look['contrast_source']==c.look['saturation_source']=='auto'
    assert c.tone_mapping['hdr_manual_highlight_bonus']==0
    assert c.look['exposure_ev']==pytest.approx(a.look['exposure_ev']+.3)
    assert c.look['saturation']==pytest.approx(a.look['saturation']*1.1)
    assert (tmp_path/'c.raw').read_bytes()!=(tmp_path/'a.raw').read_bytes()


def test_saved_recipe_reopens_by_content_and_detects_source_change(tmp_path,monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test-engine')
    source=tmp_path/'中文 photo.CR2';source.write_bytes(b'fake raw for identity only')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    identity,recipe,changed=store.restore(source)
    assert not changed
    recipe=replace(recipe,shadow_ev=.5)
    store.remember(identity,recipe)
    moved=tmp_path/'moved.CR2';source.rename(moved)
    assert store.restore(moved,identity['sha256'])[1]==recipe
    with pytest.raises(InputError): check_source(identity)
    moved.write_bytes(b'different')
    with pytest.raises(InputError): store.restore(moved,identity['sha256'])


def test_full_render_cache_is_not_published_on_failure(tmp_path,monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test-engine')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    source=tmp_path/'file.CR2';source.write_bytes(b'invalid raw')
    identity,recipe,_=store.restore(source)
    with pytest.raises(Exception): store.prepare(identity,recipe)
    assert not list((store.cache/'scenes').glob('*/complete.json'))


def test_export_checks_secondary_collision_before_render(tmp_path,monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test-engine')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    source=tmp_path/'file.CR2';source.write_bytes(b'raw')
    identity,recipe,_=store.restore(source)
    (tmp_path/'file_sdr.jpg').write_bytes(b'keep')
    monkeypatch.setattr(store,'render',lambda *a,**kw: pytest.fail('should not render'))
    with pytest.raises(InputError):
        store.export(identity,recipe,tmp_path/'file_ultrahdr.jpg',include_sdr=True)
    assert (tmp_path/'file_sdr.jpg').read_bytes()==b'keep'


def test_native_envelopes_match_numpy_reference(tmp_path,monkeypatch):
    import shutil
    if not shutil.which('clang'): pytest.skip('C compiler unavailable')
    from hdrimg import accelerator
    from hdrimg.phone_clear import edge_limited_exposure
    library=tmp_path/'accelerator.dylib'
    subprocess.run(['clang','-fblocks','-O3','-ffp-contract=off','-dynamiclib','native/editor_accelerator.c','-o',str(library)],check=True)
    rng=np.random.default_rng(73)
    for shape in [(1,17),(17,1),(48,75),(75,48)]:
        y=np.exp(rng.normal(-2,1,shape)).astype(np.float32)
        request=rng.uniform(-.5,.5,shape).astype(np.float32)
        monkeypatch.delenv('HDRIMG_ACCELERATOR',raising=False);accelerator.library.cache_clear()
        expected,record=edge_limited_exposure(y,request)
        monkeypatch.setenv('HDRIMG_ACCELERATOR',str(library));accelerator.library.cache_clear()
        actual,actual_record=edge_limited_exposure(y,request)
        np.testing.assert_array_equal(actual,expected)
        assert record == actual_record
    accelerator.library.cache_clear()


def test_native_color_math_matches_accelerate(tmp_path,monkeypatch):
    import shutil
    if not shutil.which('clang'): pytest.skip('C compiler unavailable')
    from hdrimg import accelerator, color
    library=tmp_path/'color.dylib'
    subprocess.run(['clang','-fblocks','-O3','-ffp-contract=off','-dynamiclib','native/editor_accelerator.c','-o',str(library)],check=True)
    monkeypatch.setenv('HDRIMG_ACCELERATOR',str(library));accelerator.library.cache_clear()
    rng=np.random.default_rng(92)
    matrices=[color._M1,color._M2,color._M1_INV,color._M2_INV,color.REC2020_TO_SRGB,color.REC2020_TO_DISPLAY_P3]
    for shape in [(17,3),(11,17,3),(512,63,3)]:
        pixels=rng.uniform(-2,6,shape).astype(np.float32)
        for matrix in matrices:
            np.testing.assert_array_equal(accelerator.matmul(pixels,matrix),pixels@matrix.T)
    accelerator.library.cache_clear()


def test_worker_can_cancel_immediately_without_waiting_for_raw(tmp_path):
    import selectors
    p=subprocess.Popen([sys.executable,'-m','hdrimg.worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    request={'command':'prepare','id':'slow','session_id':'s','revision':1,'source':str(Path('pics/DSCF8111.RAF').resolve()),
             'cache':str(tmp_path/'cache'),'support':str(tmp_path/'support')}
    started=time.monotonic()
    p.stdin.write(json.dumps(request)+'\n'+json.dumps({'command':'cancel','id':'cancel','session_id':'s','revision':2})+'\n');p.stdin.flush()
    select=selectors.DefaultSelector();select.register(p.stdout,selectors.EVENT_READ)
    events=[]
    try:
        while time.monotonic()-started<5:
            if select.select(.1):
                event=json.loads(p.stdout.readline());events.append(event)
                if event.get('id')=='cancel' and event.get('event')=='cancelled': break
        assert any(e.get('id')=='cancel' and e.get('event')=='cancelled' for e in events)
        assert time.monotonic()-started<5
    finally:
        p.stdin.write('{"command":"close"}\n');p.stdin.flush();p.communicate(timeout=5)
        select.close()
    assert not list((tmp_path/'cache/scenes').glob('.prepare-*'))


def test_packed_gamut_search_matches_reference(tmp_path,monkeypatch):
    from hdrimg import accelerator, color
    library=tmp_path/'gamut.dylib'
    subprocess.run(['clang','-fblocks','-O3','-ffp-contract=off','-dynamiclib','native/editor_accelerator.c','-o',str(library)],check=True)
    rng=np.random.default_rng(53)
    for shape in [(400,3),(32,68,3),(16,103,3),(8,1025,3),(5,1026,3),(5,1027,3)]:
        pixels=rng.uniform(-.02,1.2,shape).astype(np.float32)
        for target in ['srgb','display-p3','rec2020']:
            monkeypatch.delenv('HDRIMG_ACCELERATOR',raising=False);accelerator.library.cache_clear()
            expected=color.compress_gamut(pixels,target=target,upper=1)
            monkeypatch.setenv('HDRIMG_ACCELERATOR',str(library));accelerator.library.cache_clear()
            actual=color.compress_gamut(pixels,target=target,upper=1)
            np.testing.assert_array_equal(actual,expected)
    accelerator.library.cache_clear()


def test_newer_unrendered_draft_is_restored_and_engine_change_reported(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID', 'old')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    raw=tmp_path/'source.RAF';raw.write_bytes(b'identity only')
    source,recipe,_=store.restore(raw);store.remember(source,recipe)
    draft=replace(recipe,exposure_ev=.25)
    atomic_json(store.support/'drafts'/f'{source["sha256"]}.json',
                {'updated':time.time()+1,'recipe':asdict(draft)})
    store.engine='new'
    restored=store.restore(raw)
    assert restored[1]==draft and restored[2]


def test_export_failure_rolls_back_both_existing_files(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test-engine')
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    raw=tmp_path/'file.CR2';raw.write_bytes(b'original raw')
    source,recipe,_=store.restore(raw)
    output=tmp_path/'file_ultrahdr.jpg';sdr=tmp_path/'file_sdr.jpg'
    output.write_bytes(b'previous HDR');sdr.write_bytes(b'previous SDR')
    rendered=tmp_path/'render.jpg';rendered.write_bytes(b'new image')
    monkeypatch.setattr(store,'render',lambda *a,**kw:{'ultrahdr':str(rendered),'sdr':str(rendered)})
    original_replace=os.replace
    def fail_second(source,target):
        if str(source).endswith('/1.jpg'): raise OSError('disk failure')
        original_replace(source,target)
    monkeypatch.setattr(os,'replace',fail_second)
    with pytest.raises(OSError):store.export(source,recipe,output,include_sdr=True,overwrite=True)
    assert output.read_bytes()==b'previous HDR'
    assert sdr.read_bytes()==b'previous SDR'
    assert raw.read_bytes()==b'original raw'
    assert not list(tmp_path.glob('.img2uhdr-*'))


def test_pruning_retains_active_scene_and_newer_entries(tmp_path,monkeypatch):
    import hdrimg.editor as editor
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test-engine');monkeypatch.setattr(editor,'CACHE_LIMIT',15)
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    active=store.cache/'scenes'/'active';active.mkdir();(active/'data').write_bytes(b'1234567890')
    old=store.cache/'renders'/'old';old.mkdir();(old/'data').write_bytes(b'1234567890')
    store.prune({active})
    assert active.exists() and not old.exists()


def test_derived_masks_are_namespaced_by_engine_version(tmp_path, monkeypatch):
    paths = []
    for version in ('first-engine', 'updated-engine'):
        monkeypatch.setenv('HDRIMG_ENGINE_ID', version)
        store = EditorStore(cache=tmp_path/'cache', support=tmp_path/'support')
        paths.append(Path(os.environ['HDRIMG_DERIVED_CACHE']))
        assert paths[-1] == store.cache/'derived'/version
        assert paths[-1].is_dir()
    assert paths[0] != paths[1]


def test_relocated_raw_reuses_cache_but_reports_current_source(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID', 'test-engine')
    store = EditorStore(cache=tmp_path/'cache', support=tmp_path/'support')
    original = tmp_path/'original.CR2'; original.write_bytes(b'identity only')
    source, recipe, _ = store.restore(original)
    scene_key = digest({'source': source['sha256'], 'engine': store.engine,
                        'development': recipe.development_key()})
    render_key = digest({'scene': scene_key, 'recipe': asdict(recipe), 'full': False,
                         'strip_metadata': True})
    atomic_json(store.cache/'scenes'/scene_key/'complete.json', {'key': scene_key, 'source': source})
    atomic_json(store.cache/'renders'/render_key/'complete.json', {'key': render_key, 'source': source})
    relocated = tmp_path/'重新定位 photo.CR2'; original.rename(relocated)
    current, _, _ = store.restore(relocated, source['sha256'])
    assert store.prepare(current, recipe)['source'] == current
    cached = store.render(current, recipe)
    assert cached['cache_hit'] and cached['source'] == current


@pytest.mark.parametrize('style',['phone-clear','phone-natural'])
def test_fixed_analysis_matches_original_pixels_and_sdr_control_keeps_hdr(tmp_path,style):
    from hdrimg.tone import exposure_statistics
    from hdrimg.look import resolve_look
    rng=np.random.default_rng(43)
    scene=rng.uniform(.003,.2,(64,96,3)).astype(np.float32)
    source=tmp_path/'scene.tif';tifffile.imwrite(source,scene,photometric='rgb')
    stats=exposure_statistics(scene,auto_exposure=True,exposure_ev=0,development_ev=-2)
    look=resolve_look(scene,base_scene_adjustment_ev=stats.scene_adjustment_ev,
                      enabled=True,auto_exposure=True,exposure_ev=None,contrast=None,saturation=None)
    np.save(tmp_path/'analysis.npy',scene)
    analysis={'base_stats':asdict(stats),'look':asdict(look),'sample':str(tmp_path/'analysis.npy')}
    opts=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,peak_nits=1000,
              development_ev=-2,style=STYLE_PRESETS[style],_skin_context=(None,{}))
    render_pair(source,tmp_path/'a.jpg',tmp_path/'a.raw',**opts)
    render_pair(source,tmp_path/'b.jpg',tmp_path/'b.raw',**opts,_analysis=analysis)
    assert (tmp_path/'a.jpg').read_bytes()==(tmp_path/'b.jpg').read_bytes()
    assert (tmp_path/'a.raw').read_bytes()==(tmp_path/'b.raw').read_bytes()
    render_pair(source,tmp_path/'c.jpg',tmp_path/'c.raw',**opts,_analysis=analysis,
                sdr_exposure_ev=-.5,edit_exposure_ev=.2,shadow_ev=.4,saturation_scale=.95)
    render_pair(source,tmp_path/'d.jpg',tmp_path/'d.raw',**opts,_analysis=analysis,
                edit_exposure_ev=.2,shadow_ev=.4,saturation_scale=.95)
    assert (tmp_path/'c.raw').read_bytes()==(tmp_path/'d.raw').read_bytes()
    assert (tmp_path/'c.jpg').read_bytes()!=(tmp_path/'d.jpg').read_bytes()


def test_scene_cache_reuses_raw_for_adjustments_but_invalidates_white_balance(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import hdrimg.editor as editor
    monkeypatch.setenv('HDRIMG_ENGINE_ID','test-engine')
    # Use a real linear ICC profile but a tiny synthetic RAW development.
    store=EditorStore(cache=tmp_path/'cache',support=tmp_path/'support')
    raw=tmp_path/'file.CR2';raw.write_bytes(b'identity')
    source,recipe,_=store.restore(raw)
    from importlib.resources import files
    import base64
    icc=base64.b64decode(files('hdrimg').joinpath('profiles/Rec2020-elle-V4-g10.icc.b64').read_text())
    calls=[]
    def prepare(source,options,*,work,**kw):
        calls.append(options.white_balance)
        scene=work/'scene.tif'
        editor.write_scene(scene,np.full((32,48,3),.1,np.float32),icc)
        return SimpleNamespace(scene=scene,scene_decision=None,skin_context=None,raw_development={})
    monkeypatch.setattr(editor,'prepare_raw_scene',prepare)
    monkeypatch.setattr(editor,'build_skin_context',lambda *a,**kw:(None,{}))
    monkeypatch.setattr(editor,'read_source_metadata',lambda *a:{})
    first=store.prepare(source,recipe)
    second=store.prepare(source,replace(recipe,exposure_ev=.5,shadow_ev=1,saturation=1.1))
    third=store.prepare(source,replace(recipe,white_balance='custom'))
    assert first['key']==second['key'] and first['key']!=third['key']
    assert calls==['auto','custom']


def test_worker_process_group_stops_if_controller_crashes(tmp_path):
    import selectors,signal
    p=subprocess.Popen([sys.executable,'-m','hdrimg.worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    request={'command':'prepare','id':'slow','session_id':'s','revision':1,
             'source':str(Path('pics/DSCF8111.RAF').resolve()),'cache':str(tmp_path/'cache'),'support':str(tmp_path/'support')}
    if not Path(request['source']).is_file():
        p.terminate();p.communicate(timeout=5);pytest.skip('local RAW samples unavailable')
    p.stdin.write(json.dumps(request)+'\n');p.stdin.flush()
    selector=selectors.DefaultSelector();selector.register(p.stdout,selectors.EVENT_READ)
    child=None;deadline=time.monotonic()+20
    try:
        while time.monotonic()<deadline:
            if not selector.select(.1):continue
            event=json.loads(p.stdout.readline())
            if event.get('event')=='progress' and event.get('phase')=='正在显影 RAW':
                ps=subprocess.check_output(['ps','-axo','pid=,ppid=,pgid='],text=True)
                child=next(int(f[2]) for line in ps.splitlines() if len(f:=line.split())==3 and int(f[1])==p.pid)
                break
        assert child is not None
        p.kill();p.wait(timeout=2)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            ps=subprocess.check_output(['ps','-axo','pgid=,stat='],text=True)
            alive=[f for line in ps.splitlines() if len(f:=line.split())==2 and int(f[0])==child and not f[1].startswith('Z')]
            if not alive:break
            time.sleep(.1)
        assert not alive
    finally:
        if p.poll() is None:p.kill()
        p.communicate(timeout=5);selector.close()
        if child:
            try:os.killpg(child,signal.SIGKILL)
            except ProcessLookupError:pass


def test_zombie_only_process_group_cleanup_is_benign_but_live_refusal_is_not(monkeypatch):
    from hdrimg.worker import signal_task_group
    import signal
    def denied(*args):raise PermissionError('group has no signalable process')
    monkeypatch.setattr(os,'killpg',denied)
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**kw:'123 Z\n124 S\n')
    signal_task_group(123,signal.SIGKILL)
    with pytest.raises(PermissionError):signal_task_group(124,signal.SIGKILL)


def test_explicit_policy_controls_protection_independently_of_parameter_source(tmp_path):
    from hdrimg.render import RenderPolicy
    scene=np.full((48,64,3),.08,np.float32)
    tifffile.imwrite(tmp_path/'scene.tif',scene,photometric='rgb')
    info=render_pair(tmp_path/'scene.tif',tmp_path/'sdr.jpg',tmp_path/'hdr.raw',
                    auto_exposure=True,exposure_ev=.5,contrast=1.25,saturation=1.1,
                    highlight_ev=0,hdr_strength=1,peak_nits=1000,
                    style=STYLE_PRESETS['phone-clear'],development_ev=-2,
                    _skin_context=(None,{}),_processing_policy=RenderPolicy())
    assert info.look['contrast_source']==info.look['saturation_source']=='manual'
    assert info.tone_mapping['phone_neutral_protection']==1
    assert info.tone_mapping['phone_skin']['strength']==1
    assert info.tone_mapping['hdr_manual_highlight_bonus']==0
