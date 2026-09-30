"""Build final-file boundary evidence and the camera validation gallery.

All coordinates below are diagnostic ROIs in the original photographs. They
are intentionally outside the renderer and never influence production gains.
"""
from __future__ import annotations

import argparse
import html
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from audit_phone_clear import _decode_transfer, _panel
from hdrimg.color import DISPLAY_P3_TO_SRGB, DISPLAY_P3_TO_XYZ, REC2020_TO_DISPLAY_P3, linear_srgb_to_oklab
from hdrimg.tools import resolve_tools, run_checked


STYLE = '''body{margin:24px;background:#11151b;color:#eef1f5;font:16px system-ui}p{line-height:1.6;color:#b4bdca}a{color:#99c5ff}button,select{background:#242c38;color:white;font:inherit;padding:8px;border:1px solid #536174;border-radius:6px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}figure{margin:0;overflow:auto;background:#090c10}figcaption{padding:12px}img{display:block;width:100%;height:70vh;object-fit:contain}.native img{width:auto;height:auto;max-width:none}nav{margin:16px 0;display:flex;gap:14px;flex-wrap:wrap}table{border-collapse:collapse}td,th{border:1px solid #536174;padding:10px;text-align:left}'''


def accepted(root):
    path=root/'promotion.json'
    return path.exists() and json.loads(path.read_text()).get('accepted',False)


def bounds(box, width, height):
    return tuple(round(v*n) for v, n in zip(box, (width, height, width, height)))


def color_metrics(rgb):
    lab = linear_srgb_to_oklab(rgb @ DISPLAY_P3_TO_SRGB.T)
    return {'median_y': float(np.median(rgb @ DISPLAY_P3_TO_XYZ[1])),
            'median_chroma_over_lightness': float(np.median(
                np.linalg.norm(lab[..., 1:], axis=-1)/np.maximum(lab[..., 0], .02)))}


