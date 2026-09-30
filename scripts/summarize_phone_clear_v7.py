"""Assemble recorded validation gates; this script never promotes a preset."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image
import numpy as np
from hdrimg.color import DISPLAY_P3_TO_SRGB, linear_srgb_to_oklab
from hdrimg.style import STYLE_PRESETS


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def compare_sdr(before,after):
    with Image.open(before) as a, Image.open(after) as b:
        if a.size!=b.size: return {'same_dimensions':False}
        maximum=0; changed=0; total=0; count=0
        for y in range(0,a.height,128):
            box=(0,y,a.width,min(a.height,y+128))
            delta=np.abs(np.asarray(a.crop(box),np.int16)-np.asarray(b.crop(box),np.int16))
            maximum=max(maximum,int(delta.max()));changed+=int(np.count_nonzero(delta.max(axis=-1)))
            total+=int(delta.sum());count+=delta.size
        return {'same_dimensions':True,'pixel_count':a.width*a.height,'changed_pixels':changed,
                'changed_pixel_fraction':changed/(a.width*a.height),'max_code_value_delta':maximum,
                'mean_code_value_delta':total/count}


def hdr_color(preview,regions=None):
    """Separate HDR chroma drift from luminance gain using homogeneous OKLab."""
    with np.load(preview) as data:
        a=data['repo_sdr'] if 'repo_sdr' in data else data['sdr']
        b=data['repo_hdr'] if 'repo_hdr' in data else data['hdr']
    labs=[linear_srgb_to_oklab(np.maximum(x,0) @ DISPLAY_P3_TO_SRGB.T) for x in (a,b)]
    relative=[x[...,1:]/np.maximum(x[...,:1],.02) for x in labs]
    boxes={'whole':[0,0,1,1],**{name:region['repo_box'] for name,region in (regions or {}).items()}}
    result={}
    for name,box in boxes.items():
        h,w=a.shape[:2];x0,y0,x1,y1=[round(v*n) for v,n in zip(box,[w,h,w,h])]
        ra,rb=[x[y0:y1,x0:x1] for x in relative]
        visible=(labs[0][y0:y1,x0:x1,0]>.10)&(labs[1][y0:y1,x0:x1,0]>.10)
        if not np.any(visible):continue
        ca,cb=[np.linalg.norm(x[visible],axis=-1) for x in (ra,rb)]
        result[name]={'sdr_relative_chroma_median':float(np.median(ca)),
                      'hdr_relative_chroma_median':float(np.median(cb)),
                      'normalized_ab_change_median':float(np.median(np.linalg.norm((ra-rb)[visible],axis=-1))),
                      'visible_pixels':int(np.count_nonzero(visible))}
    return result


def compact_metrics(metrics):
    return {key:metrics[key] for key in ['sdr_percentiles','hdr_percentiles','hdr_peak',
        'hdr_above_white_fraction','hdr_above_2x_white_fraction','midtone_gain_median','gain_bands']}


def compact_regions(before,after):
    result={}
    for name,region in after.items():
        a,b=before[name]['repo'],region['repo']
        result[name]={'category':region['category'],
            'sdr_median_before_after':[a['sdr_y_p10_p50_p90'][1],b['sdr_y_p10_p50_p90'][1]],
            'hdr_median_before_after':[a['hdr_y_p10_p50_p90'][1],b['hdr_y_p10_p50_p90'][1]],
            'sdr_chroma_before_after':[a['sdr_chroma_median'],b['sdr_chroma_median']],
            'hdr_gain_ev_candidate':b['hdr_gain_ev_p10_p50_p90']}
    return result


def run(root,destination,final):
    iteration=read(root/'iteration.json') or {}
    candidate=read(root/'candidate/audit.json');baseline=read(root/'decoded-baseline/audit.json')
    cameras=[read(p) for p in sorted((root/'cameras').glob('*/audit.json'))]
    expected=sorted(p.name for p in Path('pics').iterdir() if p.suffix.lower() in {'.cr2','.raf'})
    actual=sorted(Path(r['source']).name for r in cameras)
    same=read(root/'natural-same-input/audit.json')
    full_frame=bool(cameras) and all(r.get('metric_sampling','').startswith('full frame;') for r in cameras)
    if final and (actual!=expected or not full_frame or not same or same['count']!=13):
        raise ValueError('Full camera batch and same-input Natural comparison must finish first')
    preservation=read(root/'preservation-before.json')
    preserved={};locations={}
    for name,digest in preservation.items():
        if not name.startswith('result/'):continue
        path=Path(name)
        if not path.exists():
            # Preserve an observed user directory reorganization; never move
            # files back or infer equality merely from matching filenames.
            relocated=Path('result_natural')/path.name
            if relocated.exists():path=relocated
        locations[name]=str(path)
        preserved[name]=path.exists() and sha(path)==digest
    natural=[]
    for path in sorted((root/'natural-regression').glob('*/audit.json')):
        row=read(path);stem=path.parent.name
        comparison=dict(row['natural_comparison'])
        if not comparison['sdr_pixels_equal']:
            comparison['sdr_difference']=compare_sdr(
                root/'baseline/phone-natural'/stem/f'{stem}_ultrahdr.jpg',
                path.parent/f'{stem}_ultrahdr.jpg')
        natural.append({'sample':stem,**comparison})
    frozen=root/'natural-frozen-repeat/0N6A9406/0N6A9406_ultrahdr.jpg'
    before=root/'baseline/phone-natural/0N6A9406/0N6A9406_ultrahdr.jpg'
    repeated=None
    if frozen.exists():
        repeated={'same_frozen_code_jpeg_identical':sha(frozen)==sha(before),
                  'sdr_difference':compare_sdr(before,frozen)}
    transition=read(root/'transition-probe/audit.json')
    pipeline_edges=read(root/'pipeline-edges/audit.json')
    visual=read(root/'visual-review.json') or {}
    display=read(root/'display-review.json') or {}
    camera_visual=read(root/'camera-visual-review.json') or {}
    reviewed_cameras=[r['sample'] for r in camera_visual.get('rows',[])]
    camera_visual_complete=(len(reviewed_cameras)==len(expected)
        and set(reviewed_cameras)=={Path(name).stem for name in expected})
    reproduction=read(root/'reproduction-check.json')
    promotion=read(root/'promotion.json') or {}
    default_equivalence=read(root/'promotion-preflight/same-input/audit.json')
    natural_after_default=read(root/'promotion-preflight/natural-same-input/audit.json')
    default_smoke=read(root/'promotion-preflight/default-smoke-comparison.json')
    current_version=STYLE_PRESETS['phone-clear'].algorithm_version
    promoted=promotion.get('accepted',False) and current_version==7
    tests=None
    if (root/'tests.xml').exists():
        suite=ET.parse(root/'tests.xml').getroot().find('testsuite')
        tests={k:int(suite.get(k,'0')) for k in ['tests','failures','errors','skipped']}
    phone=[]
    for old,new in zip(baseline['pairs'],candidate['pairs']):
        local=new['render']['tone_mapping'].get('phone_clear_local',{})
        phone.append({'id':new['id'],'scene':new['scene'],'baseline':compact_metrics(old['repo']),
                      'candidate':compact_metrics(new['repo']),'regions':compact_regions(old['regions'],new['regions']),
                      'codec_validation':new['validation'],
                      'codec_luminance_relative_error':new['codec_luminance_relative_error'],
                      'native_raw_wb':new['raw_development']['white_balance'].get('native_temperature_bias'),
                      'local_exposure':{k:local[k] for k in ['face_count','ev_min','ev_max','garment_confidence','texture_enhancement'] if k in local},
                      'hdr_color_at_normalized_lightness':hdr_color(
                          root/'candidate'/f'{new["id"]:02}_{new["stem"]}'/'previews.npz',new['regions'])})
    camera_rows=[{'source':Path(r['source']).name,'source_sha256':r['source_sha256'],
                  'recipe_sha256':r['recipe_sha256'],'validation':r['validation'],
                  'metrics':compact_metrics(r['metrics']),'metric_sampling':r.get('metric_sampling','paired phone audit crop'),
                  'elapsed_seconds':r['elapsed_seconds'],
                  'hdr_color_at_normalized_lightness':hdr_color(root/'cameras'/Path(r['source']).stem/'previews.npz')} for r in cameras]
    manifest=read(Path(candidate['pairs'][0]['manifest']))
    report={'finalized':final,'candidate':'phone-clear-v7','candidate_round':root.name,'promoted':promoted,
      'default':f'phone-clear-v{current_version}','natural':'phone-natural-v8','tools':manifest['tools'],'runtime':manifest['runtime'],
      'criteria':'docs/phone-clear-v7-criteria.json',
      'gates':{'phone_codec_passed':all(r['validation']['passed'] and r['codec_luminance_relative_error']['passed'] for r in candidate['pairs']),
               'camera_batch_complete':actual==expected,'camera_count':len(cameras),'camera_expected':len(expected),
               'camera_codec_passed':bool(cameras) and all(r['validation']['passed'] for r in cameras),
               'camera_full_frame_metrics':full_frame,
               'camera_recipe_consistent':bool(cameras) and len({r['recipe_sha256'] for r in cameras})==1,
               'camera_inputs_unchanged':all(sha(Path(r['source']))==r['source_sha256'] for r in cameras) if final else None,
               'strict_spatial_transition_passed':transition['passed'],
               'final_codec_transition_passed':pipeline_edges['passed'] if pipeline_edges else None,
               'at_least_eight_visual_improvements':visual.get('improved_count',0)>=8 if visual else None,
               'no_clear_visual_regressions':visual.get('regressed_count')==0 if visual else None,
               'phone_edges_passed':visual.get('edge_veto_passed'),
               'camera_visual_complete':camera_visual_complete,
               'camera_visual_passed':camera_visual_complete and camera_visual.get('passed',False)
                   and all(r.get('passed',False) for r in camera_visual.get('rows',[])),
               'physical_mac_hdr_acceptance':display.get('status','not_performed'),
               'physical_mac_hdr_passed':display.get('status')=='user_confirmed_hdr_direction_and_natural_transitions',
               'automated_tests_passed':bool(tests) and tests['tests']>0 and tests['failures']==tests['errors']==0,
               'natural_same_float_input_passed':same['passed'] if same else None,
               'proposed_default_same_float_input_passed':default_equivalence['passed'] if default_equivalence else None,
               'natural_after_proposed_default_passed':natural_after_default['passed'] if natural_after_default else None,
               'proposed_default_full_raw_codec_passed':default_smoke.get('default_codec_validation_passed') if default_smoke else None,
               'frozen_candidate_reproduction_passed':reproduction.get('passed') if reproduction else None,
               'original_accepted_files_unchanged':len(preserved)==37 and all(preserved.values())},
      'tests':tests,'doctor':read(root/'doctor.json'),'original_accepted_files':preserved,
      'original_accepted_file_current_locations':locations,
      'natural_raw_repeats':natural,'natural_frozen_repeat':repeated,'natural_same_input':same,
      'transition_probe':{'total':transition['cases'],
          'passed_count':sum(x['strict_monotone_passed'] for x in transition['rows']),
          'failed_count':sum(not x['strict_monotone_passed'] for x in transition['rows']),
          'source':str(root/'transition-probe/audit.json')},
      'pipeline_transition_probe':pipeline_edges,
      'preview_review':visual,'display_review':display,'camera_visual_review':camera_visual,
      'promotion':promotion,
      'default_dispatch_equivalence':default_equivalence,
      'natural_after_default_change':natural_after_default,
      'default_full_raw_smoke':default_smoke,
      'gallery_browser_validation':read(root/'review/browser-check-latest.json'),
      'final_artifact_checksums':str(root/'final-artifacts-sha256.json'),
      'limitations':visual.get('remaining_concerns',[])+[
          'Preview inspection and automated decoding cannot establish physical HDR display acceptance.'],
      'phone_pairs':phone,'camera_samples':camera_rows,
      'reproduction':iteration.get('documentation','docs/phone-clear-v7.md'),
      'implementation_and_fixture_check':reproduction,
      'full_metrics':str(root/'candidate/audit.json')+' and '+str(root/'cameras/*/audit.json'),
      'local_gallery':str(root/'review/index.html')}
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'finalized':final,'gates':report['gates'],'tests':tests},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    p.add_argument('--output',type=Path,default=Path('docs/phone-clear-v7-validation.json'))
    p.add_argument('--final',action='store_true');args=p.parse_args()
    run(args.root,args.output,args.final)
