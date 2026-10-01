"""Cancel real RAW development, pixel rendering and encoding, verifying process cleanup."""
import argparse,json,os,selectors,subprocess,sys,tempfile,time
from pathlib import Path
from hdrimg.editor import atomic_json
p=argparse.ArgumentParser();p.add_argument('output',type=Path)
p.add_argument('--cache',type=Path,default=Path('outputs/app-validation/benchmark-isolated/cache'))
p.add_argument('--source',type=Path,default=Path('pics/DSCF8111.RAF'))
args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
prepared=next(json.loads(path.read_text()) for path in (args.cache/'scenes').glob('*/complete.json')
    if Path(json.loads(path.read_text())['source']['path']).resolve()==args.source.resolve())
rows=[]
unique_ev=.347+(time.time()%997)*1e-7
for stage,command,cache,recipe,floating in [
    ('正在显影 RAW','prepare',args.output/'fresh-cache',{},False),
    ('正在生成全尺寸图像','full_render',args.cache.resolve(),{'exposure_ev':unique_ev},False),
    ('正在编码 Ultra HDR','preview',args.cache.resolve(),{'exposure_ev':unique_ev+.001},False),
    ('正在更新预览','preview',args.cache.resolve(),{'exposure_ev':unique_ev+.002},True)]:
    env=dict(os.environ,HDRIMG_ENGINE_ID=prepared['engine'])
    proc=subprocess.Popen([sys.executable,'-m','hdrimg.worker'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ)
    request={'command':command,'id':'work','session_id':'s','revision':1,'source':str(args.source.resolve()),
        'recipe':recipe,'cache':str(cache),'support':str(args.output/'support')}
    if floating: request['preview_format']='float_v1'
    proc.stdin.write(json.dumps(request)+'\n');proc.stdin.flush();deadline=time.monotonic()+180;cancelled=False;group=None
    buffered=b'';ready=[]
    try:
        while time.monotonic()<deadline:
            if not ready:
                if not selector.select(.2):continue
                chunk=os.read(proc.stdout.fileno(),65536)
                if not chunk:raise RuntimeError('worker closed')
                lines=(buffered+chunk).split(b'\n');buffered=lines.pop();ready.extend(lines)
                if not ready:continue
            event=json.loads(ready.pop(0))
            if event.get('event')=='error':raise RuntimeError(event)
            if event.get('event')=='result' and not cancelled:raise RuntimeError('Requested stage was skipped: '+str(event.get('result',{}).get('cache_hit')))
            if event.get('event')=='progress' and event.get('phase')==stage and not cancelled:
                time.sleep(.1)
                ps=subprocess.check_output(['ps','-axo','pid=,ppid=,pgid='],text=True)
                group=next(int(f[2]) for line in ps.splitlines() if len(f:=line.split())==3 and int(f[1])==proc.pid)
                start=time.monotonic();proc.stdin.write('{"command":"cancel","id":"cancel","session_id":"s","revision":2}\n');proc.stdin.flush();cancelled=True
            if event.get('id')=='cancel' and event.get('event')=='cancelled':
                elapsed=time.monotonic()-start
                ps=subprocess.check_output(['ps','-axo','pid=,pgid='],text=True)
                remaining=[line for line in ps.splitlines() if len(f:=line.split())==2 and int(f[1])==group]
                assert not remaining,remaining
                assert elapsed<5,elapsed
                incomplete=list((cache/'scenes').glob(f'.prepare-{group}-*'))+list((cache/'renders').glob(f'.render-{group}-*'))
                assert not incomplete,incomplete
                row={'stage':stage,'cancel_seconds':elapsed,'process_group_removed':True,'no_partial_cache':True}
                rows.append(row);atomic_json(args.output/'cancellation.json',rows);print(row,flush=True);break
        else:raise TimeoutError(stage)
    finally:
        try: proc.stdin.write('{"command":"close"}\n');proc.stdin.flush()
        except BrokenPipeError: pass
        _,err=proc.communicate(timeout=8);selector.close()
        if err:print(err,file=sys.stderr)
