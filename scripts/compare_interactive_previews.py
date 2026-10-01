import argparse,json
from pathlib import Path
import numpy as np
from PIL import Image
from hdrimg.editor import atomic_json
from hdrimg.color import rec2020_to_linear_srgb, linear_srgb_to_oklab, DISPLAY_P3_TO_SRGB
p=argparse.ArgumentParser();p.add_argument('cases',type=Path);p.add_argument('gpu',type=Path);a=p.parse_args()
cases=json.loads(a.cases.read_text());frames=json.loads((a.gpu/'gpu.json').read_text());rows=[]
for frame in frames:
 case=cases[frame['case']];packet=case['target']['preview_packet'];hdr=frame['hdr']
 shape=(packet['height'],packet['width'],4)
 actual=np.fromfile(frame['path'],dtype='<f2').reshape(shape)[...,:3].astype('float32')
 expected=np.fromfile(packet['hdr' if hdr else 'sdr'],dtype='<f2').reshape(shape)[...,:3].astype('float32')
 coefficients=np.array([.26270021,.67799807,.05930172] if hdr else [.22897456,.69173852,.07928691],np.float32)
 y=actual@coefficients;ref=expected@coefficients;valid=(y>.02)&(ref>.02)
 ev=np.abs(np.log2(np.maximum(y,1e-8)/np.maximum(ref,1e-8)))
 quantiles=np.percentile(ev[valid],[50,95]).tolist()
 row={k:case[k] for k in ['source','style','variant']};row.update(hdr=hdr,ev_median_p95=quantiles,finite=bool(np.isfinite(actual).all()),gpu_ms=frame['gpu_ms'],passed=quantiles[0]<=.05 and quantiles[1]<=.2)
 if not row['passed']:
  difference=np.clip(ev/.3,0,1);im=np.stack([difference,np.zeros_like(difference),np.zeros_like(difference)],axis=-1)
  Image.fromarray((im*255).astype('uint8')).save(a.gpu/f"{frame['case']}-{'hdr' if hdr else 'sdr'}-difference.png")
 rows.append(row)
atomic_json(a.gpu/'comparison.json',rows)
print(json.dumps({'passed':sum(r['passed'] for r in rows),'total':len(rows),'failures':[r for r in rows if not r['passed']]},indent=2))