def edges(root, output):
    tools = resolve_tools(require_raw=False, require_exif=False)
    output.mkdir(parents=True, exist_ok=True)
    crops = {'0N6A9406': (.30,.70,.55,.99), '0N6A9416': (.30,.68,.63,.96)}
    regions = {'0N6A9416': {'foot':(.434,.900,.449,.910), 'ankle':(.445,.847,.460,.855),
                'white_wall':(.68,.65,.76,.80), 'calf':(.415,.768,.432,.795), 'knee':(.422,.704,.445,.720)}}
    records, sources = {}, {}
    for stem, box in crops.items():
        records[stem], sources[stem] = {}, {}
        for label, folder in [('baseline',root/'baseline/phone-clear'),
                              ('natural',root/'baseline/phone-natural'), ('candidate',root/'cameras')]:
            path = (folder/stem/f'{stem}_ultrahdr.jpg').resolve()
            sources[stem][label] = path.as_uri()
            with Image.open(path) as source:
                w,h = source.size
                pixel_box = bounds(box,w,h)
                rgb = _decode_transfer(np.asarray(source.crop(pixel_box),np.float32)/255)
                values = {}
                if stem == '0N6A9406':
                    for name,b in [('leg_edge',(210,600,230,720)), ('leg_center',(380,600,430,720)),
                                   ('background',(140,600,170,720))]:
                        x0,y0,x1,y1=b; values[name]=color_metrics(rgb[y0:y1,x0:x1])
                else:
                    for name,b in regions[stem].items():
                        part = _decode_transfer(np.asarray(source.crop(bounds(b,w,h)),np.float32)/255)
                        values[name] = color_metrics(part)
                records[stem][label] = {'regions_sdr':values, 'original':str(path),
                                       'native_crop_original_pixels':list(pixel_box)}
                _panel(rgb,hdr=False,width=rgb.shape[1]+10,height=rgb.shape[0]+34).save(
                    output/f'{stem}-{label}-sdr.png')
                # A fixed transect makes new contour bands inspectable; natural
                # photographic gradients are not assumed to be monotone.
                if stem == '0N6A9406':
                    records[stem][label]['sdr_transect_y600_720'] = np.median(
                        rgb[600:720] @ DISPLAY_P3_TO_XYZ[1],axis=0).tolist()
            with tempfile.TemporaryDirectory(prefix='hdrimg-edge-review-') as temp:
                decoded=Path(temp)/'decoded.rgba16f'
                run_checked([tools.ultrahdr,'-m','1','-j',path,'-o','0','-O','4','-z',decoded],
                            label='decode native edge review',timeout=600)
                raw=np.memmap(decoded,dtype='<f2',mode='r',shape=(h,w,4))
                x0,y0,x1,y1=pixel_box
                rgb=np.asarray(raw[y0:y1,x0:x1,:3],np.float32) @ REC2020_TO_DISPLAY_P3.T
                _panel(rgb,hdr=True,width=rgb.shape[1]+10,height=rgb.shape[0]+34).save(
                    output/f'{stem}-{label}-hdr.png')
                records[stem][label]['hdr_crop_metrics']=color_metrics(rgb)
                del raw
        print('Boundary review',stem,flush=True)
    report={'metric_note':'C/L compares chroma relative to lightness; luminance is measured separately. Fixed diagnostic ROIs, no automatic visual acceptance.',
            'hdr_preview':'Common SDR mapping of decoded final HDR; direct original files are linked separately.',
            'samples':records}
    (output/'edges-metrics.json').write_text(json.dumps(report,indent=2)+'\n')
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>腿部边缘对照</title><style>STYLE</style>
<h1>历史腿部边缘 · 最终成品对照</h1><p>左：Clear V6；中：已接受的 Natural V8；右：Clear V7 候选。检查腿部、脚背与白墙交界。颜色相对亮度的变化单独记录；不靠模糊原图隐藏轮廓。</p>
<nav><select id="scene"><option>0N6A9406</option><option>0N6A9416</option></select><select id="mode"><option value="sdr">SDR 原尺寸区域</option><option value="hdr">HDR 区域统一映射</option><option value="original">HDR 完整原图</option></select><button id="scale">切换 100% / 适屏</button><a href="index.html">十二张对照</a></nav>
<div class="grid"><figure><figcaption>Clear V6 · <a id="link-baseline">打开成品</a></figcaption><img id="baseline"></figure><figure><figcaption>Natural V8 · <a id="link-natural">打开成品</a></figcaption><img id="natural"></figure><figure><figcaption>Clear V7 候选 · <a id="link-candidate">打开成品</a></figcaption><img id="candidate"></figure></div>
<p>100% 模式按图像像素显示，可在每一栏滚动检查；适屏用于判断整体过渡。HDR 区域图是统一映射预览，真实 HDR 验收请打开完整成品。</p>
<script>const rows=ROWS;const scene=document.querySelector('#scene'),mode=document.querySelector('#mode');function update(){for(const key of ['baseline','natural','candidate']){document.querySelector('#'+key).src=mode.value==='original'?rows[scene.value][key]:`${scene.value}-${key}-${mode.value}.png`;document.querySelector('#link-'+key).href=rows[scene.value][key]}}scene.onchange=mode.onchange=update;document.querySelector('#scale').onclick=()=>document.querySelector('.grid').classList.toggle('native');update();</script></html>'''
    if accepted(root):page=page.replace('Clear V7 候选','Clear V7')
    (output/'edges.html').write_text(page.replace('STYLE',STYLE).replace('ROWS',json.dumps(sources)))
    return report


def cameras(root,output):
    rows=[]
    extra_labels={'leg-wall':'小腿与白墙', 'lit-leg-wall':'受光腿部与白墙',
        'leg-shadow':'腿间与接触阴影', 'foot-shadow':'脚背与阴影',
        'calf-wall':'小腿与白墙', 'hand-paper':'手部与纸张', 'paper-edge':'纸张边缘',
        'knee-background':'腿部与背景', 'hair-clothing':'头发与衣领',
        'white-fabric':'浅色衣物纹理', 'knee-wall':'腿部与白墙',
        'calf-background':'小腿与背景', 'hair-background':'发丝与背景',
        'left-face':'左侧人物面部', 'right-face':'右侧人物面部',
        'left-calf':'左侧人物小腿', 'right-calf':'右侧人物小腿',
        'ankle-floor':'脚踝与地板', 'fringe-natural-comparison':'原有彩边与 Natural 对照',
        'flare-natural-comparison':'原有眩光与 Natural 对照'}
    review_path=root/'camera-visual-review.json'
    reviewed=json.loads(review_path.read_text()) if review_path.exists() else {}
    findings={r['sample']:r['finding'] for r in reviewed.get('rows',[])}
    for path in sorted((root/'cameras').glob('*/audit.json')):
        report=json.loads(path.read_text()); stem=path.parent.name
        rows.append({'stem':stem, 'sdr':(path.parent/'sdr-preview.jpg').resolve().as_uri(),
                     'hdr':(path.parent/'hdr-preview.jpg').resolve().as_uri(),
                     'original':Path(report['ultrahdr']).resolve().as_uri(),
                     'native':(output/'camera-native'/f'{stem}.png').resolve().as_uri()
                         if (output/'camera-native'/f'{stem}.png').exists() else None,
                     'finding':findings.get(stem,''),
                     'extras':[{'label':extra_labels.get(p.stem[len(stem)+1:],p.stem[len(stem)+1:]), 'href':p.resolve().as_uri()}
                         for p in sorted((output/'camera-extra').glob(f'{stem}-*.png'))],
                     'passed':report['validation']['passed']})
    opts=''.join(f'<option>{html.escape(r["stem"])}</option>' for r in rows)
    overview=[]
    for start in range(0,len(rows),9):
        group=rows[start:start+9]
        for mode in ['sdr','hdr']:
            sheet=Image.new('RGB',(1200,1260),'#eeeeee');draw=ImageDraw.Draw(sheet)
            for i,row in enumerate(group):
                source=root/'cameras'/row['stem']/f'{mode}-preview.jpg'
                with Image.open(source) as im:
                    panel=im.copy();panel.thumbnail((390,386))
                x=(i%3)*400;y=(i//3)*420
                sheet.paste(panel,(x+(400-panel.width)//2,y));draw.text((x+8,y+396),row['stem'],fill='#111')
            filename=f'cameras_{start+1:02}_{start+len(group):02}_{mode}.jpg'
            sheet.save(output/filename,quality=95)
            overview.append(f'<a href="{filename}">{start+1}–{start+len(group)} {"SDR" if mode=="sdr" else "HDR 映射"}</a>')
    page='''<!doctype html><html lang="zh"><meta charset="utf-8"><title>相机 RAW 泛化检查</title><style>STYLE</style>
