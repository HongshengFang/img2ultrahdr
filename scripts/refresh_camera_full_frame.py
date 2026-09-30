"""Measure complete final camera frames without the paired-phone audit crop."""
import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from audit_phone_clear import _decode_transfer, _metrics, _panel
from audit_phone_raw_first import reduced
from hdrimg.color import REC2020_TO_DISPLAY_P3
from hdrimg.tools import resolve_tools, run_checked


def refresh(path,tools):
    record=json.loads(path.read_text())
    if record.get('metric_sampling')=='full frame; area reduction to 1024 after final decoding':return False
    original=Path(record['ultrahdr'])
    with Image.open(original) as image:
        w,h=image.size
        sdr=reduced(_decode_transfer(np.asarray(image,np.float32)/255))
    with tempfile.TemporaryDirectory(prefix='hdrimg-full-frame-audit-') as temp:
        decoded=Path(temp)/'hdr.rgba16f'
        run_checked([tools.ultrahdr,'-m','1','-j',original,'-o','0','-O','4','-z',decoded],
                    label='decode full camera frame',timeout=600)
        raw=np.memmap(decoded,dtype='<f2',mode='r',shape=(h,w,4))
        hdr=reduced(np.asarray(raw[...,:3],np.float32));del raw
    record['previous_paired_audit_frame_metrics']=record['metrics']
    record['metrics']=_metrics(sdr,hdr,hdr_gamut='rec2020')
    record['metric_sampling']='full frame; area reduction to 1024 after final decoding'
    record['full_frame_audit_script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    hdr=hdr @ REC2020_TO_DISPLAY_P3.T
    np.savez_compressed(path.parent/'previews.npz',sdr=sdr,hdr=hdr)
    for mode,rgb in [('sdr',sdr),('hdr',hdr)]:
        _panel(rgb,hdr=mode=='hdr',width=800,height=1050).save(path.parent/f'{mode}-preview.jpg',quality=95)
    path.write_text(json.dumps(record,indent=2)+'\n')
    print('Full frame',path.parent.name,flush=True)
    return True


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    p.add_argument('--wait-for',type=int,default=0);args=p.parse_args()
    tools=resolve_tools(require_raw=False,require_exif=False)
    while True:
        paths=sorted(args.root.glob('*/audit.json'))
        for path in paths:
            try:refresh(path,tools)
            except json.JSONDecodeError:continue  # A newly published row is still closing.
        if not args.wait_for or len(paths)>=args.wait_for:break
        time.sleep(5)
