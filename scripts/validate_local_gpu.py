"""Bounded scratch space for real CPU/Metal local lighting comparisons."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from hdrimg.editor import atomic_json

p=argparse.ArgumentParser();p.add_argument('cases',type=Path);p.add_argument('output',type=Path)
p.add_argument('--checker',type=Path,required=True);p.add_argument('--prune-scratch',action='store_true')
a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
cases=json.loads(a.cases.read_text());report=[]
keep=set()
for key in ('anchor','target'):
    keep.add(Path(cases[0][key]['preview_packet']['sdr']).parent.resolve())
for start in range(0,len(cases),14):
    group=cases[start:start+14];work=a.output/f'group-{start//14:02d}';work.mkdir(parents=True,exist_ok=True)
    casefile=work/'cases.json';atomic_json(casefile,group)
    subprocess.run([str(a.checker.resolve()),str(casefile),str(work)],check=True)
    subprocess.run(['.venv/bin/python','scripts/compare_local_previews.py',str(casefile),str(work)],check=True)
    report+=json.loads((work/'comparison.json').read_text());atomic_json(a.output/'comparison.json',report)
    if a.prune_scratch:
        for frame in work.glob('*.rgba16f'):frame.unlink()
        parents=set()
        for case in group:
            for key in ('anchor','target'):parents.add(Path(case[key]['preview_packet']['sdr']).parent.resolve())
        for parent in parents-keep:
            if parent.is_relative_to(a.cases.parent.resolve()/'cache'/'renders'):shutil.rmtree(parent,ignore_errors=True)
assert len(report)==len(cases)*2 and all(r['passed'] for r in report)
print(f'Passed all {len(report)} GPU/CPU comparisons')
