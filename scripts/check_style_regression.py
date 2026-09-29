"""Replay unchanged styles on all 12 phone scenes plus the two fixed Canon RAWs.

Run once against a saved source tree using PYTHONPATH, then against current
source with --reference. Compares decoded SDR and exact HDR rendition buffers.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image
from hdrimg.raw import RAW_DEVELOPMENT_EV
from hdrimg.render import open_scene, render_pair
from hdrimg.style import STYLE_PRESETS


def regression(output: Path, reference: Path | None):
    output.mkdir(parents=True, exist_ok=False)
    scenes = [(p.stem, p, {}) for p in sorted(Path("outputs/phone_reanalysis/scenes").glob("*.tif"))]
    for stem, ev, sdr_ev in (("0N6A9479", 1.0, 0.0), ("0N6A9480", .95, -.45)):
        source=Path(f"outputs/phone_clear_optimized_{stem[-4:]}/{stem}_scene.tif")
        scene, info=open_scene(source)
        stride=max(1,int(np.ceil(max(info.width,info.height)/1024)))
        dest=output/f"{stem}_preview.tif"
        tifffile.imwrite(dest,np.asarray(scene[::stride,::stride],dtype=np.float32),photometric="rgb")
        scenes.append((stem,dest,dict(exposure_ev=ev,sdr_exposure_ev=sdr_ev,contrast=1.35,saturation=1.18)))
    result=[]
    for name,source,extra in scenes:
        for style in ("natural","phone-natural"):
            target=output/f"{name}_{style}";target.mkdir()
            opts=dict(auto_exposure=True,exposure_ev=None,development_ev=RAW_DEVELOPMENT_EV,
                      highlight_ev=0,hdr_strength=1,peak_nits=1000,style=STYLE_PRESETS[style]);opts.update(extra)
            render_pair(source,target/"sdr.jpg",target/"hdr.rgba16f",**opts)
            sdr=np.asarray(Image.open(target/"sdr.jpg"))
            hdr=(target/"hdr.rgba16f").read_bytes()
            row={"scene":name,"style":style,"sdr_pixels_sha256":hashlib.sha256(sdr.tobytes()).hexdigest(),
                 "hdr_sha256":hashlib.sha256(hdr).hexdigest()}
            if reference:
                old=reference/target.name
                row["sdr_equal"]=bool(np.array_equal(sdr,np.asarray(Image.open(old/"sdr.jpg"))))
                row["hdr_equal"]=hdr==(old/"hdr.rgba16f").read_bytes()
                if not row["sdr_equal"] or not row["hdr_equal"]:
                    raise AssertionError(f"Unchanged style regression: {name} {style}")
            result.append(row);print(name,style,"OK",flush=True)
    (output/"regression.json").write_text(json.dumps(result,indent=2)+"\n")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("output",type=Path);p.add_argument("--reference",type=Path)
    args=p.parse_args();regression(args.output,args.reference)
