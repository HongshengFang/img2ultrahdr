"""Prepare the same residual-noise decision used by the production pipeline."""
from pathlib import Path
import argparse
import json
import tempfile
import numpy as np
import tifffile
from PIL import Image
from hdrimg.phone_noise import phone_denoise_decision
from hdrimg.raw import RAW_DEVELOPMENT_EV, develop_raw, phone_denoise_overlay
from hdrimg.render import open_scene
from hdrimg.tools import resolve_tools


def prepare(samples, balanced, output):
    output.mkdir(parents=True, exist_ok=False)
    for name in ('full','area','profiles'):
        (output/name).mkdir()
    tools=resolve_tools(require_raw=True,require_exif=True)
    rows=[]
    for path in sorted((balanced/'full').glob('*.tif')):
        scene,info=open_scene(path)
        decision=phone_denoise_decision(scene,development_ev=RAW_DEVELOPMENT_EV)
        del scene
        weight=decision['extra_denoise_weight']
        target=output/'full'/path.name;area=output/'area'/path.name
        if weight<=0:
            target.symlink_to(path.resolve())
            area.symlink_to((balanced/'area'/path.name).resolve())
        else:
            profile=output/'profiles'/f'{path.stem}.pp3'
            profile.write_text(phone_denoise_overlay(1,extra_luma=weight))
            source=next(samples.glob(path.stem+'.RAW-*.dng'))
            with tempfile.TemporaryDirectory(prefix='phone-adaptive-cache-') as tmp:
                develop_raw(source.resolve(),target.resolve(),tools=tools,white_balance='camera',
                    temperature_k=None,tint=1,work_dir=Path(tmp),profile_overlay=profile)
            scene,_=open_scene(target)
            size=(round(info.width*1024/max(info.width,info.height)),round(info.height*1024/max(info.width,info.height)))
            small=np.stack([np.asarray(Image.fromarray(scene[...,c],mode='F').resize(size,Image.Resampling.BOX)) for c in range(3)],axis=-1)
            tifffile.imwrite(area,small,photometric='rgb')
            del scene
        row={'stem':path.stem,**decision};rows.append(row);print(json.dumps(row),flush=True)
    (output/'cache.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('samples','balanced','output'):
        p.add_argument(name,type=Path)
    a=p.parse_args();prepare(a.samples,a.balanced,a.output)
