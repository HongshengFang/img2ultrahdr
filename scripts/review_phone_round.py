"""Build visual before/after evidence and fixed-region tables for one audit.

All images use color-managed sRGB previews. HDR previews have the same mapping
across candidates; the Ultra HDR originals remain the display-level evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from audit_phone_clear import _panel, _read_hdr, _read_sdr
from hdrimg.color import REC2020_TO_DISPLAY_P3


def images(folder: Path):
    sdr, dimensions = _read_sdr(folder / "sdr.jpg", step=1)
    hdr = _read_hdr(folder / "hdr.rgba16f", dimensions, step=1) @ REC2020_TO_DISPLAY_P3.T
    return sdr, hdr


def review(baseline: Path, current: Path, output: Path | None = None):
    previous = json.loads((baseline / "audit.json").read_text())
    report = json.loads((current / "audit.json").read_text())
    old_by_stem = {row["stem"]: row for row in previous["pairs"]}
    summary = []
    out = output or current / "review"
    out.mkdir(exist_ok=output is None)
    for row in report["pairs"]:
        old = old_by_stem[row["stem"]]
        name = f'{row["id"]:02}_{row["stem"]}'
        bs, bh = images(baseline / name)
        cs, ch = images(current / name)
        with np.load(current / name / "reference_preview.npz") as reference:
            ps, ph = reference["sdr"], reference["hdr"]
        cell_w = 510
        cell_h = min(715, round(max(rgb.shape[0]/rgb.shape[1] for rgb in (ps,bs,cs))*cell_w)+34)
        sheet = Image.new("RGB", (cell_w * 3, cell_h * 2), "#ededed")
        draw = ImageDraw.Draw(sheet)
        for r, triplet in enumerate(((ps, bs, cs), (ph, bh, ch))):
            for c, rgb in enumerate(triplet):
                panel = _panel(rgb, hdr=bool(r), width=cell_w, height=cell_h)
                sheet.paste(panel, (c*cell_w+(cell_w-panel.width)//2, r*cell_h))
                label = ("phone", "baseline", "candidate")[c] + (" HDR preview" if r else " SDR")
                draw.text((c*cell_w+8, (r+1)*cell_h-23), f'{row["id"]:02} {label}', fill="#111")
        sheet.save(out / f'{row["id"]:02}_before_after.png')
        regions = row.get("regions", {})
        crop_rows = []
        for r, (region_name, region) in enumerate(regions.items()):
            before = old["regions"][region_name]["repo"]
            phone, after = region["phone"], region["repo"]
            def luma_error(values, field):
                return abs(float(np.log2((values[field][1]+.02)/(phone[field][1]+.02))))
            def color_error(values):
                return float(np.linalg.norm(np.array(values["sdr_oklab_median"])[1:] - np.array(phone["sdr_oklab_median"])[1:]))
            summary.append({
                "id":row["id"], "scene":row.get("scene"), "region":region_name,
                "category":region["category"],
                "sdr_ev_error_before":luma_error(before,"sdr_y_p10_p50_p90"),
                "sdr_ev_error_after":luma_error(after,"sdr_y_p10_p50_p90"),
                "hdr_ev_error_before":luma_error(before,"hdr_y_p10_p50_p90"),
                "hdr_ev_error_after":luma_error(after,"hdr_y_p10_p50_p90"),
                "color_error_before":color_error(before), "color_error_after":color_error(after),
                "contrast_phone":phone["local_contrast_l_p90_p10"],
                "contrast_before":before["local_contrast_l_p90_p10"],
                "contrast_after":after["local_contrast_l_p90_p10"],
                "gain_ev_phone":phone["hdr_gain_ev_p10_p50_p90"][1],
                "gain_ev_before":before["hdr_gain_ev_p10_p50_p90"][1],
                "gain_ev_after":after["hdr_gain_ev_p10_p50_p90"][1],
            })
            panels = []
            for c, rgb in enumerate((ps, bs, cs, ph, bh, ch)):
                box = region["phone_box"] if c in (0,3) else region["repo_box"]
                h,w = rgb.shape[:2]
                x0,y0,x1,y1 = box
                crop = rgb[round(y0*h):round(y1*h),round(x0*w):round(x1*w)]
                panel = _panel(crop,hdr=c>=3,width=210,height=225)
                label = ("phone", "base", "new")[c%3]
                panels.append((panel, f'{region_name} {label} '+('H' if c>=3 else 'S')))
            crop_rows.append(panels)
        if regions:
            heights = [max(panel.height for panel, _ in row)+28 for row in crop_rows]
            crops = Image.new("RGB", (6*210, sum(heights)), "#ededed")
            cd = ImageDraw.Draw(crops)
            top = 0
            for height, panels in zip(heights, crop_rows):
                for c, (panel, label) in enumerate(panels):
                    crops.paste(panel, (c*210+(210-panel.width)//2, top+4))
                    cd.text((c*210+4,top+height-20),label,fill='#111')
                top += height
            crops.save(out / f'{row["id"]:02}_regions.png')
    with (out / "regions.csv").open("w") as f:
        writer=csv.DictWriter(f,fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)
    (out / "README.md").write_text(
        "# Review evidence\n\nBefore/after images: phone, baseline, candidate; SDR above and common-curve HDR previews below.\n"
        "Region crops: phone, baseline, candidate SDR followed by the same HDR sequence.\n"
        "Fixed rectangles are manually labelled diagnostics, not semantic segmentation or exact registration.\n"
        "Absent categories are recorded in audit.json. No combined quality score is used.\n"
    )


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("baseline",type=Path);p.add_argument("current",type=Path)
    p.add_argument("--output",type=Path,help="new evidence folder; existing folders are not replaced")
    args=p.parse_args();review(args.baseline,args.current,args.output)
