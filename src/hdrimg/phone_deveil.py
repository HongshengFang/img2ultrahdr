"""Bounded bright-material luminance contrast with face and sky protection.

Inspired by He et al. 2009; artistic SDR-reference correction, not physical dehazing.
"""
import numpy as np
from PIL import Image
from .phone_tone import _box_mean, _smoothstep
from .color import DISPLAY_P3_TO_XYZ, SRGB_TO_XYZ


def _minimum(x,r):
    h,w=x.shape
    pad=np.pad(x,((0,0),(r,r)),mode="edge")
    out=np.minimum.reduce([pad[:,i:i+w] for i in range(2*r+1)])
    pad=np.pad(out,((r,r),(0,0)),mode="edge")
    return np.minimum.reduce([pad[i:i+h] for i in range(2*r+1)])


def deveil_fields(rgb,strength=.7,faces=None,gamut="display-p3"):
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError("Material contrast strength must be between 0 and 1")
    if gamut not in ("display-p3", "srgb"):
        raise ValueError("Unsupported material contrast gamut")
    rgb=np.clip(np.nan_to_num(rgb),0,1).astype(np.float32)
    y=rgb@(DISPLAY_P3_TO_XYZ[1] if gamut=="display-p3" else SRGB_TO_XYZ[1]);h,w=y.shape
    one=Image.fromarray(np.ones_like(y),mode='F')
    if min(y.shape)<8 or strength<=0:return one,one.copy(),{'method':'bounded dark-channel local contrast','reason':'disabled'}
    dark=_minimum(np.min(rgb,axis=-1),max(2,round(min(h,w)*.012)))
    light=np.cbrt(y);texture=np.sqrt(np.maximum(_box_mean(light*light,4)-_box_mean(light,4)**2,0))
    chroma=(np.max(rgb,axis=-1)-np.min(rgb,axis=-1))/(np.max(rgb,axis=-1)+.015)
    blue=_smoothstep((rgb[...,2]/(rgb[...,0]+.01)-1.05)/.35)
    support=(1-blue)*_smoothstep((chroma-.08)/.22)*_smoothstep((texture-.008)/.025)
    support*=_smoothstep((y-.16)/.16)*(1-_smoothstep((y-.78)/.15))
    yy,xx=np.mgrid[:h,:w].astype(np.float32);protected=np.zeros_like(y)
    for face in faces or []:
        x0,y0,bw,bh=[face[k] for k in ('x','y','width','height')]
        dx=(xx/w-(x0+bw/2))/max(bw,.001);dy=(yy/h-(y0+.35*bh))/max(bh,.001)
        protected=np.maximum(protected,np.clip(3*np.exp(-1.6*(dx*dx+(dy/1.6)**2)),0,1))
    support*=1-protected
    # Conservative white atmospheric anchor, luminance-only bounded adaptation.
    t=np.clip(1-.85*dark/.85,.55,1)
    # Edge-aware smooth transmission, using cube-root luminance guidance.
    m=_box_mean(light,8);p=_box_mean(t,8)
    var=np.maximum(_box_mean(light*light,8)-m*m,0)
    cov=_box_mean(light*t,8)-m*p
    a=cov/(var+.002);b=p-a*m
    t=np.clip(_box_mean(a,8)*light+_box_mean(b,8),.55,1)
    target=np.clip((y-.85)/t+.85,1e-6,1-1e-6)
    safe=np.clip(y,1e-6,1-1e-6)
    delta=np.log2(target/(1-target))-np.log2(safe/(1-safe))
    delta=_box_mean(np.clip(delta,-.75,0)*strength*support,1)
    odds=np.exp2(delta);after=y*odds/(1-y+y*odds)
    ratio=np.divide(after,y,out=np.ones_like(y),where=y>1e-6)
    return Image.fromarray(ratio,mode='F'),Image.fromarray(odds,mode='F'),{'method':'bounded non-sky dark-channel luminance contrast','strength':strength,'max_abs_odds_ev':float(np.max(np.abs(delta))),'mean_support':float(np.mean(support))}
