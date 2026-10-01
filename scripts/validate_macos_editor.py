"""Reproducible editor checks. Writes evidence only into a new output directory."""
import argparse
from dataclasses import asdict, replace
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import numpy as np
import tifffile
from PIL import Image
from hdrimg.editor import EditorStore, EditRecipe, atomic_json, file_digest
from hdrimg.render import render_pair, open_scene
from hdrimg.raw import RAW_DEVELOPMENT_EV
from hdrimg.style import STYLE_PRESETS
from hdrimg.ultrahdr import validate_ultrahdr
from hdrimg.tools import resolve_tools


def frozen_package(path):
    spec=importlib.util.spec_from_file_location('frozen_app_baseline',path/'__init__.py',submodule_search_locations=[str(path)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return importlib.import_module(spec.name+'.render'),importlib.import_module(spec.name+'.style')


def equivalence(args):
    old,oldstyle=frozen_package(args.baseline.resolve())
    rows=[]
    scenes=sorted(Path('outputs/phone-clear-v8-20260930/camera-scenes').glob('*/scene-preview.tif'))
    scenes+=sorted(Path('outputs/phone-clear-v8-20260930/candidate-phone').glob('*/scene-preview.tif'))
    if args.limit: scenes=scenes[:args.limit]
    for i,scene in enumerate(scenes):
        out=args.output/f'{i:02d}-{scene.parent.name}';out.mkdir()
        for style in ['phone-clear','phone-natural']:
            opts=dict(auto_exposure=True,exposure_ev=None,highlight_ev=0,hdr_strength=1,peak_nits=1000,development_ev=-2)
            old.render_pair(scene,out/f'{style}-old.jpg',out/f'{style}-old.raw',style=oldstyle.STYLE_PRESETS[style],**opts)
            render_pair(scene,out/f'{style}-new.jpg',out/f'{style}-new.raw',style=STYLE_PRESETS[style],**opts)
            row={'scene':str(scene),'style':style,'sdr_equal':file_digest(out/f'{style}-old.jpg')==file_digest(out/f'{style}-new.jpg'),
                 'hdr_equal':file_digest(out/f'{style}-old.raw')==file_digest(out/f'{style}-new.raw')}
            rows.append(row);atomic_json(args.output/'equivalence.json',rows);print(json.dumps(row),flush=True)
            for p in out.glob('*.raw'): p.unlink()
    if not rows or not all(r['sdr_equal'] and r['hdr_equal'] for r in rows): raise RuntimeError('Pixel equivalence failed')


def raw_suite(args):
    if args.cache_gib is not None:
        import hdrimg.editor
        hdrimg.editor.CACHE_LIMIT = int(args.cache_gib * 1024**3)
    store=EditorStore(cache=args.output/'cache',support=args.output/'support',progress=lambda p:print(p,flush=True))
    sources=sorted(p for p in Path('pics').iterdir() if p.suffix.lower() in {'.cr2','.raf'})
    if args.limit: sources=sources[:args.limit]
    report=args.output/'raw-suite.json'
    rows=json.loads(report.read_text()) if args.resume and report.exists() else []
    completed={row['source'] for row in rows}
    for sourcepath in sources:
        if sourcepath.name in completed:
            continue
        started=time.monotonic();source,_,_=store.restore(sourcepath);recipe=EditRecipe()
        preview=store.render(source,recipe,floating_preview=args.floating_preview)
        if args.floating_preview:
            linear_preview=np.fromfile(preview['preview_packet']['sdr'],dtype='<f2').reshape(preview['height'],preview['width'],4)[...,:3].astype(np.float32)
        final=store.export(source,recipe,args.output/(sourcepath.stem+'_ultrahdr.jpg'),include_sdr=True)
        from hdrimg.color import DISPLAY_P3_TO_XYZ
        def srgb_eotf(x): return np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)
        small=None if args.floating_preview else np.asarray(Image.open(preview['sdr']),np.float32)/255
        large=np.asarray(Image.open(final['exported_sdr']).resize((preview['width'],preview['height']),Image.Resampling.BOX),np.float32)/255
        py=(linear_preview if args.floating_preview else srgb_eotf(small))@DISPLAY_P3_TO_XYZ[1];fy=srgb_eotf(large)@DISPLAY_P3_TO_XYZ[1]
        mask=(py>.02)&(fy>.02)
        ev=np.abs(np.log2(py[mask]/fy[mask]))
        row={'source':sourcepath.name,'preview_seconds':preview['elapsed_seconds'],
             'full_seconds':final['elapsed_seconds'],'total_seconds':time.monotonic()-started,
             'sdr_preview_ev_median_p95':np.percentile(ev,[50,95]).tolist(),
             'validation':final['validation'],'exported':final['exported']}
        rows.append(row);atomic_json(args.output/'raw-suite.json',rows);print(json.dumps(row),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['equivalence','raw-suite']);parser.add_argument('output',type=Path)
    parser.add_argument('--baseline',type=Path,help='Explicit frozen hdrimg source directory for equivalence mode');parser.add_argument('--limit',type=int)
    parser.add_argument('--floating-preview',action='store_true')
    parser.add_argument('--cache-gib',type=float)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if args.mode=='equivalence' and (args.baseline is None or not (args.baseline/'__init__.py').is_file()):
        parser.error('equivalence requires --baseline pointing to an existing frozen hdrimg source directory')
    args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=args.resume)
    equivalence(args) if args.mode=='equivalence' else raw_suite(args)
