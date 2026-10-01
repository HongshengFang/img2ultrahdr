"""Real RAW white-balance/cache checks; output is isolated from historical samples."""
import argparse,json,time
from pathlib import Path
from dataclasses import replace
from hdrimg.editor import EditorStore,EditRecipe,atomic_json
p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
s=EditorStore(cache=a.output/'cache',support=a.output/'support',progress=lambda x:print(x,flush=True));rows=[]
for filename in ['DSCF8111.RAF','0N6A9034.CR2']:
 source,_,_=s.restore(Path('pics')/filename)
 for mode in ['camera','custom','auto']:
  recipe=replace(EditRecipe(),white_balance=mode,temperature_k=5600,tint=20 if mode=='custom' else 0)
  before=time.monotonic();prepared=s.prepare(source,recipe)
  meter={p.name:p.stat().st_mtime_ns for p in (s.cache/'metering').glob('*.npy')}
  result=s.render(source,recipe);original_stat=Path(prepared['scene']).stat().st_mtime_ns
  adjusted=s.render(source,replace(recipe,exposure_ev=.2,shadow_ev=.4,saturation=1.1))
  assert Path(prepared['scene']).stat().st_mtime_ns==original_stat
  assert meter=={p.name:p.stat().st_mtime_ns for p in (s.cache/'metering').glob('*.npy')}
  assert prepared['raw_development']['white_balance']['resolved']==mode
  if mode!='auto':assert not prepared['raw_development']['white_balance']['skin_guard']['applied']
  row={'source':filename,'mode':mode,'elapsed_seconds':time.monotonic()-before,
       'raw_reused_for_exposure_shadow_saturation':True,'camera_metering_unchanged':True,
       'white_balance':prepared['raw_development']['white_balance'],'preview':result['sdr']}
  rows.append(row);atomic_json(a.output/'white-balance.json',rows);print(json.dumps(row),flush=True)
 # Switching style does not reset user deltas and Natural is renderable from RAW.
 recipe=replace(EditRecipe(),style='phone-natural',exposure_ev=.2,shadow_ev=.4)
 result=s.render(source,recipe)
 rows.append({'source':filename,'style':'phone-natural','recipe':result['recipe'],'preview':result['sdr']})
 atomic_json(a.output/'white-balance.json',rows)
