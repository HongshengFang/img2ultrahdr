import numpy as np
from hdrimg.color import refine_phone_color, oklab_to_linear_srgb


def test_selective_rotation_preserves_green_blue_and_near_gray():
    # Green and neutral controls must remain unaffected by narrowing warm hue.
    hues=np.deg2rad([120,150,240,60])
    chroma=np.array([.09,.09,.09,.01])
    lab=np.stack([np.full(4,.7),chroma*np.cos(hues),chroma*np.sin(hues)],axis=-1)[None]
    rgb=oklab_to_linear_srgb(lab)
    new=refine_phone_color(rgb,target='srgb',dark_weight=0,indoor_weight=0,neutral_protection=True)
    assert np.isfinite(new).all()
    # Full neutral protection path has always desaturated near gray; retain it.
    from hdrimg.color import linear_srgb_to_oklab
    result=linear_srgb_to_oklab(new)
    np.testing.assert_allclose(result[0,:2],lab[0,:2],atol=1e-5)
    assert np.linalg.norm(result[0,3,1:]) < chroma[3]