<h1>Canon / Fuji RAW · Clear V7 候选</h1><p>已完成 COUNT / 37 张。每张都从 RAW 完整显影、编码并检查最终解码结果。自动通过只表示文件与数据检查通过，仍需查看人物过渡、材质和实际 HDR 光感。</p><nav><select id="scene">OPTIONS</select><button id="prev">上一张</button><button id="next">下一张</button><button id="scale">切换 100% / 适屏</button><a href="index.html">十二张手机对照</a><a id="original">打开 Ultra HDR 成品</a></nav><nav>OVERVIEWS</nav>
<div class="grid"><figure><figcaption>SDR 预览</figcaption><img id="sdr"></figure><figure><figcaption>HDR 统一映射预览</figcaption><img id="hdr"></figure><figure><figcaption>最终 Ultra HDR 原图</figcaption><img id="full"></figure></div><p id="status"></p>
<script>const rows=ROWS;const scene=document.querySelector('#scene');function update(){let r=rows.find(x=>x.stem===scene.value);if(!r)return;document.querySelector('#sdr').src=r.sdr;document.querySelector('#hdr').src=r.hdr;document.querySelector('#full').src=r.original;document.querySelector('#original').href=r.original;document.querySelector('#status').textContent=r.passed?'成品数据检查通过':'成品数据检查未通过'}scene.onchange=update;document.querySelector('#prev').onclick=()=>{scene.selectedIndex=(scene.selectedIndex+rows.length-1)%rows.length;update()};document.querySelector('#next').onclick=()=>{scene.selectedIndex=(scene.selectedIndex+1)%rows.length;update()};document.querySelector('#scale').onclick=()=>document.querySelector('.grid').classList.toggle('native');update();</script></html>'''
    page=page.replace('<a id="original">打开 Ultra HDR 成品</a>',
        '<a id="original">打开 Ultra HDR 成品</a><a id="native">原尺寸局部</a>')
    page=page.replace("document.querySelector('#status').textContent=", 
        "document.querySelector('#native').href=r.native||r.original;document.querySelector('#native').hidden=!r.native;document.querySelector('#status').textContent=")
    page=page.replace('自动通过只表示文件与数据检查通过，',
        '本轮候选尚待完整验收，默认仍为 V6。自动通过只表示文件与数据检查通过，')
    if accepted(root):
        page=page.replace('Clear V7 候选','Clear V7')
        page=page.replace('本轮候选尚待完整验收，默认仍为 V6。', '本轮已完成验收，Clear 默认已更新为 V7。')
        page=page.replace('自动通过只表示文件与数据检查通过，仍需查看人物过渡、材质和实际 HDR 光感。',
            '成品数据与适屏、原尺寸局部复核已完成；可逐张查看结果与复核说明。')
    page=page.replace('<p id="status"></p>', '<p id="status"></p><p id="finding"></p><nav id="extras"></nav>')
    page=page.replace("document.querySelector('#status').textContent=",
        "document.querySelector('#finding').textContent=r.finding;const extra=document.querySelector('#extras');extra.replaceChildren();for(const item of r.extras){const a=document.createElement('a');a.href=item.href;a.textContent='补充原尺寸：'+item.label;extra.append(a)}document.querySelector('#status').textContent=")
    (output/'cameras.html').write_text(page.replace('STYLE',STYLE).replace('COUNT',str(len(rows))).replace('OPTIONS',opts).replace('ROWS',json.dumps(rows)).replace('OVERVIEWS',' · '.join(overview)))
    return rows


def camera_native(root,output):
    """Native 512-pixel diagnostics; inferred ROI locations never drive edits."""
    directory=output/'camera-native';directory.mkdir(exist_ok=True)
    tools=resolve_tools(require_raw=False,require_exif=False)
    for record_path in sorted((root/'cameras').glob('*/audit.json')):
        stem=record_path.parent.name
        if (directory/f'{stem}.png').exists():continue
        record=json.loads(record_path.read_text());original=Path(record['ultrahdr'])
        local=record['render']['tone_mapping'].get('phone_clear_local',{})
        faces=local.get('detection',{}).get('faces',[])
        face=max(faces,key=lambda x:x.get('confidence',0),default=None)
        centers=[(.5,.4),(.5,.75)]
        if face:
            x,y,bw,bh=[face[k] for k in ['x','y','width','height']]
            centers=[(x+bw/2,y+bh/2),(x+bw/2,min(.92,y+7*bh))]
        sheet=Image.new('RGB',(1024,1092),'#eeeeee');draw=ImageDraw.Draw(sheet)
        boxes=[]
        with Image.open(original) as image:
            w,h=image.size
            for row,(x,y) in enumerate(centers):
                x0=max(0,min(w-512,round(x*w)-256));y0=max(0,min(h-512,round(y*h)-256))
                box=(x0,y0,min(w,x0+512),min(h,y0+512));boxes.append(box)
                rgb=_decode_transfer(np.asarray(image.crop(box),np.float32)/255)
                panel=_panel(rgb,hdr=False,width=522,height=546)
                sheet.paste(panel,(0,row*546));draw.text((8,row*546+516),f'{stem} SDR / crop {row+1}, 100%',fill='#111')
        with tempfile.TemporaryDirectory(prefix='hdrimg-native-camera-') as temp:
            decoded=Path(temp)/'hdr.rgba16f'
            run_checked([tools.ultrahdr,'-m','1','-j',original,'-o','0','-O','4','-z',decoded],
                        label='decode native camera crops',timeout=600)
            raw=np.memmap(decoded,dtype='<f2',mode='r',shape=(h,w,4))
            for row,(x0,y0,x1,y1) in enumerate(boxes):
                rgb=np.asarray(raw[y0:y1,x0:x1,:3],np.float32) @ REC2020_TO_DISPLAY_P3.T
                panel=_panel(rgb,hdr=True,width=522,height=546)
                sheet.paste(panel,(512,row*546));draw.text((520,row*546+516),f'{stem} HDR mapped / crop {row+1}, 100%',fill='#111')
            del raw
        sheet.save(directory/f'{stem}.png')
        (directory/f'{stem}.json').write_text(json.dumps({'boxes_original_pixels':boxes,
            'location_note':'Face and estimated lower subject if detected; otherwise central diagnostics. These are not semantic truth or production masks.',
            'source':str(original),'resampling':False,'hdr_mapping':'Y/(1+Y)'},indent=2)+'\n')
        print('Native camera',stem,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--cameras-only',action='store_true')
    parser.add_argument('--native-cameras',action='store_true')
    args=parser.parse_args(); out=args.root/'review'; out.mkdir(parents=True,exist_ok=True)
    if not args.cameras_only: edges(args.root,out)
    if args.native_cameras: camera_native(args.root,out)
    cameras(args.root,out)
