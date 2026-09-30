"""Verify the completed experiment against current source, inputs and artifacts."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def run(root):
    configuration=json.loads((root/'candidate/configuration.json').read_text())
    implementation={name:Path(name).is_file() and sha(Path(name))==digest
                    for name,digest in configuration['source_sha256'].items()}
    samples=Path('jpg_hdr_sample_effect')
    phone_inputs={name:sha(samples/name)==digest
                  for name,digest in configuration['fixtures_sha256'].items()}
    report=json.loads((root/'candidate/audit.json').read_text())
    cameras=[json.loads(p.read_text()) for p in sorted((root/'cameras').glob('*/audit.json'))]
    expected={p.name for p in Path('pics').iterdir() if p.suffix.lower() in {'.cr2','.raf'}}
    camera_inputs={Path(r['source']).name:sha(Path(r['source']))==r['source_sha256'] for r in cameras}
    camera_sources=[]
    for r in cameras:
        valid=all(Path(k).is_file() and sha(Path(k))==v for k,v in r['recipe'].items()
                  if k.startswith('src/hdrimg/'))
        valid &= r['recipe'].get('validation_script_sha256')==sha(Path('scripts/validate_phone_clear_cameras.py'))
        camera_sources.append(valid)
    originals={}
    for r in report['pairs']:
        p=Path(r['manifest']);m=json.loads(p.read_text())
        originals[f'phone-{r["id"]:02}']=bool(list(p.parent.glob('*_ultrahdr.jpg'))) and m['validation']['passed']
    for r in cameras:
        originals[Path(r['source']).name]=Path(r['ultrahdr']).is_file() and r['validation']['passed']
    result={
        'source_unchanged':all(implementation.values()),'source_files':implementation,
        'phone_inputs_unchanged':all(phone_inputs.values()),'phone_inputs':phone_inputs,
        'phone_count':report['pair_count'],'phone_complete':report['pair_count']==12,
        'camera_count':len(cameras),'camera_complete':set(camera_inputs)==expected,
        'camera_inputs_unchanged':all(camera_inputs.values()),'camera_inputs':camera_inputs,
        'camera_implementation_current':bool(camera_sources) and all(camera_sources),
        'all_final_files_present_and_validated':all(originals.values()),'final_files':originals,
        'scope':'Reproducibility and completed file validation; not a physical HDR or visual-quality acceptance.'}
    result['passed']=all(result[k] for k in ['source_unchanged','phone_inputs_unchanged',
        'phone_complete','camera_complete','camera_inputs_unchanged','camera_implementation_current',
        'all_final_files_present_and_validated'])
    (root/'reproduction-check.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if not isinstance(v,dict)},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    run(p.parse_args().root)
