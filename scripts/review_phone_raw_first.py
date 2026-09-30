"""Build side-by-side evidence for full RAW-pipeline audits."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from audit_phone_clear import _panel, compare_audits
from review_phone_native import native_review
from hdrimg.tools import resolve_tools, run_checked


def review(baseline: Path, candidate: Path, output: Path, samples: Path, native: bool,
           release_decoded: bool = False):
    output.mkdir(parents=True, exist_ok=True)
    before = json.loads((baseline / 'audit.json').read_text())
    after = json.loads((candidate / 'audit.json').read_text())
    promotion_path = candidate.parent/'promotion.json'
    accepted = promotion_path.exists() and json.loads(promotion_path.read_text()).get('accepted', False)
    comparison = compare_audits(after, before)
    (output / 'metrics.json').write_text(json.dumps(comparison, indent=2) + '\n')
    cards = []
    scene_names = ('峡谷缝隙与亮天', '黄昏砂岩人像', '入口标牌人像', '砂岩墙人像',
                   '洞口逆光剪影', '夜间加油站', '室内混合光人像', '荒漠标牌',
                   '峡谷逆光白裙', '蓝天山谷树林', '黄昏白外套', '雪人与秋叶')
    for row in after['pairs']:
        folder = f'{row["id"]:02}_{row["stem"]}'
        with np.load(baseline / folder / 'previews.npz') as b, \
             np.load(candidate / folder / 'previews.npz') as c:
            images = [b['phone_sdr'], b['repo_sdr'], c['repo_sdr'],
                      b['phone_hdr'], b['repo_hdr'], c['repo_hdr']]
        # Two rows make the SDR and HDR comparisons separately inspectable.
        sheet = Image.new('RGB', (1560, 1440), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for i, rgb in enumerate(images):
            panel = _panel(rgb, hdr=i >= 3, width=520, height=720)
            x, y = (i % 3) * 520, (i // 3) * 720
            sheet.paste(panel, (x + (520-panel.width)//2, y + 4))
            draw.text((x+10, y+697), f'{row["id"]:02} ' +
                      ('Phone', 'Clear V6', 'Clear V7' if accepted else 'Clear V7 candidate')[i % 3] +
                      (' HDR preview' if i >= 3 else ' SDR'), fill='#111111')
        sheet.save(output / f'{row["id"]:02}_comparison.jpg', quality=94)
        for kind, image_rows in [('sdr', images[:3]), ('hdr', images[3:])]:
            for label, rgb in zip(('phone', 'baseline', 'candidate'), image_rows):
                _panel(rgb, hdr=kind == 'hdr', width=1024, height=1400).save(
                    output / f'{row["id"]:02}_{kind}_{label}.jpg', quality=95)
        for folder_root in (baseline, candidate):
            target = folder_root / folder
            manifest_path = Path(json.loads((target / 'record.json').read_text())['manifest'])
            manifest = json.loads(manifest_path.read_text())
            original = next(target.glob('*_ultrahdr.jpg'))
            alias = target / 'sdr.jpg'
            if not alias.exists():
                if alias.is_symlink(): alias.unlink()
                alias.symlink_to(original.name)
            alias = target / 'hdr.rgba16f'
            if native and not alias.exists():
                if alias.is_symlink(): alias.unlink()
                decoded = target / 'decoded_hdr.rgba16f'
                tools = resolve_tools(require_raw=False, require_exif=False)
                run_checked([tools.ultrahdr,'-m','1','-j',original.resolve(),'-o','0','-O','4','-z',decoded],
                            label='restore decoded review cache',timeout=600)
                alias.symlink_to(decoded.name)
        raw_link = next((candidate / folder).glob('*_ultrahdr.jpg')).resolve().as_uri()
        base_link = next((baseline / folder).glob('*_ultrahdr.jpg')).resolve().as_uri()
        scene_name = scene_names[row['id']-1] if 1 <= row['id'] <= 12 else row['scene']
        cards.append(f'<option value="{row["id"]:02}">{row["id"]:02} {html.escape(scene_name)}</option>')
        row['review_links'] = {'candidate': raw_link, 'baseline': base_link,
                               'phone': next(samples.glob(row['stem']+'*.jpg')).resolve().as_uri()}
    if native:
        native_review(samples, baseline, candidate, output / 'native')
    if release_decoded:
        if not all((output/'native'/f'{r["id"]:02}_native_regions.png').is_file() for r in after['pairs']):
            raise ValueError('Export native review crops before releasing decoded caches')
        # Only derived decode caches made by these audits are released. Final
        # JPEGs, source RAWs, native crops and all metrics remain available.
        for folder_root in (baseline, candidate):
            audit = json.loads((folder_root/'audit.json').read_text())
            for row in audit['pairs']:
                target = folder_root/f'{row["id"]:02}_{row["stem"]}'
                decoded, alias = target/'decoded_hdr.rgba16f', target/'hdr.rgba16f'
                if alias.is_symlink() and alias.resolve() == decoded.resolve(): alias.unlink()
                if decoded.is_file(): decoded.unlink()
                row['retention'] = 'Decoded HDR cache released after native crop export; regenerate from retained final Ultra HDR JPEG. Float scene preview, metrics and native crops retained.'
                (target/'record.json').write_text(json.dumps(row,indent=2)+'\n')
            (folder_root/'audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    page = '''<!doctype html><html lang="zh"><meta charset="utf-8">
<title>Phone Clear · HDR 与自然过渡</title>
<style>body{margin:0;background:#11151b;color:#eef1f5;font:16px system-ui}header{padding:24px 32px;border-bottom:1px solid #303641}h1{font-size:25px;margin:0 0 12px}p{color:#b4bdca;line-height:1.6}select,button,a{font:inherit}select,button{background:#242c38;color:white;border:1px solid #536174;padding:8px 12px;border-radius:6px}button{cursor:pointer}.controls{display:flex;gap:12px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;padding:20px}figure{margin:0}figcaption{padding:8px;text-align:center}img{width:100%;height:70vh;object-fit:contain;background:#090c10}a{color:#99c5ff}footer{padding:0 32px 30px} .note{max-width:980px}</style>
<header><h1>Phone Clear · HDR 与自然过渡</h1><p class="note">左：手机参考；中：Clear V6；右：Clear V7 候选。候选尚未替换默认版本，Phone Natural 保持原样。请先看整体明暗，再检查皮肤、衣物和轮廓过渡。HDR 原图模式直接加载最终成品，统一映射预览只用于比较分布。</p><div class="controls"><button id="prev">上一张</button><select id="scene">OPTIONS</select><button id="next">下一张</button><select id="mode"><option value="original">HDR 原图</option><option value="sdr">SDR 预览</option><option value="hdr">HDR 统一映射预览</option></select></div></header>
<div class="grid"><figure><figcaption>手机原片</figcaption><img id="phone"></figure><figure><figcaption>Clear V6</figcaption><img id="baseline"></figure><figure><figcaption>Clear V7 候选</figcaption><img id="candidate"></figure></div>
<footer><p id="hdr-support"></p><p><a href="edges.html">历史腿部边缘对照</a> · <a href="cameras.html">37 张相机照片</a></p><p><a id="base-original">打开当前 Ultra HDR</a> · <a id="new-original">打开候选 Ultra HDR</a> · <a id="compare">整张对照</a> · <a id="native">原尺寸局部对照</a></p><p class="note">默认版本仍为 V6；候选需要通过真实 HDR 屏幕验收。边缘发灰发白、黑边和光晕均不能通过。DNG 与手机 JPEG 的构图和合成处理略有差异，区域指标不能视作逐像素真值。</p></footer>
<script>const rows=ROWS;const scene=document.querySelector('#scene'),mode=document.querySelector('#mode');function update(){let id=scene.value,m=mode.value,r=rows.find(x=>String(x.id).padStart(2,'0')===id);for(const key of ['phone','baseline','candidate'])document.querySelector('#'+key).src=m==='original'?r.review_links[key]:`${id}_${m}_${key}.jpg`;document.querySelector('#base-original').href=r.review_links.baseline;document.querySelector('#new-original').href=r.review_links.candidate;document.querySelector('#compare').href=`${id}_comparison.jpg`;document.querySelector('#native').href=`native/${id}_native_regions.png`;}scene.onchange=mode.onchange=update;document.querySelector('#prev').onclick=()=>{scene.selectedIndex=(scene.selectedIndex+rows.length-1)%rows.length;update()};document.querySelector('#next').onclick=()=>{scene.selectedIndex=(scene.selectedIndex+1)%rows.length;update()};update();document.querySelector('#hdr-support').textContent=matchMedia('(dynamic-range: high)').matches?'浏览器报告支持 HDR；请同时确认屏幕设置与实际亮感。':'浏览器未报告 HDR 支持；此环境不能完成真实 HDR 验收。';</script></html>'''
    transition_path = candidate.parent/'transition-probe/audit.json'
    transition = json.loads(transition_path.read_text()) if transition_path.exists() else None
    codec_path = candidate.parent/'pipeline-edges/audit.json'
    codec = json.loads(codec_path.read_text()) if codec_path.exists() else None
    if transition is None:
        status = '验收进行中，尚无本轮空间边界检查记录；默认保留 V6。'
    elif transition['passed']:
        status = '本轮自动空间边界检查通过；真实照片、材质与 Mac HDR 实屏仍需共同验收。默认保留 V6。'
        if codec:
            status = (f'本轮空间过渡及 {codec["count"]} 项最终编解码边界检查通过。' if codec['passed'] else
                      '本轮最终编解码边界检查未全部通过。') + ' 真实照片、材质与 Mac HDR 实屏仍需共同验收；默认保留 V6。'
    else:
        status = '验收状态：未通过。空间曝光场的严格单调边界测试仍发现亮度起伏，不能由其他观感改善抵消；默认保留 V6。'
    display_path = candidate.parent/'display-review.json'
    display = json.loads(display_path.read_text()) if display_path.exists() else {}
    if display.get('status') == 'user_confirmed_hdr_direction_and_natural_transitions':
        count = len(list((candidate.parent/'cameras').glob('*/audit.json')))
        status = f'用户已在 Mac HDR 实屏确认：方向符合预期，过渡自然。相机回归 {count}/37 张；最终验收记录持续更新。'
        page = page.replace('候选需要通过真实 HDR 屏幕验收。', '实屏方向与过渡已获用户确认，其余回归完成后更新默认。')
    if accepted:
        status = '已完成验收并设为 Phone Clear 默认：12 张同拍样片、37 张相机 RAW、边缘与编解码检查通过；Mac HDR 方向与过渡已获用户确认。'
        page = page.replace('Clear V7 候选', 'Clear V7')
        page = page.replace('候选尚未替换默认版本，Phone Natural 保持原样。', 'V7 已设为默认版本，Phone Natural V8 保持原样。')
        page = page.replace('默认版本仍为 V6；实屏方向与过渡已获用户确认，其余回归完成后更新默认。', '默认版本为 V7；V6 保留作对照。')
        page = page.replace('打开当前 Ultra HDR', '打开 V6 Ultra HDR').replace('打开候选 Ultra HDR', '打开 V7 Ultra HDR')
    page = page.replace('<div class="controls">', '<p class="note" style="color:#ffd399">'+status+'</p><div class="controls">')
    page = page.replace('<a href="edges.html">', '<a href="../transition-probe/audit.json">边缘压力测试记录</a> · <a href="edges.html">')
    if codec:
        page = page.replace('<a href="edges.html">', '<a href="../pipeline-edges/audit.json">最终编解码边界记录</a> · <a href="edges.html">')
    page = page.replace('OPTIONS', ''.join(cards)).replace('ROWS', json.dumps([
        {'id': r['id'], 'review_links': r['review_links']} for r in after['pairs']]))
    visual_path = candidate.parent/'visual-review.json'
    if visual_path.exists():
        visual = json.loads(visual_path.read_text())
        notes = {str(r['id']).zfill(2):r['finding'] for r in visual.get('rows',[])}
        page = page.replace('</header>', '<p id="review-note" class="note"></p></header>')
        page = page.replace('<script>const rows=', '<script>const reviewNotes='+json.dumps(notes,ensure_ascii=False)+';const rows=')
        page = page.replace("document.querySelector('#native').href=`native/${id}_native_regions.png`;",
            "document.querySelector('#native').href=`native/${id}_native_regions.png`;document.querySelector('#review-note').textContent=reviewNotes[id]||'';")
    probes = [(number, baseline.parent / f'{number:02}_raw_wb_probe.jpg')
              for number in (2, 7, 9, 10)]
    links = ' · '.join(f'<a href="{path.resolve().as_uri()}">{number:02} {scene_names[number-1]}</a>'
                       for number, path in probes if path.exists())
    if links:
        page = page.replace('<footer>', '<footer><p>RAW 白平衡专项对照：' + links + '</p>')
    (output / 'index.html').write_text(page)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--samples', type=Path, default=Path('jpg_hdr_sample_effect'))
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--release-decoded', action='store_true', help='Release derived HDR caches after native crop export')
    args = parser.parse_args()
    review(args.baseline, args.candidate, args.output, args.samples, args.native, args.release_decoded)
