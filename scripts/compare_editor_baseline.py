"""Measure repeat-RAW differences separately from same-float equivalence."""
import json,time,os,argparse
from pathlib import Path
import numpy as np
from PIL import Image
from hdrimg.editor import atomic_json
p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--follow-pid',type=int);a=p.parse_args()
rows=[];seen=set();baseline=Path('outputs/phone-clear-v8-20260930/best-ultrahdr-37')
def linear(x):return np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)
while True:
 suite=json.loads((a.root/'raw-suite.json').read_text())
 for r in suite:
  if r['source'] in seen:continue
  original=baseline/Path(r['exported']).name
  if not original.exists():continue
  arrays=[]
  for path in [original,Path(r['exported'])]:
   with Image.open(path) as im:
    scale=1536/max(im.size);size=tuple(round(x*scale) for x in im.size)
    arrays.append(linear(np.asarray(im.resize(size,Image.Resampling.BOX),np.float32)/255))
  before,after=arrays;yb=before@np.array([.22897456,.69173852,.07928691],np.float32);ya=after@np.array([.22897456,.69173852,.07928691],np.float32)
  valid=(yb>.02)&(ya>.02);ev=np.abs(np.log2(ya[valid]/yb[valid]))
  chroma=np.abs(after/np.maximum(ya[...,None],1e-5)-before/np.maximum(yb[...,None],1e-5))
  row={'source':r['source'],'sdr_repeat_ev_median_p95':np.percentile(ev,[50,95]).tolist(),
       'downsampled_sdr_pixels_equal':bool(np.array_equal(before,after)),
       'normalized_channel_difference_p95':float(np.percentile(chroma[valid],95))}
  rows.append(row);seen.add(r['source']);atomic_json(a.root/'repeat-raw-comparison.json',rows);print(json.dumps(row),flush=True)
 if not a.follow_pid:break
 try:os.kill(a.follow_pid,0)
 except ProcessLookupError:break
 time.sleep(5)
