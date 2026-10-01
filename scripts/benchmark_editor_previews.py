"""Measure final editor preview code on existing full-RAW preparations.

Optional pause PIDs must identify this task's validation processes, never user
applications. Each pause lasts one render and is resumed in a finally block.
"""
import argparse,json,os,signal,time,shutil,copy
from dataclasses import replace
from pathlib import Path
import numpy as np
from hdrimg.editor import EditorStore,EditRecipe,atomic_json
p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--pause-pid',type=int,action='append',default=[]);p.add_argument('--repeats',type=int,default=3)
p.add_argument('--floating-preview',action='store_true')
p.add_argument('--prepared-cache',type=Path,action='append',default=[],help='Optional existing preparation cache; otherwise prepare the six RAW inputs again')
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
names=['0N6A9034.CR2','0N6A9169.CR2','0N6A9406.CR2','0N6A9416.CR2','0N6A9453.CR2','DSCF8111.RAF']
cases={}
for cache in a.prepared_cache:
 for path in (cache/'scenes').glob('*/complete.json'):
  d=json.loads(path.read_text());name=Path(d['source']['path']).name;wb=d['raw_development']['white_balance']
  if name in names and wb['resolved']=='auto' and 'native_temperature_bias' in wb and name not in cases:cases[name]=(cache,d)
# Own hard links protect the fixed input set from the batch runner's LRU.
# Immutable scene files can share storage; manifests are rewritten locally.
owned_cache=(a.output/'cache').resolve()
for name in names:
 if name not in cases:
  os.environ.pop('HDRIMG_ENGINE_ID',None)
  fresh=EditorStore(cache=owned_cache,support=a.output/'support')
  source,_,_=fresh.restore(Path('pics')/name)
  cases[name]=(owned_cache,fresh.prepare(source,EditRecipe()))
  continue
 cache,prepared=cases[name];old=Path(prepared['scene']).parent;new=owned_cache/'scenes'/prepared['key']
 new.mkdir(parents=True,exist_ok=True)
 for file in old.iterdir():
  if file.name!='complete.json':os.link(file,new/file.name)
 def remap(value):
  if isinstance(value,str):return value.replace(str(old),str(new))
  if isinstance(value,list):return [remap(v) for v in value]
  if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
  return value
 prepared=remap(prepared);atomic_json(new/'complete.json',prepared);cases[name]=(owned_cache,prepared)
rows=[];paused=[]
def resume():
 for group in paused:
  try:os.killpg(group,signal.SIGCONT)
  except ProcessLookupError:pass
 paused.clear()
def interrupted(sig,frame):
 resume();raise SystemExit(128+sig)
signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
for repeat in range(a.repeats):
 for name in names:
  cache,prepared=cases[name]
  os.environ['HDRIMG_ENGINE_ID']=prepared['engine']
  store=EditorStore(cache=cache,support=a.output/'support')
  recipe=replace(EditRecipe(),exposure_ev=.181+repeat*.01,shadow_ev=.2)
  try:
   for pid in a.pause_pid:
    try:
     group=os.getpgid(pid)
     if group==os.getpgrp():raise ValueError('Refusing to stop benchmark group')
     os.killpg(group,signal.SIGSTOP);paused.append(group)
    except ProcessLookupError:pass
   started=time.monotonic();result=store.render(prepared['source'],recipe,remember=False,floating_preview=a.floating_preview);elapsed=time.monotonic()-started
  finally:resume()
  assert not result['cache_hit']
  row={'source':name,'repeat':repeat,'seconds':elapsed,'render_encode_seconds':result['elapsed_seconds'],
       'width':result['width'],'height':result['height'],'prepared_engine':prepared['engine']}
  rows.append(row);values=[x['seconds'] for x in rows]
  atomic_json(a.output/'benchmark.json',{'rows':rows,'median_seconds':float(np.median(values)),
      'p95_seconds':float(np.percentile(values,95)),
      'method':'Latest renderer, 1536px, complete scene analysis, all protections. Existing unchanged float inputs; excludes RAW prep and worker import. Other listed validation jobs paused during each sample.',
      'floating_preview':a.floating_preview})
  print(json.dumps(row),flush=True);time.sleep(.5)
