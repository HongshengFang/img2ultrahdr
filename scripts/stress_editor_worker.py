"""Exercise the real JSONL worker with isolated RAW caches, bursts and recovery.

Run with the project Python; --prepared-cache points at an existing scenes cache.
Only immutable scene inputs are hard-linked; source RAWs are never modified.
"""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time

import numpy as np
from PIL import Image
from hdrimg.editor import EditRecipe, atomic_json, cached_render
from hdrimg.worker import task_group_members


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--prepared-cache', required=True, type=Path)
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    cache=root/'cache';support=root/'support';cache.mkdir();support.mkdir()
    preparations=[]
    for path in sorted((args.prepared_cache/'scenes').glob('*/complete.json')):
        record=json.loads(path.read_text())
        if not Path(record['source']['path']).is_file():continue
        target=cache/'scenes'/path.parent.name
        shutil.copytree(path.parent,target,copy_function=os.link)
        def remap(value):
            if isinstance(value,str):return value.replace(str(path.parent.resolve()),str(target))
            if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
            if isinstance(value,list):return [remap(v) for v in value]
            return value
        # Remove the shared record link before publishing isolated paths.
        (target/'complete.json').unlink()
        record=remap(record);atomic_json(target/'complete.json',record);preparations.append(record)
    assert len(preparations)>=2,'Need two real prepared RAW sources'
    engine=preparations[0]['engine'];assert all(p['engine']==engine for p in preparations)
    env=dict(os.environ,HDRIMG_ENGINE_ID=engine)
    app=Path.home()/'Applications/Img2UltraHDR.app/Contents/Resources'
    env.update(HDRIMG_ACCELERATOR=str(app/'libhdreditor.dylib'),HDRIMG_VISION_HELPER=str(app/'phone-subject'),HDRIMG_LOCAL_SELECTION_HELPER=str(app/'local-selection'))
    hashes={p['source']['path']:hashlib.sha256(Path(p['source']['path']).read_bytes()).hexdigest() for p in preparations}
    report=[];events=[];inbox=queue.Queue();counter=0;workers=[]
    def start():
        log=(root/f'worker-{len(workers)}.log').open('w')
        process=subprocess.Popen([sys.executable,'-m','hdrimg.worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,env=env)
        workers.append((process,log))
        def receive():
            for line in process.stdout:
                event=json.loads(line);events.append(event);inbox.put(event)
        threading.Thread(target=receive,daemon=True).start()
        return process
    process=start()
    def send(command, source=None, recipe=None, **extra):
        nonlocal counter
        counter+=1;identity=f'stress-{counter}'
        payload=dict(command=command,id=identity,session_id='isolated-stress',revision=counter,cache=str(cache),support=str(support),preview_format='float_v2',**extra)
        if source:payload['source']=source['path']
        if recipe is not None:payload['recipe']=asdict(recipe) if isinstance(recipe,EditRecipe) else recipe
        process.stdin.write(json.dumps(payload)+'\n');process.stdin.flush()
        return identity
    def wait(identity, expected='result', timeout=240):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            event=inbox.get(timeout=max(.01,deadline-time.monotonic()))
            if event.get('id')==identity and event.get('event') in ('result','error','cancelled'):
                assert event['event']==expected,event
                return event
        raise TimeoutError(identity)
    def record(name,**data):
        report.append(dict(name=name,**data));atomic_json(root/'stress.json',dict(requests=counter,checks=report));print(name,flush=True)
    def render(source,recipe):
        event=wait(send('preview',source,recipe));frame=event['result'];packet=frame['preview_packet']
        assert cached_render(Path(packet['hdr']).parent)
        for mode,ceiling in [('sdr',1),('hdr',1000/203+.003)]:
            values=np.fromfile(packet[mode],dtype='<f2').reshape(packet['height'],packet['width'],4)
            assert np.isfinite(values).all() and values[...,:3].min()>=0 and values[...,:3].max()<=ceiling
        return frame
    try:
        (support/'doctor.json').write_text('{interrupted startup')
        wait(send('hello'));record('Corrupt startup cache recovers with real dependency checks')
        for p in preparations:
            source=p['source'];frame=render(source,EditRecipe())
            record('Real RAW preview',source=Path(source['path']).name,width=frame['width'],height=frame['height'])
        source=preparations[-1]['source']
        for bad in [{'exposure_ev':999},{'exposure_ev':True},{'temperature_k':5600.5},{'local_adjustments':[{}]}, {'style':'invalid'}]:
            event=wait(send('preview',source,bad),expected='error');assert event['error_code']=='invalid_input'
        for raw in ['{malformed','[]','null','"文字"']:
            process.stdin.write(raw+'\n');process.stdin.flush()
        wait(send('hello'));record('Invalid settings and malformed requests leave controller usable')
        p=preparations[0];raw=Path(p['source']['path'])
        moved=root/('移动 '+raw.name.upper());os.link(raw,moved)
        relocated=render({'path':str(moved)},EditRecipe())
        assert relocated['source']['sha256']==p['source']['sha256']
        wait(send('preview',{'path':str(root/'missing.RAF')},EditRecipe()),expected='error')
        invalid=root/'not-a-raw.RAF';invalid.write_bytes(b'wrong data')
        wait(send('prepare',{'path':str(invalid)},EditRecipe()),expected='error')
        render(source,EditRecipe());record('Moved, uppercase, Unicode, missing and invalid RAW recover')
        for burst in range(3):
            started=time.monotonic()
            for i in range(12):
                chosen=preparations[i%len(preparations)]['source']
                send('preview',chosen,replace(EditRecipe(),exposure_ev=(i%7-3)/10))
                if i%3==0:send('cancel')
            wait(send('cancel'),expected='cancelled',timeout=30)
            children=subprocess.run(['pgrep','-P',str(process.pid)],capture_output=True,text=True)
            assert children.returncode==1,children.stdout
            frame=render(source,replace(EditRecipe(),exposure_ev=.1+burst*.01))
            record('Rapid source/edit/cancel burst recovers latest render',burst=burst,seconds=time.monotonic()-started)
        selected=wait(send('select_region',source,EditRecipe(),point=[.5,.5]))['result']
        assert selected['mode'] in ('soft','smart');record('Real local selection completes or uses fallback',mode=selected['mode'])
        if selected['mode']=='smart':
            smart=EditRecipe.from_dict(dict(local_adjustments=[dict(id='real-smart',mode='smart',mask_ref=selected['mask_ref'],amount=1)]))
            frame=render(source,smart)
            assert 'real-smart' in frame['preview_packet']['local_masks']
            record('Real Vision selection asset renders as an active smart region')
        regions=[dict(id=f'stress-{i}',mode='soft',shape='ellipse',center_x=i/7,center_y=1-i/7,radius_x=.03+ i*.02,radius_y=.2,rotation=-180+i*50,direction='brighten' if i%2 else 'darken',amount=1) for i in range(8)]
        recipe=EditRecipe.from_dict(dict(local_adjustments=regions,exposure_ev=-.2,highlight_ev=-2,shadow_ev=2))
        frame=render(source,recipe);record('Eight overlapping edge regions and extreme lighting remain finite')
        ninth={**regions[0],'id':'ninth'}
        wait(send('preview',source,dict(local_adjustments=regions+[ninth])),expected='error')
        invalid_ref='f'*64
        broken=EditRecipe.from_dict(dict(local_adjustments=[dict(id='missing-selection',mode='smart',mask_ref=invalid_ref,amount=1)]))
        error=wait(send('preview',source,broken),expected='error');assert error['error_code']=='local_asset_missing'
        disabled=replace(broken,local_adjustments=tuple(replace(r,enabled=False) for r in broken.local_adjustments))
        render(source,disabled);record('Missing active selection blocks processing; disabled selection remains editable')
        valid=replace(EditRecipe(),exposure_ev=.21)
        render(source,valid)
        draft=support/'drafts'/(source['sha256']+'.json');draft.parent.mkdir(exist_ok=True);draft.write_text('{interrupted save')
        recovered=wait(send('prepare',source,recipe=None))['result']
        assert recovered['recipe']['exposure_ev']==.21 and recovered.get('warning')
        record('Real worker preserves valid edit after a damaged newer draft')
        # Remove an isolated packet, then request the identical recipe again.
        path=Path(recovered['preview_packet']['hdr']);path.unlink()
        repaired=render(source,valid);assert not repaired['cache_hit'];record('Missing display buffer is rebuilt without corrupting RAW')
        # Kill the controller during a heavy task; the owned job watches its parent.
        heavy=send('full_render',source,replace(valid,exposure_ev=.2222));group=None
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            event=inbox.get(timeout=max(.01,deadline-time.monotonic()))
            if event.get('id')==heavy and event.get('phase_code')=='full_render':
                listing=subprocess.check_output(['ps','-axo','pid=,ppid=,pgid='],text=True)
                children=[int(f[2]) for line in listing.splitlines() if len(f:=line.split())==3 and int(f[1])==process.pid]
                assert len(children)==1,children
                group=children[0];break
        assert group is not None
        os.kill(process.pid,signal.SIGKILL);process.wait(timeout=5)
        deadline=time.monotonic()+5
        while task_group_members(group) and time.monotonic()<deadline:time.sleep(.1)
        assert not task_group_members(group)
        process=start();wait(send('hello'));render(source,valid)
        assert not list((cache/'renders').glob('.render-*'))
        record('Controller crash during full render stops its process group and restart recovers saved edit',group=group)
        destination=root/'导出 stress_ultrahdr.jpg'
        delivered=wait(send('export',source,valid,destination=str(destination),include_sdr=True,strip_metadata=False,overwrite=False))['result']
        companion=Path(delivered['exported_sdr'])
        assert destination.is_file() and companion.is_file() and delivered['validation']
        before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in [destination,companion]]
        wait(send('export',source,valid,destination=str(destination),include_sdr=True,overwrite=False),expected='error')
        assert before==[hashlib.sha256(p.read_bytes()).hexdigest() for p in [destination,companion]]
        record('Full-resolution HDR and SDR export; collisions preserve both existing files',width=delivered['width'],height=delivered['height'])
        private=wait(send('export',source,valid,destination=str(destination),include_sdr=True,strip_metadata=True,overwrite=True))['result']
        from hdrimg.metadata import read_output_metadata
        from hdrimg.tools import resolve_tools
        assert not read_output_metadata(destination,resolve_tools()).get('Make')
        assert private['validation']
        wait(send('export',source,valid,destination=str(root/'invalid.png')),expected='error')
        record('Overwrite and privacy export validate; unsupported output suffix is rejected')
        for path,sha in hashes.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha
        record('All original RAW fingerprints unchanged')
    finally:
        if process.poll() is None:
            try:send('close');process.stdin.close();process.wait(timeout=10)
            except Exception:process.kill();process.wait()
        for _,log in workers:log.close()
        atomic_json(root/'events.json',events)
    print(f'Passed {len(report)} scenarios across {counter} requests',flush=True)


if __name__=='__main__':main()
