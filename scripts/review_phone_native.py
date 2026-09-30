"""Compare native-resolution texture from original phone JPEGs and two audits.

Each fixed diagnostic region supplies a center; the crop is limited to 360
pixels or the region extent. The candidates use the matching normalized field
of view. This is a visual texture check, not pixel registration.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from audit_phone_clear import _read_hdr, _read_sdr, _panel
from hdrimg.color import REC2020_TO_DISPLAY_P3
from hdrimg.tools import resolve_tools, run_checked


def native_review(samples: Path, baseline: Path, current: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    report = (json.loads((current / "audit.json").read_text())
              if (current / "audit.json").exists() else
              {"pairs":[json.loads(p.read_text()) for p in sorted(current.glob('*_PXL*/record.json'))]})
    tools = resolve_tools(require_raw=False, require_exif=False)
    for row in report["pairs"]:
        phone = next(samples.glob(row["stem"]+".RAW-*.jpg"))
        ps, dimensions = _read_sdr(phone, step=1)
        folder = f'{row["id"]:02}_{row["stem"]}'
        bs, bd = _read_sdr(baseline / folder / "sdr.jpg", step=1)
        cs, cd = _read_sdr(current / folder / "sdr.jpg", step=1)
        bh = _read_hdr(baseline / folder / "hdr.rgba16f", bd, step=1) @ REC2020_TO_DISPLAY_P3.T
        ch = _read_hdr(current / folder / "hdr.rgba16f", cd, step=1) @ REC2020_TO_DISPLAY_P3.T
        with tempfile.TemporaryDirectory(prefix="phone-detail-") as temp:
            raw = Path(temp) / "phone.rgba16f"
            run_checked([tools.ultrahdr,"-m","1","-j",phone.resolve(),"-o","0","-O","4","-z",raw],
                        label="decode phone detail",timeout=600)
            ph = _read_hdr(raw, dimensions, step=1)
        regions = row["regions"]
        panel_rows = []
        records = {}
        for r, (name, region) in enumerate(regions.items()):
            panels = []
            for c, rgb in enumerate((ps,bs,cs,ph,bh,ch)):
                box = region["phone_box"] if c in (0,3) else region["repo_box"]
                height, width = rgb.shape[:2]
                x0,y0,x1,y1 = box
                cx,cy = (x0+x1)/2, (y0+y1)/2
                # Same normalized field of view, capped at 360 phone pixels.
                pw,phh = min(x1-x0,360/ps.shape[1]), min(y1-y0,360/ps.shape[0])
                crop = rgb[max(0,round((cy-phh/2)*height)):min(height,round((cy+phh/2)*height)),
                           max(0,round((cx-pw/2)*width)):min(width,round((cx+pw/2)*width))]
                # Keep one output pixel per original crop pixel. The generic
                # contact-sheet panel otherwise shrinks 360-pixel tall crops
                # to 351, which is unsuitable for a 100% edge inspection.
                panel = _panel(crop, hdr=c>=3,
                               width=crop.shape[1]+10, height=crop.shape[0]+34)
                assert panel.size == (crop.shape[1], crop.shape[0])
                # Small faces need an explicit nearest-neighbor magnified view.
                factor = min(3, max(1, min(360//max(panel.width,1),360//max(panel.height,1))))
                if factor>1:
                    panel = panel.resize((panel.width*factor,panel.height*factor),Image.Resampling.NEAREST)
                label = ("phone","baseline","candidate")[c%3]
                panels.append((panel, f'{name}: {label} '+('HDR preview' if c>=3 else 'SDR'),
                               f'{crop.shape[1]}x{crop.shape[0]} source pixels; x{factor}'))
                records[f'{name}/{label}/{"HDR" if c>=3 else "SDR"}'] = list(crop.shape[:2])
            panel_rows.append(panels)
        heights = [max(panel.height for panel, _, _ in row)+42 for row in panel_rows]
        column_width=max(380,max(panel.width+16 for row in panel_rows for panel,_,_ in row))
        sheet = Image.new("RGB", (6*column_width, sum(heights)), "#ededed")
        draw = ImageDraw.Draw(sheet)
        top = 0
        for height, panels in zip(heights, panel_rows):
            for c, (panel, label, dimensions_label) in enumerate(panels):
                sheet.paste(panel, (c*column_width+(column_width-panel.width)//2,top+6))
                draw.text((c*column_width+8,top+height-30),label,fill='#111')
                draw.text((c*column_width+8,top+height-15),dimensions_label,fill='#111')
            top += height
        sheet.save(output / f'{row["id"]:02}_native_regions.png')
        (output / f'{row["id"]:02}_crops.json').write_text(json.dumps(records,indent=2)+'\n')
        print(f'Native detail {row["id"]:02}',flush=True)
    (output/'README.md').write_text(
        'Original phone JPEG and native render crops, matched normalized fields of view. '
        'Columns: phone/base/candidate SDR, then the same HDR sequence. '
        'HDR previews share a fixed SDR mapping and do not replace an HDR display. '
        'Small crops are magnified with nearest-neighbor interpolation; dimensions are labelled. '
        'Phone/DNG framing and processing differ; these are not exact registered pixels.\n')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline',type=Path);p.add_argument('current',type=Path)
    p.add_argument('output',type=Path);p.add_argument('--samples',type=Path,default=Path('jpg_hdr_sample_effect'))
    a=p.parse_args();native_review(a.samples,a.baseline,a.current,a.output)
