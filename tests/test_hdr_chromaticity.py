import numpy as np
import tifffile
from hdrimg import render
from hdrimg.color import REC2020_TO_DISPLAY_P3, DISPLAY_P3_TO_XYZ


def test_automatic_hdr_retains_independent_sdr_reference_chromaticity(tmp_path, monkeypatch):
    rgb=np.empty((64,96,3),np.float32)
    rgb[:,:32]=[.22,.11,.09]
    rgb[:,32:64]=[.09,.20,.11]
    rgb[:,64:]=[.08,.10,.21]
    rgb *= np.linspace(.7,1.3,64,dtype=np.float32)[:,None,None]
    path=tmp_path/'scene.tif';tifffile.imwrite(path,rgb,photometric='rgb')
    linear_sdr=tmp_path/'final-sdr.tif'
    render.render_pair(path,tmp_path/'sdr.jpg',tmp_path/'hdr.raw',auto_exposure=True,
        exposure_ev=None,highlight_ev=0,hdr_strength=1,peak_nits=1000,
        subject_adaptation_strength=0, _allow_histogram=False,
        _linear_sdr_output=linear_sdr)
    sdr=tifffile.imread(linear_sdr)
    hdr=np.fromfile(tmp_path/'hdr.raw',dtype='<f2').reshape(64,96,4)[...,:3].astype(np.float32)@REC2020_TO_DISPLAY_P3.T
    sy=sdr@DISPLAY_P3_TO_XYZ[1];hy=hdr@DISPLAY_P3_TO_XYZ[1]
    normalized=hdr*(sy/hy)[...,None]
    np.testing.assert_allclose(normalized,sdr,atol=2e-4)
