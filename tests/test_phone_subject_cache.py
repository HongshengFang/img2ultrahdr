from pathlib import Path
import numpy as np
from PIL import Image
import hdrimg.phone_subject as module


def setup(monkeypatch,tmp_path,run):
    module._SUBJECT_CACHE.clear()
    monkeypatch.setattr(module,"_vision_helper",lambda:Path("/fake/vision"))
    monkeypatch.setattr(module,"_detect_subject_fields_uncached",run)
    source=tmp_path/"ref.jpg";source.write_bytes(b"reference-a")
    return source


def success():
    a=Image.fromarray(np.full((8,8),.9,np.float32),mode="F")
    return a,a.copy(),{"status":"applied","faces":[{"x":.2}]}


def call(source,**extra):
    args=dict(high_key_weight=0,strength=1,gamut="srgb");args.update(extra)
    return module.detect_subject_fields(source,**args)


def test_same_content_reuses_success_with_independent_results(tmp_path,monkeypatch):
    calls=[]
    def run(*a,**k):calls.append(1);return success()
    source=setup(monkeypatch,tmp_path,run);h,s,record=call(source)
    h.putpixel((0,0),4);record['faces'][0]['x']=9
    other=tmp_path/'other.jpg';other.write_bytes(source.read_bytes())
    h,s,record=call(other)
    assert len(calls)==1 and record['reference_cache_hit']
    assert h.getpixel((0,0))<1 and record['faces'][0]['x']==.2


def test_transient_failure_retries_and_records_recovery(tmp_path,monkeypatch):
    calls=[]
    def run(*a,**k):
        calls.append(1)
        return (None,None,{'status':'unavailable','reason':'temporary Vision failure'}) if len(calls)==1 else success()
    source=setup(monkeypatch,tmp_path,run);_,_,record=call(source)
    assert len(calls)==2 and record['status']=='applied' and record['helper_attempts']==2
    assert record['recovered_helper_errors']==['temporary Vision failure']


def test_repeated_failure_is_not_cached(tmp_path,monkeypatch):
    calls=[]
    def run(*a,**k):
        calls.append(1)
        return (None,None,{'status':'unavailable','reason':'failure'}) if len(calls)<=2 else success()
    source=setup(monkeypatch,tmp_path,run)
    assert call(source)[2]['status']=='unavailable'
    assert call(source)[2]['status']=='applied' and len(calls)==3


def test_reference_and_options_invalidate_cache_and_size_is_bounded(tmp_path,monkeypatch):
    calls=[]
    def run(*a,**k):calls.append(1);return success()
    source=setup(monkeypatch,tmp_path,run)
    call(source);call(source,high_key_weight=1)
    source.write_bytes(b'new-content');call(source)
    assert len(calls)==3
    for i in range(8):call(source,strength=i/10)
    assert len(module._SUBJECT_CACHE)==4


def test_unavailable_provider_cannot_reuse_a_previous_success(tmp_path,monkeypatch):
    source=setup(monkeypatch,tmp_path,lambda *a,**k:success());call(source)
    monkeypatch.setattr(module,'_vision_helper',lambda:None)
    assert call(source)[2]['status']=='unavailable'
