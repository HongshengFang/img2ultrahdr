"""Fit bounded tone/color residuals with leave-one-scene-out validation.

This is an experimental calibration method, not a production default. The
held-out image's phone JPEG never enters its fitted model. Low-capacity ridge
regression predicts a transform from image colors, neighborhood lightness and
scene statistics; neither coordinates nor filenames are model features.
"""
from __future__ import annotations
import argparse
import json
import hashlib
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from audit_phone_clear import _read_hdr, _read_sdr, _small
from hdrimg.color import (DISPLAY_P3_TO_SRGB, DISPLAY_P3_TO_XYZ, REC2020_TO_DISPLAY_P3,
                         SRGB_TO_DISPLAY_P3, linear_srgb_to_oklab,
                         oklab_to_linear_srgb, compress_gamut, srgb_oetf)
from hdrimg.phone_tone import _box_mean

DISPLAY_P3_TO_REC2020 = np.linalg.inv(REC2020_TO_DISPLAY_P3).astype(np.float32)


def features(rgb: np.ndarray, tone: dict) -> np.ndarray:
    lab = linear_srgb_to_oklab(rgb @ DISPLAY_P3_TO_SRGB.T)
    l = np.clip(lab[...,0],0,1)
    a = np.clip(lab[...,1]/np.maximum(l,.1),-.4,.4)*4
    b = np.clip(lab[...,2]/np.maximum(l,.1),-.4,.4)*4
    local = l-_box_mean(l,max(2,round(min(l.shape)*.04)))
    basis = [np.ones_like(l),l,l*l,l*l*l,a,b,a*a,b*b,a*b,l*a,l*b,local]
    global_values = [tone['phone_dark_weight'],tone['phone_indoor_weight'],tone['phone_high_key_weight'],
                     np.clip(np.log2(max(tone['phone_raw_p50'],.01)/.3),-3,3)/3,
                     np.clip(np.log2(max(tone['phone_raw_p90'],.01)/max(tone['phone_raw_p10'],.01)),0,6)/6]
    for g in global_values:
        basis.extend([g*np.ones_like(l),g*l,g*l*l,g*a,g*b])
    return np.stack(basis,axis=-1).astype(np.float32)


def target_residual(ps,rs,ph,rh):
    py,ry = ps@DISPLAY_P3_TO_XYZ[1],rs@DISPLAY_P3_TO_XYZ[1]
    logit = lambda v: np.log2(np.clip(v,.005,.995)/(1-np.clip(v,.005,.995)))
    pl = linear_srgb_to_oklab(ps@DISPLAY_P3_TO_SRGB.T)
    rl = linear_srgb_to_oklab(rs@DISPLAY_P3_TO_SRGB.T)
    relative = pl[...,1:]/np.maximum(pl[...,0:1],.1)-rl[...,1:]/np.maximum(rl[...,0:1],.1)
    return np.concatenate((
        np.clip(logit(py)-logit(ry),-2,2)[...,None],
        np.clip(np.log2((ph@DISPLAY_P3_TO_XYZ[1]+.01)/(rh@DISPLAY_P3_TO_XYZ[1]+.01)),-2,2)[...,None],
        np.clip(relative,-.12,.12)),axis=-1).astype(np.float32)


def fit(x: np.ndarray,y: np.ndarray) -> dict:
    scale = np.maximum(np.std(x,axis=0),.1);scale[0]=1
    z=x/scale
    # Shrink toward identity, with one fixed penalty for all held-out scenes.
    penalty=np.eye(z.shape[1],dtype=np.float64)*(.04*len(z))
    coefficients=[]
    for channel in range(y.shape[1]):
        weights=np.ones(len(z))
        for _ in range(3):
            c=np.linalg.solve(z.T@(z*weights[:,None])+penalty,z.T@(weights*y[:,channel]))
            residual=np.abs(z@c-y[:,channel])
            delta=.3 if channel<2 else .025
            weights=np.minimum(1,delta/np.maximum(residual,1e-6))
        coefficients.append(c)
    return {'scale':scale.tolist(),'coefficients':np.stack(coefficients,axis=-1).tolist(),
            'caps':[.6,.6,.04,.04],'feature_version':1,'ridge_fraction':.04}


def predict(rgb,tone,model):
    x=features(rgb,tone)
    return np.clip((x/np.asarray(model['scale'],dtype=np.float32))@
                   np.asarray(model['coefficients'],dtype=np.float32),
                   -np.asarray(model['caps']),np.asarray(model['caps'])).astype(np.float32)


