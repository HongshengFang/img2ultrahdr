"""Create a separate, ICC-preserving cache with the production surface filter."""
import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image
from hdrimg.phone_surface import denoise_blue_surfaces


def prepare(source: Path, output: Path, strength: float):
    output.mkdir(parents=True, exist_ok=False)
    (output/'full').mkdir();(output/'area').mkdir()
    inputs=sorted((source/'full').glob('*.tif'))
    if len(inputs)!=12:
        raise ValueError('Expected exactly12 native scene TIFFs')
    rows=[]
    for path in inputs:
        target=output/'full'/path.name
        decision=denoise_blue_surfaces(path,target,strength=strength,development_ev=-2)
        if not decision['applied']:shutil.copy2(path,target)
        scene=tifffile.memmap(target);h,w=scene.shape[:2]
        size=(round(w*1024/max(h,w)),round(h*1024/max(h,w)))
        small=np.stack([np.asarray(Image.fromarray(scene[...,c],mode='F').resize(size,Image.Resampling.BOX)) for c in range(3)],axis=-1)
        tifffile.imwrite(output/'area'/path.name,small,photometric='rgb')
        row={'stem':path.stem,'source':str(path.resolve()),**decision}
        rows.append(row);print(json.dumps(row),flush=True)
        del scene
    (output/'cache.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--strength',type=float,default=1)
    args=parser.parse_args();prepare(args.source,args.output,args.strength)
