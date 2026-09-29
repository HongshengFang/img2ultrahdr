import numpy as np
from hdrimg.phone_deveil import deveil_fields, _minimum


def test_separable_minimum_and_disabled():
    x=np.arange(25,dtype=np.float32).reshape(5,5)
    assert _minimum(x,1)[2,2]==6
    rgb=np.full((40,50,3),.2,dtype=np.float32)
    h,s,_=deveil_fields(rgb,strength=0)
    assert np.all(np.asarray(h)==1) and np.all(np.asarray(s)==1)


def test_neutral_and_sky_protected():
    v=np.linspace(.1,.7,80,dtype=np.float32)[None,:,None]*np.ones((64,1,3),np.float32)
    for rgb in [v, v*np.array([.3,.6,1],np.float32)]:
        h,s,_=deveil_fields(rgb)
        assert np.max(np.abs(np.asarray(s)-1))<1e-5


def test_bounded_textured_material():
    rng=np.random.default_rng(4)
    rgb=(.5+.1*rng.normal(size=(64,80,1)))*np.array([1,.7,.4])
    h,s,r=deveil_fields(rgb)
    assert np.isfinite(np.asarray(h)).all()
    assert -.526 <= np.log2(np.asarray(s)).min() < -.01
    assert np.asarray(s).max() <= 1.000001


def test_gamut_validation_and_srgb_neutral():
    import pytest
    rgb=np.full((40,50,3),.4,np.float32)
    for value in (-1,1.1,float('nan')):
        with pytest.raises(ValueError):deveil_fields(rgb,strength=value)
    with pytest.raises(ValueError):deveil_fields(rgb,gamut='invalid')
    h,s,_=deveil_fields(rgb,gamut='srgb')
    assert np.allclose(np.asarray(s),1)


def test_head_protection_reduces_material_shift():
    rng=np.random.default_rng(3)
    rgb=(.5+.06*rng.normal(size=(80,80,1)))*np.array([1,.7,.4])
    _,s,_=deveil_fields(rgb,strength=1)
    _,p,_=deveil_fields(rgb,strength=1,faces=[dict(x=.3,y=.3,width=.4,height=.4)])
    assert np.asarray(p)[30:50,30:50].min()>.999
    assert np.asarray(s)[30:50,30:50].mean()<.95
