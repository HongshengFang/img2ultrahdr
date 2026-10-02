"""Twelve original-size local exports per style; keep originals and old evidence intact."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import time

from hdrimg.editor import EditRecipe, EditorStore, atomic_json, check_source
from hdrimg.local_adjustments import LocalAdjustment, resolve_masks

p=argparse.ArgumentParser()
p.add_argument('output',type=Path)
p.add_argument('--preparations',type=Path,required=True)
p.add_argument('--selections',type=Path,required=True)
a=p.parse_args();a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
store=EditorStore(cache=a.output/'cache',support=a.output/'support',progress=lambda phase:print(phase,flush=True))
original_prepare=store.prepare
prepared_rows=json.loads(a.preparations.read_text())
selections={r['source']:r for r in json.loads((a.selections/'selection.json').read_text())}
names=['0N6A9034.CR2','0N6A9169.CR2','0N6A9406.CR2','0N6A9416.CR2','0N6A9453.CR2','DSCF8111.RAF',
       '0N6A9476.CR2','0N6A9479.CR2','0N6A9486.CR2','0N6A9507.CR2','DSCF8114.RAF','DSCF8129.RAF']
report=a.output/'exports.json';rows=json.loads(report.read_text()) if report.exists() else []
done={(r['source'],r['style']) for r in rows}
for name in names:
    source,_,_=store.restore(Path('pics')/name)
    selected=selections[name]['selection'];ref=selected['mask_ref']
    assets=store.support/'selection-assets'/source['sha256'];assets.mkdir(parents=True,exist_ok=True)
    shutil.copy2(a.selections/'support'/'selection-assets'/source['sha256']/(ref+'.png'),assets/(ref+'.png'))
    for style in ('phone-clear','phone-natural'):
        if (name,style) in done:continue
        started=time.monotonic();recipe=EditRecipe(style=style)
        if name in prepared_rows:
            prepared=json.loads(Path(prepared_rows[name]['record']).read_text())
            store.prepare=lambda *args,**kwargs:prepared
            provenance='retained accepted full RAW preparation: '+prepared_rows[name]['record']
        else:
            store.prepare=original_prepare
            prepared=store.prepare(source,recipe)
            provenance='fresh RAW development and current style analysis'
            # A completed retained scene is independent of development cache.
            # Keep enough room for original-size encoding on small disks.
            for child in (store.cache/'developments').iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        w,h=prepared['scene_info']['width'],prepared['scene_info']['height'];short=min(w,h)
        regions=(LocalAdjustment('foreground',mode='smart',mask_ref=ref,center_x=selected['point'][0],
                                  center_y=selected['point'][1],amount=.5),
                 LocalAdjustment('background',shape='ellipse',center_x=.23,center_y=.2,
                                 radius_x=.25*short/w,radius_y=.16*short/h,rotation=-28,direction='darken',amount=.5))
        recipe=replace(recipe,local_adjustments=regions)
        resolve_masks(regions,store.support,source['sha256'])
        store.remember(source,recipe);assert store.restore(Path(source['path']))[1]==recipe
        final=store.export(source,recipe,a.output/style/(Path(name).stem+'_ultrahdr.jpg'),include_sdr=True)
        check_source(source)
        row={'source':name,'style':style,'provenance':provenance,'width':final['width'],'height':final['height'],
             'validation':final['validation'],'exported':final['exported'],'exported_sdr':final['exported_sdr'],
             'render_seconds':final['elapsed_seconds'],'total_seconds':time.monotonic()-started,'recipe':final['recipe'],
             'physical_hdr_review':'pending human observation','source_unchanged':True}
        rows.append(row);atomic_json(report,rows);print(json.dumps(row),flush=True)
        # Only this run's regenerable buffers are removed. JPEGs, recipes,
        # durable selections, metrics, originals and previous evidence remain.
        for directory in ('renders','scenes','developments'):
            for child in (store.cache/directory).iterdir():
                if child.is_dir():shutil.rmtree(child)
                else:child.unlink()
assert len(rows)==24,len(rows)
