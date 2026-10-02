"""Fault injection for restart recovery and disposable cache damage."""
from dataclasses import asdict, replace
from types import SimpleNamespace
import base64
from importlib.resources import files
import json

import numpy as np
import pytest

from hdrimg.editor import EditorStore, EditRecipe, atomic_json


def test_concurrent_atomic_records_never_share_scratch_files(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    destination=tmp_path/'shared.json'
    def publish(index):
        for sequence in range(30):
            atomic_json(destination, {'writer':index, 'sequence':sequence, 'payload':'界'*1000})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(publish, range(8)))
    record=json.loads(destination.read_text())
    assert 0<=record['writer']<8 and record['sequence']==29 and record['payload']=='界'*1000
    assert list(tmp_path.iterdir())==[destination]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('HDRIMG_ENGINE_ID', 'stress-recovery')
    result = EditorStore(cache=tmp_path/'cache', support=tmp_path/'support')
    raw = tmp_path/'中文 strange name.RAF'
    raw.write_bytes(b'identity fixture, not a developed RAW')
    source, _, _ = result.restore(raw)
    return result, raw, source


@pytest.mark.parametrize('damage', ['{truncated', '[]', 'null', '{"recipe":{}}',
                                  '{"updated":"yesterday","recipe":{}}',
                                  '{"updated":9999999999,"recipe":{"exposure_ev":999}}'])
def test_bad_draft_preserves_last_valid_edit_and_original(store, damage):
    backend, raw, source = store
    recipe = replace(EditRecipe(), exposure_ev=.35, shadow_ev=.6)
    backend.remember(source, recipe)
    draft = backend.support/'drafts'/(source['sha256']+'.json')
    draft.parent.mkdir()
    draft.write_text(damage)
    assert backend.restore(raw)[1] == recipe
    assert backend.restore_warning
    assert list(draft.parent.glob(draft.name+'.corrupt-*'))
    assert raw.read_bytes() == b'identity fixture, not a developed RAW'


@pytest.mark.parametrize('damage', ['{truncated', '[]', 'null', '{"recipe":{"style":"lost-style"}}'])
def test_bad_saved_edit_recovers_valid_draft(store, damage):
    backend, raw, source = store
    backend.remember(source, EditRecipe())
    edit = backend.support/'edits'/(source['sha256']+'.json')
    edit.write_text(damage)
    recipe = replace(EditRecipe(), highlight_ev=-1.4)
    atomic_json(backend.support/'drafts'/(source['sha256']+'.json'),
                {'updated':100, 'recipe':asdict(recipe)})
    assert backend.restore(raw)[1] == recipe
    assert backend.restore_warning
    assert list(edit.parent.glob(edit.name+'.corrupt-*'))


@pytest.mark.parametrize('damage', ['{broken', '[]', 'null',
                                  '{"engine":"stress-recovery","ok":true}',
                                  '{"engine":"stress-recovery","ok":true,"checks":"invalid"}'])
def test_bad_doctor_cache_rechecks_instead_of_blocking_start(store, damage, monkeypatch, capsys):
    from hdrimg import doctor, worker
    backend, _, _ = store
    stamp = backend.support/'doctor.json'
    stamp.write_text(damage)
    calls=[]
    def check():
        calls.append(1)
        return True, [doctor.Check('fixture dependency', True, 'verified')]
    monkeypatch.setattr(doctor, 'run_doctor', check)
    worker.job({'command':'hello', 'id':'recover', 'cache':str(backend.cache), 'support':str(backend.support)})
    events=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert calls == [1]
    assert events[-1]['event'] == 'result'
    assert json.loads(stamp.read_text())['checks'][0]['ok'] is True


@pytest.mark.parametrize('damage', ['record', 'preview', 'scene', 'analysis', 'person'])
def test_bad_prepared_cache_rebuilds_once(store, damage, monkeypatch):
    import hdrimg.editor as editor
    backend, _, source = store
    icc=base64.b64decode(files('hdrimg').joinpath('profiles/Rec2020-elle-V4-g10.icc.b64').read_text())
    calls=[]
    def develop(source, options, *, work, **kwargs):
        calls.append(1)
        scene=work/'scene.tif'
        editor.write_scene(scene, np.full((32,48,3), .1, np.float32), icc)
        return SimpleNamespace(scene=scene, scene_decision=None, raw_development={})
    monkeypatch.setattr(editor, 'prepare_raw_scene', develop)
    monkeypatch.setattr(editor, 'read_source_metadata', lambda *a: {})
    monkeypatch.setattr(editor, 'build_skin_context', lambda *a,**k:(np.ones((32,48), np.float32),{}))
    original=backend.prepare(source, EditRecipe())
    if damage == 'record':
        (backend.cache/'scenes'/original['key']/'complete.json').write_text('{partial')
    else:
        path=original['analysis']['sample'] if damage=='analysis' else original[damage]
        from pathlib import Path
        Path(path).write_bytes(b'truncated')
    repaired=backend.prepare(source, EditRecipe())
    assert len(calls)==2
    assert repaired['key']==original['key']
    backend.prepare(source, EditRecipe())
    assert len(calls)==2