def apply_model(info,sdr_path: Path,hdr_path: Path,model_path: Path):
    model=json.loads(model_path.read_text())
    with Image.open(sdr_path) as image:
        icc=image.info['icc_profile'];v=np.asarray(image.convert('RGB'),dtype=np.float32)/255
    sdr=np.where(v<=.04045,v/12.92,((v+.055)/1.055)**2.4)
    field=predict(sdr,info.tone_mapping,model)
    sy=sdr@DISPLAY_P3_TO_XYZ[1]
    gain=np.exp2(field[...,0]);new_y=sy*gain/(1-sy+sy*gain)
    def recolor(rgb,y,upper,target):
        old_y=rgb@DISPLAY_P3_TO_XYZ[1]
        rgb=rgb*np.divide(y,old_y,out=np.zeros_like(y),where=old_y>1e-8)[...,None]
        lab=linear_srgb_to_oklab(rgb@DISPLAY_P3_TO_SRGB.T)
        lab[...,1:]+=field[...,2:]*lab[...,0:1]
        rgb=oklab_to_linear_srgb(lab)@SRGB_TO_DISPLAY_P3.T
        if target=='rec2020':rgb=rgb@DISPLAY_P3_TO_REC2020.T
        return compress_gamut(rgb,target=target,upper=upper)
    new_sdr=recolor(sdr,new_y,1,'display-p3')
    raw=np.fromfile(hdr_path,dtype='<f2').reshape(info.scene.height,info.scene.width,4).astype(np.float32)
    hdr=raw[...,:3]@REC2020_TO_DISPLAY_P3.T
    # The encoder currently uses a minimum boost of one. Honor that contract.
    hdr_y=np.maximum(hdr@DISPLAY_P3_TO_XYZ[1]*np.exp2(field[...,1]),new_sdr@DISPLAY_P3_TO_XYZ[1])
    raw[...,:3]=recolor(hdr,hdr_y,info.peak_nits/203,'rec2020')
    raw.astype('<f2').tofile(hdr_path)
    Image.fromarray(np.uint8(np.floor(srgb_oetf(new_sdr)*255+.5))).save(
        sdr_path,quality=95,subsampling=0,optimize=True,icc_profile=icc)
    hdr_p3=raw[...,:3]@REC2020_TO_DISPLAY_P3.T
    boost=max(1,float(np.max((np.maximum(hdr_p3,0)+1/64)/(new_sdr+1/64)))*1.03)
    record={'held_out':model.get('held_out'),'training_count':len(model['training_stems']),
            'model_sha256':hashlib.sha256(model_path.read_bytes()).hexdigest(),
            'field_min':field.min(axis=(0,1)).tolist(),'field_max':field.max(axis=(0,1)).tolist()}
    return replace(info,max_content_boost=boost,tone_mapping={**info.tone_mapping,'experimental_residual':record})


def train(source: Path,output: Path):
    output.mkdir(parents=True,exist_ok=False)
    report=json.loads((source/'audit.json').read_text())
    examples=[]
    for row in report['pairs']:
        folder=source/f'{row["id"]:02}_{row["stem"]}'
        rs,dim=_read_sdr(folder/'sdr.jpg',step=1)
        rh=_read_hdr(folder/'hdr.rgba16f',dim,step=1)@REC2020_TO_DISPLAY_P3.T
        with np.load(folder/'reference_preview.npz') as data:
            ps,ph=data['sdr'],data['hdr']
        ps,rs,ph,rh=(_small(v) for v in (ps,rs,ph,rh))
        x=features(rs,row['resolved_tone']).reshape(-1,37)
        y=target_residual(ps,rs,ph,rh).reshape(-1,4)
        examples.append((row['stem'],x,y))
    results=[]
    for name,_,_ in examples:
        training=[item for item in examples if item[0]!=name]
        model=fit(np.concatenate([v[1] for v in training]),np.concatenate([v[2] for v in training]))
        model.update(held_out=name,training_stems=[v[0] for v in training],source=str(source.resolve()))
        (output/f'{name}.json').write_text(json.dumps(model,indent=2)+'\n')
        held=next(v for v in examples if v[0]==name)
        pred=np.clip(held[1]/np.array(model['scale'])@np.array(model['coefficients']),-np.array(model['caps']),np.array(model['caps']))
        results.append({'held_out':name,'before_abs_residual':np.mean(np.abs(held[2]),axis=0).tolist(),
                        'after_abs_residual':np.mean(np.abs(held[2]-pred),axis=0).tolist()})
    full=fit(np.concatenate([v[1] for v in examples]),np.concatenate([v[2] for v in examples]))
    full.update(training_stems=[v[0] for v in examples],source=str(source.resolve()))
    (output/'full.json').write_text(json.dumps(full,indent=2)+'\n')
    (output/'validation.json').write_text(json.dumps({'channels':['SDR log-odds EV','HDR luma EV','relative OKLab a','relative OKLab b'],
        'caution':'Coarse unregistered training pairs. Diagnostic residual distances are not visual quality. Models and input JPEGs are kept separate; each validation scene is excluded from fitting.','scenes':results},indent=2)+'\n')
    print(json.dumps(results,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();train(a.source,a.output)
