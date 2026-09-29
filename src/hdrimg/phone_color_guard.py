"""Preserve smooth blue RAW chromaticity across upstream noise reduction.

RawTherapee5.13 applies a thresholded post-NR chroma boost. A separate,
matching development supplies continuous color; denoised luminance is retained.
"""
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image
from .phone_surface import smooth_blue_support, _guided_surface
from .phone_tone import resize_base_rows, _smoothstep
from .tone import luminance_rec2020


def preserve_blue_chroma(source:Path, reference:Path, destination:Path, strength=1., development_ev=-2, chunk_rows=256):
    if not np.isfinite(strength) or not 0<=strength<=1:raise ValueError('Color preservation strength must be0..1')
    if not isinstance(chunk_rows, int) or isinstance(chunk_rows, bool) or chunk_rows < 1:
        raise ValueError('Color preservation chunk_rows must be a positive integer')
    if destination.exists():raise FileExistsError(destination)
    record={'method':'reference blue chromaticity with denoised luminance','strength':strength,'applied':False}
    if strength==0:return {**record,'reason':'disabled'}
    scene=tifffile.memmap(source);original=tifffile.memmap(reference)
    if scene.shape!=original.shape:raise ValueError('Color reference geometry mismatch')
    h,w=scene.shape[:2]
    if min(h,w)<8:return {**record,'reason':'too_small'}
    scale=min(1,1024/max(h,w));size=(max(1,round(w*scale)),max(1,round(h*scale)))
    preview=np.stack([np.asarray(Image.fromarray(original[...,c],mode='F').resize(size,Image.Resampling.BOX)) for c in range(3)],axis=-1)
    mask=smooth_blue_support(preview,development_ev=development_ev)
    # The interior receives complete reference chroma; soften only uncertainty.
    mask=_smoothstep(mask/.65)
    if np.max(mask)<1e-6:return {**record,'reason':'no_blue_support'}
    mask_image=Image.fromarray(mask,mode='F')
    with tifffile.TiffFile(source) as f:
        tag=f.pages[0].tags.get(34675);icc=tag.value if tag else None
    tags=[(34675,'B',len(icc),icc,False)] if icc else []
    output=tifffile.memmap(destination,shape=scene.shape,dtype='float32',photometric='rgb',extratags=tags)
    radius=6;epsilon=.002;halo=2*radius
    for start in range(0,h,chunk_rows):
        stop=min(h,start+chunk_rows);a=max(0,start-halo);b=min(h,stop+halo);core=slice(start-a,stop-a)
        smooth=_guided_surface(np.asarray(original[a:b]),epsilon,radius)[core]
        target=np.asarray(scene[start:stop],dtype=np.float32)
        target_y=luminance_rec2020(target);smooth_y=np.maximum(luminance_rec2020(smooth),1e-8)
        candidate=smooth*(target_y/smooth_y)[...,None]
        weight=strength*resize_base_rows(mask_image,width=w,full_height=h,start=start,stop=stop)
        output[start:stop]=target+weight[...,None]*(candidate-target)
    output.flush();del output,original,scene
    return {**record,'applied':True,'mask_mean':float(mask.mean()),'radius':radius,'epsilon':epsilon}
