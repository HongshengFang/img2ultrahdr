"""Measure flat-shadow noise and test stronger RAW denoise only when supported.

This diagnostic uses full-resolution linear scenes, never reference-image masks
or sample identities. Existing balanced developments remain the zero-change base.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from hdrimg.phone_tone import phone_scene_decision
from hdrimg.raw import RAW_DEVELOPMENT_EV, develop_raw
from hdrimg.render import open_scene
from hdrimg.tone import luminance_rec2020
from hdrimg.tools import resolve_tools


def smooth(value):
    t = float(np.clip(value, 0, 1))
    return t*t*(3-2*t)


def estimate_shadow_noise(scene):
    y = luminance_rec2020(scene)*2**-RAW_DEVELOPMENT_EV
    h,w = y.shape; h-=h%64; w-=w%64
    if not h or not w:
        return {"relative_noise":0.,"shadow_support":0.,"flat_tiles":0}
    patches = y[:h,:w].reshape(h//64,64,w//64,64).transpose(0,2,1,3).reshape(-1,64,64)
    median = np.median(patches,axis=(1,2))
    coarse = patches.reshape(-1,8,8,8,8).mean(axis=(2,4))
    contrast = (np.percentile(coarse,90,axis=(1,2))-np.percentile(coarse,10,axis=(1,2)))/(median+.005)
    shadows = (median>.001)&(median<.08)&np.isfinite(contrast)
    if np.count_nonzero(shadows)<16:
        return {"relative_noise":0.,"shadow_support":float(np.mean(shadows)),"flat_tiles":0}
    flat = shadows&(contrast<=np.percentile(contrast[shadows],25))
    small = patches[flat].reshape(-1,32,2,32,2).mean(axis=(2,4))
    hh = (small[:,::2,::2]-small[:,::2,1::2]-small[:,1::2,::2]+small[:,1::2,1::2])/2
    sigma = np.median(abs(hh),axis=(1,2))/.67448975
    return {"relative_noise":float(np.median(sigma/(median[flat]+.005))),
            "shadow_support":float(np.mean(shadows)),"flat_tiles":int(flat.sum())}


def prepare(samples: Path, original: Path, balanced: Path, output: Path):
    output.mkdir(parents=True,exist_ok=False)
    for name in ['full','area','profiles']:(output/name).mkdir()
    tools = resolve_tools(require_raw=True,require_exif=True)
    rows=[]
    for path in sorted((original/'full').glob('*.tif')):
        scene,info = open_scene(path)
        step=max(1,int(np.ceil(max(info.width,info.height)/1024)))
        cue=phone_scene_decision(scene[::step,::step],development_ev=RAW_DEVELOPMENT_EV,peak_nits=1000)
        noise=estimate_shadow_noise(scene)
        weight=cue.dark_weight*smooth((noise['relative_noise']-.04)/.06)*smooth((noise['shadow_support']-.10)/.35)
        target=output/'full'/path.name;area=output/'area'/path.name
        row={'stem':path.stem,'dark_weight':cue.dark_weight,'noise':noise,'extra_denoise_weight':weight,
             'luma':15+25*weight,'detail':30*(1-weight),'chroma':60,'gamma':1}
        if weight<=0:
            target.symlink_to((balanced/'full'/path.name).resolve())
            area.symlink_to((balanced/'area'/path.name).resolve())
        else:
            source=next(samples.glob(path.stem+'.RAW-*.dng'))
            profile=output/'profiles'/f'{path.stem}.pp3'
            profile.write_text('[Directional Pyramid Denoising]\nEnabled=true\n'
                               f'Luma={row["luma"]:.8f}\nLdetail={row["detail"]:.8f}\n'
                               'Chroma=60\nCMethod=MAN\nC2Method=MANU\nGamma=1.0\n')
            with tempfile.TemporaryDirectory(prefix='phone-adaptive-nr-') as temp:
                develop_raw(source.resolve(),target.resolve(),tools=tools,white_balance='camera',
                            temperature_k=None,tint=1,work_dir=Path(temp),profile_overlay=profile)
            corrected,_=open_scene(target)
            size=(round(info.width*1024/max(info.width,info.height)),round(info.height*1024/max(info.width,info.height)))
            small=np.stack([np.asarray(Image.fromarray(corrected[...,c],mode='F').resize(size,Image.Resampling.BOX)) for c in range(3)],axis=-1)
            tifffile.imwrite(area,small,photometric='rgb')
        rows.append(row);print(json.dumps(row),flush=True)
    (output/'cache.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['samples','original','balanced','output']:p.add_argument(name,type=Path)
    a=p.parse_args();prepare(a.samples,a.original,a.balanced,a.output)
