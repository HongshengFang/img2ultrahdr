"""Export declared diagnostic crops from final files, without modifying photos."""
import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from audit_phone_clear import _decode_transfer, _panel
from hdrimg.color import REC2020_TO_DISPLAY_P3
from hdrimg.tools import resolve_tools, run_checked


def run(root, specification):
    regions=json.loads(specification.read_text())
    output=root/'review/camera-extra';output.mkdir(exist_ok=True)
    tools=resolve_tools(require_raw=False,require_exif=False)
    for stem, centers in regions.items():
        record=root/'cameras'/stem/'audit.json'
        if not record.exists():continue
        original=Path(json.loads(record.read_text())['ultrahdr'])
        if all((output/f'{stem}-{name}.png').exists() for name in centers):continue
        with Image.open(original) as source, tempfile.TemporaryDirectory(prefix='hdrimg-extra-crops-') as temporary:
            w,h=source.size
            decoded=Path(temporary)/'hdr.rgba16f'
            run_checked([tools.ultrahdr,'-m','1','-j',original,'-o','0','-O','4','-z',decoded],
                        label='decode declared diagnostic regions',timeout=600)
            raw=np.memmap(decoded,dtype='<f2',mode='r',shape=(h,w,4))
            records={}
            for name,(cx,cy) in centers.items():
                x0=max(0,min(w-512,round(cx*w)-256));y0=max(0,min(h-512,round(cy*h)-256))
                box=(x0,y0,min(w,x0+512),min(h,y0+512))
                sdr=_decode_transfer(np.asarray(source.crop(box),np.float32)/255)
                hdr=np.asarray(raw[box[1]:box[3],box[0]:box[2],:3],np.float32)@REC2020_TO_DISPLAY_P3.T
                sheet=Image.new('RGB',(1024,546),'#eeeeee');draw=ImageDraw.Draw(sheet)
                for column,rgb in enumerate((sdr,hdr)):
                    panel=_panel(rgb,hdr=column==1,width=522,height=546)
                    assert panel.size==(512,512)
                    sheet.paste(panel,(column*512,0))
                    draw.text((column*512+8,516),f'{stem} {name}: '+('SDR' if column==0 else 'HDR mapped')+' / 100%',fill='#111')
                sheet.save(output/f'{stem}-{name}.png')
                records[name]={'box_original_pixels':box,'resampling':False}
            del raw
        (output/f'{stem}.json').write_text(json.dumps({'source':str(original),'regions':records,
            'scope':'Explicit review coordinates only; never imported by the renderer.'},indent=2)+'\n')
        print('Extra regions',stem,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('specification',type=Path)
    args=parser.parse_args();run(args.root,args.specification)
