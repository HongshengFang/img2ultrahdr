from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from hdrimg.phone_subject import subject_tone_fields
from hdrimg.render import render_pair
from hdrimg.style import STYLE_PRESETS


def test_no_face_and_zero_strength_are_exact_noops():
    sdr=np.full((80,96,3),.3,dtype=np.float32)
    matte=np.ones((80,96),dtype=np.float32)
    face={"x":.4,"y":.2,"width":.2,"height":.2,"confidence":.9}
    for faces,strength in (([],1),([face],0)):
        hdr,odds,info=subject_tone_fields(sdr,matte,faces,high_key_weight=1,strength=strength,gamut="srgb")
        assert np.array_equal(hdr,np.ones_like(hdr))
        assert np.array_equal(odds,np.ones_like(odds))
        assert info["status"]=="no_reliable_subject"


def test_subject_fields_leave_background_alone_and_separate_clothes_from_head():
    sdr=np.full((160,120,3),.25,dtype=np.float32)
    matte=np.zeros((160,120),dtype=np.float32);matte[:,35:85]=1
    face={"x":.4,"y":.2,"width":.2,"height":.15,"confidence":.9}
    hdr,odds,info=subject_tone_fields(sdr,matte,[face],high_key_weight=1,strength=1,gamut="srgb")
    assert np.array_equal(hdr[:,0],np.ones(160))
    assert np.array_equal(odds[:,0],np.ones(160))
    assert hdr[44,60]<1
    assert hdr[130,60]>1
    assert np.all(np.isfinite(hdr)) and np.all(np.isfinite(odds))
    assert info["face_count"]==1


def test_duplicate_tile_faces_are_merged_and_unconfirmed_faces_are_ignored():
    sdr=np.full((160,120,3),.25,dtype=np.float32)
    matte=np.ones((160,120),dtype=np.float32)
    face={"x":.4,"y":.2,"width":.2,"height":.15,"confidence":.9}
    kwargs=dict(high_key_weight=1,strength=1,gamut="srgb")
    one=subject_tone_fields(sdr,matte,[face],**kwargs)
    two=subject_tone_fields(sdr,matte,[dict(face,confidence=.7),face],**kwargs)
    assert np.array_equal(one[0],two[0])
    assert two[2]["face_count"]==1
    absent=subject_tone_fields(sdr,np.zeros_like(matte),[face],**kwargs)
    assert absent[2]["status"]=="no_reliable_subject"
    assert np.array_equal(absent[0],np.ones_like(matte))


def test_subject_tone_keeps_hdr_independent_of_sdr_exposure(tmp_path:Path,monkeypatch):
    def fields(reference,**kwargs):
        with Image.open(reference) as im:
            shape=(im.height,im.width)
        hdr=Image.fromarray(np.full(shape,1.15,dtype=np.float32),mode="F")
        odds=Image.fromarray(np.full(shape,1.3,dtype=np.float32),mode="F")
        return hdr,odds,{"status":"test_subject"}
    monkeypatch.setattr("hdrimg.render.detect_subject_fields",fields)
    x=np.linspace(.18,.6,96,dtype=np.float32)
    scene=np.broadcast_to(x[None,:,None],(64,96,3)).copy()
    source=tmp_path/"scene.tif";tifffile.imwrite(source,scene,photometric="rgb")
    common=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,peak_nits=1000)
    render_pair(source,tmp_path/"a.jpg",tmp_path/"a.raw",**common)
    render_pair(source,tmp_path/"b.jpg",tmp_path/"b.raw",sdr_exposure_ev=-.45,**common)
    assert (tmp_path/"a.raw").read_bytes()==(tmp_path/"b.raw").read_bytes()
    assert (tmp_path/"a.jpg").read_bytes()!=(tmp_path/"b.jpg").read_bytes()


def test_unavailable_subject_helper_matches_explicit_off(tmp_path, monkeypatch):
    monkeypatch.setattr("hdrimg.phone_subject._vision_helper", lambda: None)
    source = tmp_path / "scene.tif"
    tifffile.imwrite(source, np.full((64, 96, 3), .3, dtype=np.float32), photometric="rgb")
    common = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0,
                  hdr_strength=1, peak_nits=1000)
    info = render_pair(source, tmp_path/"fallback.jpg", tmp_path/"fallback.raw", **common)
    render_pair(source, tmp_path/"off.jpg", tmp_path/"off.raw", subject_adaptation_strength=0, **common)
    assert info.tone_mapping["phone_subject"]["status"] == "unavailable"
    assert (tmp_path/"fallback.raw").read_bytes() == (tmp_path/"off.raw").read_bytes()
    assert (tmp_path/"fallback.jpg").read_bytes() == (tmp_path/"off.jpg").read_bytes()


def test_manual_look_and_legacy_styles_do_not_invoke_subject_helper(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Subject helper must not run on a manual or legacy look")
    monkeypatch.setattr("hdrimg.render.detect_subject_fields", forbidden)
    source = tmp_path / "scene.tif"
    tifffile.imwrite(source, np.full((48, 64, 3), .3, dtype=np.float32), photometric="rgb")
    common = dict(auto_exposure=True, exposure_ev=None, highlight_ev=0,
                  hdr_strength=1, peak_nits=1000)
    for i, extra in enumerate(({"contrast": 1.35, "saturation": 1.18},
                               {"style": STYLE_PRESETS["natural"]},
                               {"style": STYLE_PRESETS["phone-natural"]})):
        render_pair(source, tmp_path/f"{i}.jpg", tmp_path/f"{i}.raw", **common, **extra)


def test_extra_subject_correction_requires_bright_outdoor_face():
    matte = np.ones((160, 120), dtype=np.float32)
    face = {"x": .4, "y": .2, "width": .2, "height": .15, "confidence": .9}
    for brightness in [.12, .25, .4, .7]:
        sdr = np.full((160, 120, 3), brightness, dtype=np.float32)
        common = dict(high_key_weight=0, strength=1, gamut="srgb")
        outdoor = subject_tone_fields(sdr, matte, [face], indoor_weight=0, **common)
        indoor = subject_tone_fields(sdr, matte, [face], indoor_weight=1, **common)
        assert indoor[2]["face_excess_ev"] == 0
        if brightness <= .27:
            assert np.array_equal(outdoor[0], indoor[0])
        else:
            assert outdoor[0][44,60] < indoor[0][44,60]
            assert 0 < outdoor[2]["face_excess_ev"] <= .65
        assert np.all(np.isfinite(outdoor[0])) and np.all(outdoor[0] > 0)
