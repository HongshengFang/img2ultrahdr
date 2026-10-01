"""SDR JPEG -> AI luminance-only gain -> guided upsample -> Ultra HDR JPEG."""
import hashlib, io, json, time
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageCms, ImageDraw
import torch
from .ai import predict_hdr, sam_protect
from .codec import Codec
from .runtime import runtime_dir

def srgb_linear(rgb):
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4).astype(np.float32)

def luminance(rgb):
    return rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)

def fast_guided(guide_low, gain_low, guide_high, radius=8, eps=0.002):
    """Fast guided filter: low-resolution coefficients, full-resolution guide.

    Linear gain EV is filtered in log-luminance guidance. Unlike a plain resize,
    the final field reacts to edges in the ORIGINAL image. CPU memory only.
    """
    box = lambda a: cv2.boxFilter(a, -1, (2 * radius + 1,) * 2, borderType=cv2.BORDER_REFLECT)
    mean_i, mean_p = box(guide_low), box(gain_low)
    covariance = box(guide_low * gain_low) - mean_i * mean_p
    variance = np.maximum(box(guide_low * guide_low) - mean_i * mean_i, 0)
    a = covariance / (variance + eps)
    b = mean_p - a * mean_i
    shape = (guide_high.shape[1], guide_high.shape[0])
    return cv2.resize(box(a), shape, interpolation=cv2.INTER_LINEAR) * guide_high + cv2.resize(box(b), shape, interpolation=cv2.INTER_LINEAR)

