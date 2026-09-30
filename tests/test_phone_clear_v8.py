from dataclasses import replace
import numpy as np
import pytest
from hdrimg.style import DEFAULT_STYLE,STYLE_PRESETS,PHONE_CLEAR_CANDIDATE,PHONE_CLEAR_V8,PHONE_CLEAR_V8_EXPERIMENT
from hdrimg.phone_clear_v8 import redistribution_curve,scene_decision,outdoor_evidence
from hdrimg.phone_clear import scene_decision as baseline_scene_decision


def test_accepted_default_matches_frozen_r5_and_preserves_historical_v7():
    assert DEFAULT_STYLE==STYLE_PRESETS['phone-clear']==PHONE_CLEAR_V8==PHONE_CLEAR_V8_EXPERIMENT
    assert DEFAULT_STYLE.algorithm_version==8
    assert PHONE_CLEAR_CANDIDATE.algorithm_version==7
    assert PHONE_CLEAR_CANDIDATE != DEFAULT_STYLE
    assert not STYLE_PRESETS['phone-natural'].clear_float
    assert not PHONE_CLEAR_CANDIDATE.clear_v8
    assert PHONE_CLEAR_V8_EXPERIMENT.clear_v8
    assert PHONE_CLEAR_V8_EXPERIMENT.pale_boundaries


@pytest.mark.parametrize('peak',[400,1000,2000])
def test_luminance_allocation_monotone_finite_peak_and_blacks(peak):
    upper=peak/203
    y=np.linspace(0,upper,120001,dtype=np.float32)
    for amount in [0,.45,.7,.95,1.]:
        for day in [0,.25,1]:
            mapped=redistribution_curve(y,upper=upper,amount=amount,dark_daylight=day,indoor=0)
            assert mapped[0]==0
            assert np.all(np.isfinite(mapped))
            assert mapped.max() <= upper+2e-6
            assert np.min(np.diff(mapped)) >= -2e-6
            np.testing.assert_allclose(mapped[-1],upper,rtol=2e-6)


def test_allocation_retains_deep_shadow_and_indoor_brightness():
    y=np.geomspace(1e-5,4.9,10000,dtype=np.float32)
    for day in [0,1]:
        mapped=redistribution_curve(y,upper=1000/203,amount=.7,dark_daylight=day,indoor=0)
        np.testing.assert_array_equal(mapped[y<.04],y[y<.04])
    np.testing.assert_array_equal(redistribution_curve(y,upper=1000/203,amount=.7,dark_daylight=0,indoor=1),y)


def test_exposure_composition_cap_preserves_monotone_input():
    base=np.geomspace(1e-6,1000/203,100000,dtype=np.float32)
    before=base*2**-.25
    expanded=redistribution_curve(before,upper=1000/203,amount=.95,dark_daylight=0,indoor=0)
    actual=np.clip(expanded,base*2**-.5,base*2**.5)
    assert np.min(np.diff(actual))>=-1e-6
    assert np.max(np.abs(np.log2(actual/base)))<=.500001


def test_meter_vegetation_counters_low_dynamic_range_indoor_guess():
    rng=np.random.default_rng(9)
    y=rng.uniform(.06,.12,(100,100)).astype(np.float32)
    room=np.repeat(y[...,None],3,axis=-1)
    garden=room.copy();garden[:40]*=np.array([.65,1,.35],np.float32)
    old=baseline_scene_decision(garden,development_ev=-2,peak_nits=1000)
    new=scene_decision(garden,development_ev=-2,peak_nits=1000)
    assert old.indoor_weight>.3
    assert new.indoor_weight<.1
    assert new.sdr_auto_ev>old.sdr_auto_ev
    assert scene_decision(room,development_ev=-2,peak_nits=1000)==baseline_scene_decision(room,development_ev=-2,peak_nits=1000)


def test_meter_color_counterevidence_is_continuous_and_not_white_balance():
    x=np.linspace(.02,.3,4096,dtype=np.float32).reshape(64,64)
    rgb=x[...,None]*np.array([.7,1,.4],np.float32)
    a=scene_decision(rgb,development_ev=-2,peak_nits=1000)
    b=scene_decision(rgb*1.001,development_ev=-2,peak_nits=1000)
    assert abs(a.sdr_auto_ev-b.sdr_auto_ev)<.01
    assert 0<=outdoor_evidence(rgb,development_ev=-2)['outdoor_counterevidence']<=1
