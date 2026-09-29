"""Preserve full linear phone developments and area-filtered audit previews.

Unlike stride sampling, area reduction does not alias sensor noise into the
small preview. Full-resolution data stays available for texture verification.
"""
from __future__ import annotations
import argparse
import json
import tempfile
from pathlib import Path
import numpy as np
import tifffile
from PIL import Image
from hdrimg.raw import develop_raw
from hdrimg.render import open_scene
from hdrimg.tools import resolve_tools


def cache(samples: Path, destination: Path, edge: int, white_balance: str = "camera", profile_overlay: Path | None = None):
    full=destination/"full";preview=destination/"area"
    full.mkdir(parents=True,exist_ok=True);preview.mkdir(exist_ok=True)
    tools=resolve_tools(require_raw=True,require_exif=True)
    records=[]
    for source in sorted(samples.glob("*.dng")):
        stem=source.name.split('.RAW-',1)[0];target=full/f"{stem}.tif"
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='phone-full-cache-') as temp:
                develop_raw(source.resolve(),target.resolve(),tools=tools,white_balance=white_balance,
                            temperature_k=None,tint=1,work_dir=Path(temp),profile_overlay=profile_overlay)
        scene,info=open_scene(target)
        size=(round(info.width*edge/max(info.width,info.height)),round(info.height*edge/max(info.width,info.height)))
        reduced=np.stack([np.asarray(Image.fromarray(scene[...,c],mode='F').resize(size,Image.Resampling.BOX)) for c in range(3)],axis=-1)
        dest=preview/f"{stem}.tif"
        if not dest.exists():tifffile.imwrite(dest,reduced,photometric='rgb')
        records.append({'source':str(source),'full':str(target),'preview':str(dest),'full_size':[info.width,info.height],'preview_size':size,'white_balance':white_balance,'profile_overlay':profile_overlay.read_text() if profile_overlay else None})
        print(stem,info.width,info.height,'->',size,flush=True)
    (destination/'cache.json').write_text(json.dumps(records,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('samples',type=Path);p.add_argument('destination',type=Path);p.add_argument('--edge',type=int,default=1024)
    p.add_argument('--white-balance',choices=['camera','auto'],default='camera')
    p.add_argument('--profile-overlay',type=Path)
    a=p.parse_args();cache(a.samples,a.destination,a.edge,a.white_balance,a.profile_overlay)
