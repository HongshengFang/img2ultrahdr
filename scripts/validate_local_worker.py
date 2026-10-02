"""Exercise the real JSONL controller, selections, cancellation and damaged assets."""
import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

from hdrimg.editor import EditRecipe, EditorStore, atomic_json, digest
from hdrimg.local_adjustments import LocalAdjustment

p=argparse.ArgumentParser();p.add_argument('output',type=Path)
p.add_argument('--preparations',type=Path,required=True);p.add_argument('--selections',type=Path,required=True)
p.add_argument('--helper',type=Path,required=True);a=p.parse_args();a.output=a.output.resolve()
os.environ['HDRIMG_ENGINE_ID']='local-v04-jsonl-check'
os.environ['HDRIMG_LOCAL_SELECTION_HELPER']=str(a.helper.resolve())
store=EditorStore(cache=a.output/'cache',support=a.output/'support')
records=json.loads(a.preparations.read_text());prepared=json.loads(Path(records['DSCF8111.RAF']['record']).read_text())
source,_,_=store.restore(Path('pics/DSCF8111.RAF'));recipe=EditRecipe()
prepared=dict(prepared,engine=store.engine,source=source)
prepared['key']=digest({'source':source['sha256'],'engine':store.engine,'development':recipe.development_key()})
atomic_json(store.cache/'scenes'/prepared['key']/'complete.json',prepared)
selected=next(r for r in json.loads((a.selections/'selection.json').read_text()) if r['source']=='DSCF8111.RAF')['selection']
ref=selected['mask_ref'];asset=store.support/'selection-assets'/source['sha256']/(ref+'.png');asset.parent.mkdir(parents=True,exist_ok=True)
contents=(a.selections/'support'/'selection-assets'/source['sha256']/(ref+'.png')).read_bytes();asset.write_bytes(contents)
w,h=prepared['scene_info']['width'],prepared['scene_info']['height'];short=min(w,h)
regions=(LocalAdjustment('foreground',mode='smart',mask_ref=ref,amount=.5),)+tuple(
    LocalAdjustment(f'soft-{i}',shape='ellipse',radius_x=.2*short/w,radius_y=.13*short/h,center_x=.15+i*.09,
                    center_y=.3+i*.05,rotation=i*9,direction='brighten' if i%2 else 'darken',amount=.5) for i in range(7))
recipe=replace(recipe,local_adjustments=regions)
err=(a.output/'stderr.log').open('w')
proc=subprocess.Popen([sys.executable,'-m','hdrimg.worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err)
selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ);buffer=b'';queue=[];revision=0;checks=[];events=[]
def send(command,**kwargs):
    global revision
    revision+=1;id=f'request-{revision}'
    request=dict(command=command,id=id,session_id='local-photo',revision=revision,source=source['path'],expected_sha=source['sha256'],
                 recipe=asdict(recipe),preview_format='float_v2',cache=str(store.cache),support=str(store.support),**kwargs)
    proc.stdin.write(json.dumps(request).encode()+b'\n');proc.stdin.flush();return id
def next_event(timeout=120):
    global buffer
    deadline=time.monotonic()+timeout
    while not queue:
        if time.monotonic()>deadline:raise TimeoutError('JSONL result')
        if not selector.select(.1):continue
        chunk=os.read(proc.stdout.fileno(),65536)
        if not chunk:raise RuntimeError('worker stopped')
        lines=(buffer+chunk).split(b'\n');buffer=lines.pop();queue.extend(lines)
    value=json.loads(queue.pop(0));events.append(value);return value
def finish(id):
    while True:
        event=next_event()
        if event.get('id')==id and event.get('event') in ('result','error','cancelled'):return event
def check(value,label):
    assert value,label
    checks.append(label);atomic_json(a.output/'checks.json',{'passed':len(checks),'checks':checks});print(label,flush=True)
try:
    hello=finish(send('hello'));check('local_adjustments_v1' in hello['capabilities'] and 'float_preview_v2' in hello['capabilities'],'Real worker advertises both new capabilities')
    exact=finish(send('preview'));check(exact['result']['preview_packet']['version']==2 and len(exact['result']['recipe']['local_adjustments'])==8,'Eight-region nested recipe passes JSONL and yields exact v2 frames')
    restored=store.restore(Path(source['path']))[1];check(restored==recipe,'Eight regions and mask references restore from disk')
    selection=finish(send('select_region',point=selected['point']));check(selection['result']['mode']=='smart','Actual Vision selection succeeds from the unadjusted exact base')
    check(store.restore(Path(source['path']))[1]==recipe,'Selection requests never overwrite the editing recipe')
    for i in range(3):
        recipe=replace(recipe,local_adjustments=tuple(replace(r,amount=.13+i*.03) for r in regions))
        id=send('preview')
        while True:
            event=next_event()
            if event.get('id')==id and event.get('event')=='progress':break
        cancel=send('cancel');reply=finish(cancel)
        check(reply['event']=='cancelled' and not list((store.cache/'renders').glob('.render-*')),f'Continuous cancellation {i+1} leaves no partial render')
    recipe=replace(recipe,local_adjustments=regions)
    asset.unlink()
    destination=a.output/'must-not-exist.jpg'
    missing=finish(send('export',destination=str(destination)))
    check(missing['event']=='error' and missing['error_code']=='local_asset_missing' and missing['region_id']=='foreground' and not destination.exists(),'Missing active selection reports its region and blocks publication')
    disabled=replace(regions[0],enabled=False);recipe=replace(recipe,local_adjustments=(disabled,)+regions[1:])
    repairable=finish(send('preview'));check(repairable['event']=='result' and 'foreground' not in repairable['result']['preview_packet']['local_masks'],'Unavailable inactive selection remains editable')
    asset.write_bytes(contents);recipe=replace(recipe,local_adjustments=regions)
    repaired=finish(send('preview'));check(repaired['event']=='result','Restoring a verified asset recovers exact preview')
finally:
    try:proc.stdin.write(b'{"command":"close"}\n');proc.stdin.flush()
    except BrokenPipeError:pass
    proc.wait(timeout=10);selector.close();err.close();atomic_json(a.output/'events.json',events)
