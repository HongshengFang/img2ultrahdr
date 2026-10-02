"""Compare the production Metal local stage with precise CPU frames."""
import argparse
import json
from pathlib import Path
import numpy as np
from hdrimg.editor import atomic_json

p=argparse.ArgumentParser();p.add_argument('cases',type=Path);p.add_argument('gpu',type=Path);a=p.parse_args()
cases=json.loads(a.cases.read_text());frames=json.loads((a.gpu/'gpu.json').read_text());rows=[]
for frame in frames:
    case=cases[frame['case']];packet=case['target']['preview_packet'];hdr=frame['hdr']
    shape=(packet['height'],packet['width'],4)
    actual=np.fromfile(frame['path'],dtype='<f2').reshape(shape)[...,:3].astype(np.float32)
    expected=np.fromfile(packet['hdr' if hdr else 'sdr'],dtype='<f2').reshape(shape)[...,:3].astype(np.float32)
    coefficients=np.array([.26270021,.67799807,.05930172] if hdr else [.22897456,.69173852,.07928691],np.float32)
    y=actual@coefficients;ref=expected@coefficients;valid=(y>.02)&(ref>.02)
    ev=np.abs(np.log2(np.maximum(y,1e-8)/np.maximum(ref,1e-8)))
    median,p95=np.percentile(ev[valid],[50,95]).tolist();maximum=float(ev[valid].max())
    near_black=float(np.max(np.abs(actual[~valid]-expected[~valid]))) if (~valid).any() else 0
    combined=case['variant']=='global-combined'
    passed=bool(np.isfinite(actual).all()) and (median<=.05 and p95<=.2 if combined else p95<=.01 and maximum<=.05 and near_black<=.0001)
    row={k:case[k] for k in ('source','style','variant')}
    row.update(hdr=hdr,ev_median=median,ev_p95=p95,ev_max=maximum,near_black_max_abs=near_black,gpu_ms=frame['gpu_ms'],passed=passed)
    rows.append(row)
atomic_json(a.gpu/'comparison.json',rows)
summary={'passed':sum(r['passed'] for r in rows),'total':len(rows),'failures':[r for r in rows if not r['passed']]}
print(json.dumps(summary,indent=2))
assert len(rows)==len(cases)*2 and all(r['passed'] for r in rows), 'Local GPU accuracy failed'
