from pathlib import Path
import numpy as np
import tifffile
from hdrimg.phone_color_guard import preserve_blue_chroma
from hdrimg.tone import luminance_rec2020


def test_blue_step_removed_luminance_preserved_and_chunk_boundaries(tmp_path):
    h,w=80,100;gradient=np.linspace(.06,.13,h)[:,None,None]
    reference=np.ones((h,w,3),np.float32)*gradient*np.array([.4,.8,1.5])
    source=reference.copy();source[40:,:,0]*=.92
    tifffile.imwrite(tmp_path/'r.tif',reference.astype(np.float32),photometric='rgb')
    tifffile.imwrite(tmp_path/'s.tif',source.astype(np.float32),photometric='rgb')
    for chunk,name in [(17,'a'),(256,'b')]:
        preserve_blue_chroma(tmp_path/'s.tif',tmp_path/'r.tif',tmp_path/(name+'.tif'),chunk_rows=chunk)
    a=tifffile.imread(tmp_path/'a.tif');b=tifffile.imread(tmp_path/'b.tif')
    assert np.allclose(a,b,atol=1e-6)
    assert np.allclose(luminance_rec2020(a),luminance_rec2020(source),atol=1e-6)
    ratio=a[...,0]/a[...,1]
    assert np.max(np.abs(np.diff(ratio[:,50])))<.001


def test_bypass_and_geometry_guard(tmp_path):
    import pytest
    s=tmp_path/'s.tif';r=tmp_path/'r.tif';o=tmp_path/'o.tif'
    tifffile.imwrite(s,np.ones((20,30,3),np.float32),photometric='rgb')
    tifffile.imwrite(r,np.ones((21,30,3),np.float32),photometric='rgb')
    assert not preserve_blue_chroma(s,r,o,strength=0)['applied'] and not o.exists()
    with pytest.raises(ValueError):preserve_blue_chroma(s,r,o)


def test_neutral_no_supported_blue(tmp_path):
    a=np.ones((20,30,3),np.float32)*.2;p=tmp_path/'a.tif';q=tmp_path/'b.tif'
    tifffile.imwrite(p,a,photometric='rgb');result=preserve_blue_chroma(p,p,q)
    assert not result['applied'] and not q.exists()


def test_chunk_validation(tmp_path):
    import pytest
    for chunk in (0,-1,1.5,True):
        with pytest.raises(ValueError,match='chunk_rows'):
            preserve_blue_chroma(tmp_path/'s.tif',tmp_path/'r.tif',tmp_path/'o.tif',chunk_rows=chunk)


def test_preserves_source_icc(tmp_path):
    rgb=np.ones((24,30,3),np.float32)*[.04,.09,.17]
    icc=b'linear rec2020 profile test payload'
    source=tmp_path/'s.tif';target=tmp_path/'o.tif'
    tifffile.imwrite(source,rgb.astype(np.float32),photometric='rgb',extratags=[(34675,'B',len(icc),icc,False)])
    result=preserve_blue_chroma(source,source,target)
    assert result['applied']
    with tifffile.TiffFile(target) as f:
        assert f.pages[0].tags[34675].value==icc
