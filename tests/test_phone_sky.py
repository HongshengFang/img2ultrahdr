import numpy as np
from hdrimg.phone_sky import sky_fields,_top_connected


def test_connected_isolated_patch_and_wall():
    mask=np.zeros((12,16),bool);mask[:4,:]=True;mask[8:10,4:9]=True
    out=_top_connected(mask)
    assert out[:4].all() and not out[4:].any()


def test_neutral_disabled_and_strong_blue_unchanged():
    for rgb in [np.full((40,60,3),.4),np.full((40,60,3),[.02,.1,.7])]:
        h,s,_=sky_fields(rgb)
        assert np.max(np.abs(np.asarray(s)-1))<1e-5
    h,s,_=sky_fields(np.full((40,60,3),[.25,.35,.5]),strength=0)
    assert np.all(np.asarray(s)==1)


def test_pale_sky_lift_is_bounded_and_isolated_clothing_unchanged():
    rgb=np.full((100,80,3),[.3,.2,.1],np.float32);rgb[:25]=[.25,.35,.5];rgb[70:85,20:60]=[.25,.35,.5]
    h,s,_=sky_fields(rgb)
    assert np.asarray(s)[:20].mean()>1.1
    assert np.max(np.log2(np.asarray(s)))<=.501
    assert np.allclose(np.asarray(s)[73:82,24:56],1)


def test_neutral_cloud_receives_same_budget_as_blue_gaps():
    rgb=np.full((100,100,3),[.32,.40,.53],np.float32)
    rgb[10:25,35:65]=[.6,.61,.63]
    _,s,_=sky_fields(rgb)
    values=np.asarray(s)
    assert abs(values[15,50]-values[15,15])<.002


def test_saturated_sky_blocks_pale_horizon_lift():
    rgb=np.full((100,100,3),[.03,.2,.6],np.float32)
    rgb[40:55]=[.4,.5,.6]
    _,s,_=sky_fields(rgb)
    assert np.max(np.abs(np.asarray(s)-1))<1e-5


def test_invalid_sky_parameters_and_srgb_support():
    import pytest
    rgb=np.full((40,60,3),[.25,.35,.5])
    for strength in (-.1,1.1,float('nan')):
        with pytest.raises(ValueError):sky_fields(rgb,strength=strength)
    with pytest.raises(ValueError):sky_fields(rgb,gamut='unknown')
    h,s,_=sky_fields(rgb,gamut='srgb')
    assert np.isfinite(np.asarray(h)).all() and np.isfinite(np.asarray(s)).all()
