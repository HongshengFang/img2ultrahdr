"""Fast single-factor comparisons on retained linear RAW previews, never JPEG edits.

These are development previews, not final codec or full-resolution acceptance.
Use a fresh directory for each run so rejected experiments remain reviewable.
"""
import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image, ImageDraw

from hdrimg.color import REC2020_TO_DISPLAY_P3
from hdrimg.phone_tone import PhoneSceneDecision
from hdrimg.render import render_pair
from hdrimg.style import PHONE_CLEAR_CANDIDATE
from audit_phone_clear import _panel, _metrics


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    sources = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(Path('src/hdrimg').rglob('*.py'))}
    (args.output/'implementation.json').write_text(json.dumps(sources, indent=2)+'\n')
    records = []
    for folder in sorted((args.previous/'candidate').glob('[0-9][0-9]_*')):
        number = int(folder.name[:2])
        if args.ids and number not in {int(n) for n in args.ids.split(',')}:
            continue
        start = time.monotonic()
        target = args.output/folder.name
        target.mkdir()
        old = json.loads((folder/'record.json').read_text())
        decision = PhoneSceneDecision(**old['render']['tone_mapping']['phone_clear_metering']['decision'])
        info = render_pair(folder/'scene-preview.tif', target/'sdr.jpg', target/'hdr.rgba16f',
            auto_exposure=True, exposure_ev=None, development_ev=-2, highlight_ev=0,
            hdr_strength=1, peak_nits=1000, style=PHONE_CLEAR_CANDIDATE,
            _scene_decision=decision, _linear_sdr_output=target/'sdr-linear.tif')
        a = tifffile.imread(target/'sdr-linear.tif')
        b = np.memmap(target/'hdr.rgba16f', dtype='<f2', mode='r',shape=(*a.shape[:2],4))[...,:3].astype(np.float32)
        b = b @ REC2020_TO_DISPLAY_P3.T
        with np.load(folder/'previews.npz') as previous, np.load(
                args.previous/'decoded-baseline'/folder.name/'previews.npz') as baseline:
            arrays = [previous['phone_sdr'], baseline['repo_sdr'], previous['repo_sdr'], a,
                      previous['phone_hdr'], baseline['repo_hdr'], previous['repo_hdr'], b]
        sheet = Image.new('RGB', (1600,1160),'#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for i, values in enumerate(arrays):
            panel = _panel(values,hdr=i>=4,width=400,height=555)
            x,y=(i%4)*400,(i//4)*580
            sheet.paste(panel,(x+(400-panel.width)//2,y))
            draw.text((x+10,y+557),f'{number:02} '+['Phone','V6','Rejected V7','Candidate'][i%4]+
                      (' HDR mapped' if i>=4 else ' SDR'),fill='black')
        sheet.save(target/'comparison.jpg',quality=95)
        row = {'id':number,'preview_only':True,'render':asdict(info),
               'metrics':_metrics(a,b,hdr_gamut='display-p3'),
               'seconds':time.monotonic()-start}
        (target/'audit.json').write_text(json.dumps(row,indent=2)+'\n')
        np.savez_compressed(target/'previews.npz',repo_sdr=a,repo_hdr=b)
        records.append(row)
        print(f'{number:02}: {row["seconds"]:.1f}s; {info.tone_mapping["phone_clear_local"].get("native_hdr_edge_constraint")}',flush=True)
    (args.output/'audit.json').write_text(json.dumps({'scope':'float preview iteration; no codec acceptance',
                                                    'pairs':records},indent=2)+'\n')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('previous',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--ids');run(p.parse_args())