def run(args):
    started = time.perf_counter()
    source, out = Path(args.input).resolve(), Path(args.output).resolve()
    if source == out:
        raise ValueError('Output must differ from input')
    if out.suffix.lower() not in ('.jpg', '.jpeg'):
        raise ValueError('Output filename must end with .jpg or .jpeg')
    if out.exists() and not args.overwrite:
        raise FileExistsError('Output exists; use --overwrite explicitly')
    if not 0 < args.max_ev <= 5 or not 384 <= args.ai_size <= 1536:
        raise ValueError('max-ev must be (0,5]; ai-size must be [384,1536]')
    if not np.isfinite(args.strength) or not 0 <= args.strength <= 3:
        raise ValueError('strength must be finite and within [0,3]')
    base_bytes = source.read_bytes()
    codec = Codec()
    buffer, descriptor = codec.compressed(base_bytes)
    if codec.lib.is_uhdr_image(descriptor.data, descriptor.size):
        raise ValueError('Input is already Ultra HDR; use the original SDR JPEG')
    image = Image.open(io.BytesIO(base_bytes))
    if image.format != 'JPEG' or image.mode != 'RGB':
        raise ValueError('V1 accepts RGB JPEG only (no CMYK/grayscale)')
    # Work in stored pixel coordinates to keep compressed JPEG and EXIF intact.
    w, h = image.size
    if max(w, h) > 8192:
        raise ValueError('libultrahdr build supports dimensions up to 8192')
    icc = image.info.get('icc_profile')
    if icc:
        name = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
        if 'srgb' not in name.lower():
            raise ValueError('V1 requires sRGB ICC. Convert to sRGB before using this prototype.')
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if device == 'cuda':
        torch.cuda.reset_peak_memory_stats()
    rgb8 = np.asarray(image)
    ratio = min(1, args.ai_size / max(w, h))
    low_w, low_h = [max(32, round(n * ratio / 32) * 32) for n in (w, h)]
    low8 = cv2.resize(rgb8, (low_w, low_h), interpolation=cv2.INTER_AREA)
    low_lin = srgb_linear(low8.astype(np.float32) / 255)
    print(f'IntrinsicHDR: {low_w}x{low_h}, {device}, FP16={not args.fp32}', flush=True)
    ai_start = time.perf_counter()
    hdr = predict_hdr(low_lin, device=device, fp16=not args.fp32)
    ai_seconds = time.perf_counter() - ai_start
    y_sdr, y_hdr = luminance(low_lin), luminance(hdr)
    raw_ev = np.log2(np.maximum(y_hdr, 1e-6) / np.maximum(y_sdr, 1e-6))
    # Resolve the scene/model exposure ambiguity on ordinary midtones.
    mids = (y_sdr > 0.03) & (y_sdr < 0.5)
    exposure_ev = float(np.median(raw_ev[mids])) if np.count_nonzero(mids) >= 100 else float(np.median(raw_ev))
    gain = np.clip((raw_ev - exposure_ev) * args.strength, 0, args.max_ev)
    # Protect shadows from noisy/hallucinated expansion; smooth highlight gate.
    gate = np.clip((y_sdr - 0.05) / 0.65, 0, 1)
    gate = gate * gate * (3 - 2 * gate)
    gain *= gate
    cap = np.full((low_h, low_w), args.max_ev, np.float32)
    if args.protect:
        spec = json.loads(Path(args.protect).read_text(encoding='utf-8'))
        # Prompt coordinates are ORIGINAL stored pixels, never AI-image pixels.
        prompts = []
        for p in spec['regions']:
            p = dict(p)
            if not 0 <= float(p.get('max_ev', 0.35)) <= args.max_ev:
                raise ValueError('Protection max_ev must be within [0, max-ev]')
            if 'points' in p:
                p['points'] = [[x * low_w / w, y * low_h / h] for x, y in p['points']]
            if 'box' in p:
                p['box'] = [n * (low_w / w if i % 2 == 0 else low_h / h) for i, n in enumerate(p['box'])]
            prompts.append(p)
        print(f'SAM2 tiny: {len(prompts)} protection region(s)', flush=True)
        cap = np.minimum(cap, sam_protect(low8, prompts, device, not args.fp32))
        gain = np.minimum(gain, cap)
    else:
        print('Protection: luminance shadow/highlight rules; SAM2 regions not supplied', flush=True)
    low_guide = np.log1p(y_sdr * 32) / np.log(33)
    linear = srgb_linear(rgb8.astype(np.float32) / 255)
    high_y = luminance(linear)
    high_guide = np.log1p(high_y * 32) / np.log(33)
    full_gain = fast_guided(low_guide.astype(np.float32), gain.astype(np.float32), high_guide.astype(np.float32))
    # Reapply the cap after filtering, so boundary filtering cannot undo guards.
    full_cap = cv2.resize(cap, (w, h), interpolation=cv2.INTER_NEAREST)
    full_gain = np.minimum(np.clip(full_gain, 0, args.max_ev), full_cap)
    full_gain[high_y < 0.005] = 0
    if not np.isfinite(full_gain).all():
        raise ValueError('Nonfinite gain field')
    out.parent.mkdir(parents=True, exist_ok=True)
    gain_bytes = io.BytesIO()
    Image.fromarray(np.rint(full_gain / args.max_ev * 255).astype(np.uint8)).save(gain_bytes, format='JPEG', quality=98)
    encoded = codec.encode(base_bytes, gain_bytes.getvalue(), args.max_ev)
    verification = codec.verify(encoded, base_bytes, args.max_ev)
    out.write_bytes(encoded)
    diagnostics = out.parent / (out.stem + '_diagnostics')
    diagnostics.mkdir(exist_ok=True)
    (diagnostics / 'gainmap.jpg').write_bytes(gain_bytes.getvalue())
    np.save(diagnostics / 'gain_ev_low.npy', gain.astype(np.float32))
    np.save(diagnostics / 'gain_ev_full.npy', full_gain.astype(np.float32))
    Image.fromarray(np.rint(full_gain / args.max_ev * 255).astype(np.uint8)).save(diagnostics / 'gainmap.png')
    Image.fromarray(np.rint(full_cap / args.max_ev * 255).astype(np.uint8)).save(diagnostics / 'protection_cap.png')
    # Preview is a false-color map beside the SDR. It cannot preview actual HDR.
    thumb = image.copy()
    thumb.thumbnail((960, 640))
    g = cv2.resize(full_gain, thumb.size)
    heat = cv2.cvtColor(cv2.applyColorMap(np.rint(g / args.max_ev * 255).astype(np.uint8), cv2.COLORMAP_TURBO), cv2.COLOR_BGR2RGB)
    preview = Image.new('RGB', (thumb.width * 2, thumb.height + 36), '#111111')
    preview.paste(thumb, (0, 36)); preview.paste(Image.fromarray(heat), (thumb.width, 36))
    draw = ImageDraw.Draw(preview)
    draw.text((10, 10), 'Original SDR base (unchanged)', fill='white')
    draw.text((thumb.width + 10, 10), f'Gain EV: blue=0, red={args.max_ev:g} (diagnostic, not HDR preview)', fill='white')
    preview.save(diagnostics / 'preview.jpg', quality=92)
    report = dict(pipeline='jpeg-hdr-research-v1', input=str(source), output=str(out),
                  runtime=str(runtime_dir()), input_sha256=hashlib.sha256(base_bytes).hexdigest(),
                  gpu=torch.cuda.get_device_name() if device == 'cuda' else None,
                  ai_size=[low_w, low_h], precision='FP32' if args.fp32 else 'FP16 autocast / FP32 math',
                  ai_seconds=ai_seconds, elapsed_seconds=time.perf_counter()-started,
                  peak_torch_allocated_mib=torch.cuda.max_memory_allocated()/2**20 if device=='cuda' else None,
                  peak_torch_reserved_mib=torch.cuda.max_memory_reserved()/2**20 if device=='cuda' else None,
                  exposure_anchor_ev=exposure_ev, gain_ev_percentiles=np.percentile(full_gain,[0,50,90,99,100]).tolist(),
                  max_ev=args.max_ev, strength=args.strength, sam2_protection=bool(args.protect),
                  protection_regions=spec['regions'] if args.protect else [],
                  input_linearization='standard inverse sRGB; no SingleHDR TensorFlow camera-response estimation',
                  verification=verification)
    (diagnostics / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2), flush=True)
