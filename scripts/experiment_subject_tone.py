"""Experimental local subject tone, using on-device Vision observations.

This is an evidence-generating prototype, not a production renderer dependency.
No sample coordinates or reference image values are used by the adjustment.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
import audit_phone_clear as audit
from hdrimg.color import DISPLAY_P3_TO_SRGB, DISPLAY_P3_TO_XYZ, REC2020_TO_DISPLAY_P3, linear_srgb_to_oklab, srgb_oetf


def subject_ratio(sdr: np.ndarray, matte: np.ndarray, faces: list[dict], high_key: float) -> tuple[np.ndarray,dict]:
    h,w=sdr.shape[:2]
    yy,xx=np.mgrid[:h,:w].astype(np.float32);xx/=w;yy/=h
    luma=sdr@DISPLAY_P3_TO_XYZ[1]
    lab=linear_srgb_to_oklab(sdr@DISPLAY_P3_TO_SRGB.T)
    relative_chroma=np.hypot(lab[...,1],lab[...,2])/np.maximum(lab[...,0],.05)
    head=np.zeros((h,w),np.float32);face_weight=head.copy()
    medians=[]
    for face in faces:
        x,y,bw,bh=(face[k] for k in ('x','y','width','height'))
        if face['confidence']<.55 or bw*bh<.0005:continue
        area=luma[max(0,round((y+.2*bh)*h)):min(h,round((y+.9*bh)*h)),max(0,round((x+.2*bw)*w)):min(w,round((x+.8*bw)*w))]
        if area.size<4:continue
        medians.append(float(np.median(area)))
        dx=(xx-(x+bw/2))/bw;dy=(yy-(y+.35*bh))/bh
        head=np.maximum(head,np.exp(-1.6*(dx*dx+(dy/1.6)**2)))
        face_weight=np.maximum(face_weight,np.exp(-2.0*(dx*dx+((yy-(y+.60*bh))/bh)**2)))
    if not medians:
        return np.ones_like(luma),{'applied':False,'reason':'no reliable face'}
    median=float(np.median(medians))
    shadow=np.clip((median-luma)/max(median,.03),0,1)
    darken=(-.30*face_weight-.50*head*shadow)*matte
    neutral=np.clip((.09-relative_chroma)/.06,0,1)
    light=np.clip((luma-.10)/.14,0,1)
    garment=matte*(1-np.clip(head*1.7,0,1))*neutral*light
    brighten=1.25*high_key*garment
    # Positive changes use bounded log-odds to retain highlight separation.
    gain=np.exp2(brighten)
    lifted=luma*gain/(1-luma+luma*gain)
    desired=lifted*np.exp2(darken)
    ratio=desired/np.maximum(luma,1e-6)
    return ratio,{'applied':True,'face_median_y':median,'ratio_percentiles':np.percentile(ratio,[0,10,50,90,100]).tolist(),'high_key_weight':high_key}


def experiment(output:Path,reference:Path,cache:Path,probe:Path):
    output.mkdir(parents=True,exist_ok=False);(output/'vision').mkdir()
    with tarfile.open(output/'source.tar.gz','w:gz') as archive:
        for folder in ('src','scripts','tests','docs'):
            for p in Path(folder).rglob('*'):
                if p.is_file() and '__pycache__' not in str(p):archive.add(p)
    original=audit.render_pair
    def render(scene,sdr_path,hdr_path,**kwargs):
        info=original(scene,sdr_path,hdr_path,**kwargs)
        prefix=output/'vision'/scene.stem
        subprocess.run([str(probe.resolve()),str(sdr_path),str(prefix.resolve())],check=True,capture_output=True)
        observations=json.loads(prefix.with_suffix('.json').read_text())
        with Image.open(sdr_path) as image:
            icc=image.info['icc_profile'];encoded=np.asarray(image.convert('RGB'),dtype=np.float32)/255
        sdr=audit._decode_transfer(encoded)
        with Image.open(str(prefix)+'_person.png') as image:
            matte=np.asarray(image.convert('L').resize((sdr.shape[1],sdr.shape[0]),Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(1)),dtype=np.float32)/255
        ratio,decision=subject_ratio(sdr,matte,observations['faces'],info.tone_mapping['phone_high_key_weight'])
        if info.tone_mapping['phone_dark_weight']>.5:
            ratio[:]=1;decision={'applied':False,'reason':'dark-scene gate'}
        observations['tone']=decision;prefix.with_suffix('.json').write_text(json.dumps(observations,indent=2)+'\n')
        if not decision['applied']:return info
        new_sdr=np.clip(sdr*ratio[...,None],0,1)
        raw=np.fromfile(hdr_path,dtype='<f2').reshape(info.scene.height,info.scene.width,4).astype(np.float32)
        raw[...,:3]*=ratio[...,None]
        Image.fromarray(np.uint8(np.clip(np.floor(srgb_oetf(new_sdr)*255+.5),0,255))).save(sdr_path,quality=95,subsampling=0,optimize=True,icc_profile=icc)
        raw.astype('<f2').tofile(hdr_path)
        hdr_p3=raw[...,:3]@REC2020_TO_DISPLAY_P3.T
        boost=max(1,float(np.max((np.maximum(hdr_p3,0)+1/64)/(new_sdr+1/64)))*1.03)
        return replace(info,max_content_boost=boost,tone_mapping={**info.tone_mapping,'experimental_subject_adjustment':1.0})
    audit.render_pair=render
    audit.audit(Path('jpg_hdr_sample_effect'),output,cache,None,reference/'audit.json',keep_renders=True,encode_check=True,regions_path=Path('docs/phone-clear-regions.json'))
    subprocess.run([sys.executable,'scripts/review_phone_round.py',str(reference),str(output)],check=True)
    with (output/'tests.log').open('w') as f:subprocess.run([sys.executable,'-m','pytest','-q'],stdout=f,stderr=subprocess.STDOUT,check=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--reference',type=Path,required=True);p.add_argument('--scene-cache',type=Path,required=True);p.add_argument('--probe',type=Path,required=True)
    a=p.parse_args();experiment(a.output,a.reference,a.scene_cache,a.probe)
