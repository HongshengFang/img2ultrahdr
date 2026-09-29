"""Bounded diffuse-sky budget from connected color evidence.

This is a geometric/color heuristic, not semantic sky recognition.
"""
from collections import deque
import numpy as np
from PIL import Image
from .color import DISPLAY_P3_TO_XYZ, DISPLAY_P3_TO_SRGB, SRGB_TO_XYZ, linear_srgb_to_oklab
from .phone_tone import _box_mean, _smoothstep


def _top_connected(mask):
    h,w=mask.shape;found=np.zeros_like(mask,dtype=bool)
    queue=deque((0,int(x)) for x in np.flatnonzero(mask[0]))
    for y,x in queue:found[y,x]=True
    while queue:
        y,x=queue.popleft()
        for yy,xx in ((y-1,x),(y+1,x),(y,x-1),(y,x+1)):
            if 0<=yy<h and 0<=xx<w and mask[yy,xx] and not found[yy,xx]:
                found[yy,xx]=True;queue.append((yy,xx))
    return found


def sky_fields(rgb,strength=1,faces=None,gamut='display-p3'):
    if not np.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Sky adaptation strength must be between 0 and 1')
    if gamut not in ('display-p3', 'srgb'):
        raise ValueError('Unsupported sky gamut')
    rgb=np.clip(np.nan_to_num(rgb),0,1).astype(np.float32)
    coeff=DISPLAY_P3_TO_XYZ[1] if gamut=='display-p3' else SRGB_TO_XYZ[1]
    y=rgb@coeff;h,w=y.shape;one=Image.fromarray(np.ones_like(y),mode='F')
    if strength<=0:return one,one.copy(),{'method':'connected diffuse sky','reason':'disabled'}
    lab=linear_srgb_to_oklab(rgb@DISPLAY_P3_TO_SRGB.T if gamut=='display-p3' else rgb)
    relative=np.hypot(lab[...,1],lab[...,2])/np.maximum(lab[...,0],.01)
    blue=_smoothstep((rgb[...,2]/(rgb[...,0]+.005)-1.025)/.12)*_smoothstep((rgb[...,2]/(rgb[...,1]+.005)-1.0)/.08)
    size=(max(2,round(w*min(1,256/max(h,w)))),max(2,round(h*min(1,256/max(h,w)))))
    small=np.asarray(Image.fromarray(blue,mode='F').resize(size,Image.Resampling.BOX))
    connected=_top_connected(small>.20)
    seed=np.array(Image.fromarray(connected.astype(np.float32),mode='F').resize((w,h),Image.Resampling.NEAREST))>.5
    selected=seed & (blue>.5)
    if np.count_nonzero(selected)<max(16,.005*h*w):
        return one,one.copy(),{'method':'scene-supported diffuse sky','reason':'insufficient_blue_sky'}
    median_chroma=float(np.median(relative[selected]))
    pale=1-float(_smoothstep((median_chroma-.115)/.030))
    # Apply a scene-wide sky budget, so pale clouds and blue gaps retain their
    # relative separation. Do not let per-pixel chroma draw a gradient contour.
    eligible=(rgb[...,2]>.90*rgb[...,0]) & (rgb[...,2]>.92*rgb[...,1]) & (y>.10)
    small=np.asarray(Image.fromarray(eligible.astype(np.float32),mode='F').resize(size,Image.Resampling.BOX))>.8
    # Open narrow color bridges (for example a blue printed sign below sky).
    pad=np.pad(small,1,mode='edge')
    eroded=np.logical_and.reduce([pad[a:a+size[1],b:b+size[0]] for a in range(3) for b in range(3)])
    connected=_top_connected(eroded)
    pad=np.pad(connected,1,mode='edge')
    connected=np.logical_or.reduce([pad[a:a+size[1],b:b+size[0]] for a in range(3) for b in range(3)])
    support=np.array(Image.fromarray(connected.astype(np.float32),mode='F').resize((w,h),Image.Resampling.BILINEAR))
    support*=pale*(1-_smoothstep((np.arange(h)[:,None]/h-.60)/.12))
    support=_box_mean(support,max(2,round(min(h,w)*.015)))
    yy,xx=np.mgrid[:h,:w].astype(np.float32)
    for face in faces or []:
        x0,y0,bw,bh=[face[k] for k in ('x','y','width','height')]
        dx=(xx/w-(x0+bw/2))/max(bw,.001);dy=(yy/h-(y0+.35*bh))/max(bh,.001)
        support*=1-np.clip(3*np.exp(-1.6*(dx*dx+(dy/1.6)**2)),0,1)
    support=_box_mean(support,2)
    delta=.50*strength*support;odds=np.exp2(delta)
    after=y*odds/(1-y+y*odds);ratio=np.divide(after,y,out=np.ones_like(y),where=y>1e-6)
    return Image.fromarray(ratio,mode='F'),Image.fromarray(odds,mode='F'),{'method':'connected diffuse sky','strength':strength,'median_sky_relative_chroma':median_chroma,'pale_scene_weight':pale,'support_mean':float(support.mean()),'max_odds_ev':float(delta.max())}
