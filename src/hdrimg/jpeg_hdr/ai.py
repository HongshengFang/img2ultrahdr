# Portions adapted from IntrinsicHDR (Dille, Careaga, Aksoy, ECCV 2024).
# This adapter is academic-use-only; see docs/licenses/IntrinsicHDR.txt.
# The repository's MIT license does not replace these terms for this file.
"""Official IntrinsicHDR architectures/weights; inference-only sequential adapter.

Avoid Lightning, legacy TensorFlow and unused training dependencies. Backbone
initialization uses the same architectures without downloading unrelated weights.
The caller derives scalar luminance ratios and discards reconstructed RGB.
"""
from pathlib import Path
import contextlib, gc, sys
import cv2
import numpy as np
import torch
import torch.nn.functional as F
from .runtime import runtime_dir

def clear_gpu():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

def configure_backbones():
    import geffnet
    from torchvision.models import resnext101_32x8d
    import src.midas.blocks as small
    import intrinsic_decomposition.networks.altered_midas.blocks as decomp
    def efficient(module, use_pretrained, exportable=False, in_chan=3):
        net = geffnet.tf_efficientnet_lite3(pretrained=False, exportable=exportable)
        if in_chan != 3:
            net.conv_stem = module.Conv2dSame(in_chan, 32, (3, 3), (2, 2), bias=False)
        return module._make_efficientnet_backbone(net)
    small._make_pretrained_efficientnet_lite3 = lambda *a, **kw: efficient(small, *a, **kw)
    decomp._make_pretrained_efficientnet_lite3 = lambda *a, **kw: efficient(decomp, *a, **kw)
    def resnext(use_pretrained, in_chan=3):
        net = resnext101_32x8d(weights=None)
        if in_chan != 3:
            net.conv1 = torch.nn.Conv2d(in_chan, 64, 7, 2, 3, bias=False)
        return decomp._make_resnet_backbone(net)
    decomp._make_pretrained_resnext101_wsl = resnext

def load_state(model, name, prefix=None):
    # The released Lightning checkpoints include Namespace objects; official
    # research checkpoint sources only. All tensors are loaded on CPU first.
    checkpoint = torch.load(runtime_dir() / 'weights' / name, map_location='cpu', weights_only=False)
    state = checkpoint.get('state_dict', checkpoint)
    if prefix:
        state = {k[len(prefix):]: v for k, v in state.items() if k.startswith(prefix)}
    model.load_state_dict(state, strict=True)
    del checkpoint, state
    return model.eval()

def infer(model, x, device, fp16):
    model = model.to(device)
    ctx = torch.autocast('cuda', dtype=torch.float16) if fp16 and device == 'cuda' else contextlib.nullcontext()
    with torch.inference_mode(), ctx:
        # Return FP32 so reciprocal, fitting and quantiles remain stable.
        result = model(x.to(device)).float()
    return result

def predict_hdr(linear, device='cuda', fp16=True):
    source = runtime_dir() / 'IntrinsicHDR'
    if not (source / 'src' / 'midas' / 'midas_net.py').is_file():
        raise FileNotFoundError(f'IntrinsicHDR source missing at {source}; see docs/jpeg-hdr.md')
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    configure_backbones()
    from intrinsic_decomposition.networks.altered_midas.midas_net import MidasNet
    from intrinsic_decomposition.networks.altered_midas.midas_net_custom import MidasNet_small as MergeNet
    from src.midas.midas_net import MidasNet_small as RecNet
    torch.manual_seed(0)
    x = torch.from_numpy(linear.copy()).permute(2, 0, 1)[None].to(device)
    h, w = linear.shape[:2]
    model = load_state(MidasNet(), 'vivid_bird_318_300.pt')
    base_shape = [max(32, int(round(n * 384 / max(h, w) / 32) * 32)) for n in (h, w)]
    base = infer(model, F.interpolate(x, base_shape, mode='bilinear', align_corners=False, antialias=True), device, fp16)
    full = infer(model, x, device, fp16)
    del model
    clear_gpu()
    base = F.interpolate(base[:, None], (h, w), mode='bilinear', align_corners=False, antialias=True)
    full = full[:, None]
    # Official decomposition scale equalization, expressed as a stable scalar
    # least-squares fit over all pixels (rather than its random subset).
    lum = (x * x.new_tensor([0.3, 0.59, 0.11])[None, :, None, None]).sum(1, keepdim=True)
    full_alb = lum / (1 / full.clamp_min(1e-5) - 1).clamp_min(1e-5)
    base_alb = lum / (1 / base.clamp_min(1e-5) - 1).clamp_min(1e-5)
    fitted = (full_alb * base_alb).sum() / full_alb.square().sum().clamp_min(1e-8)
    new_alb = full_alb * fitted
    new_sh = (x / new_alb.clamp_min(1e-5) * x.new_tensor([0.3, 0.59, 0.11])[None, :, None, None]).sum(1, keepdim=True)
    full = 1 / (1 + new_sh)
    model = load_state(MergeNet(exportable=False, input_channels=5, output_channels=1), 'fluent_eon_138_200.pt')
    inv_sh = infer(model, torch.cat([x, base, full], 1), device, fp16)[:, None].clamp(1e-5, 1 - 1e-5)
    del model, base, full
    clear_gpu()
    alb = x / (1 / inv_sh - 1).clamp_min(1e-5)
    scale = 0.95 / torch.quantile(alb.float(), 0.95).clamp_min(1e-5)
    alb = alb * scale
    inv_sh = 1 / ((1 / inv_sh - 1) / scale + 1)
    mask = ((x - 0.8).clamp(0, 1) / 0.2).amax(1, keepdim=True)
    model = load_state(RecNet(activation='none', input_channels=7, output_channels=3), 'alb_weights.ckpt', 'network.')
    alb_hdr = torch.sigmoid(infer(model, torch.cat([x, alb, mask], 1), device, fp16))
    del model, alb
    clear_gpu()
    model = load_state(RecNet(activation='none', input_channels=4, output_channels=1), 'sh_weights.ckpt', 'network.')
    inv_hdr_sh = torch.sigmoid(infer(model, torch.cat([x, inv_sh], 1), device, fp16)).clamp(1e-5, 1 - 1e-5)
    del model, inv_sh
    clear_gpu()
    hdr = alb_hdr * (1 / inv_hdr_sh - 1)
    model = load_state(RecNet(activation='none', input_channels=10, output_channels=3), 'ref_weights.ckpt', 'refiner.')
    refined = torch.sigmoid(infer(model, torch.cat([x, 1 / (hdr + 1), alb_hdr, inv_hdr_sh], 1), device, fp16)).clamp(1e-5, 1 - 1e-5)
    hdr = (1 / refined - 1)[0].permute(1, 2, 0).cpu().numpy()
    del model, x, alb_hdr, inv_hdr_sh, refined, mask
    clear_gpu()
    if not np.isfinite(hdr).all():
        raise RuntimeError('Nonfinite IntrinsicHDR prediction')
    return hdr

def sam_protect(rgb, prompts, device='cuda', fp16=True, return_floor=False):
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    predictor = SAM2ImagePredictor(build_sam2('configs/sam2.1/sam2.1_hiera_t.yaml',
                                  str(runtime_dir() / 'weights' / 'sam2.1_hiera_tiny.pt'), device=device,
                                  apply_postprocessing=False))
    cap = np.full(rgb.shape[:2], np.inf, np.float32)
    floor = np.zeros(rgb.shape[:2], np.float32)
    ctx = torch.autocast('cuda', dtype=torch.float16) if fp16 and device == 'cuda' else contextlib.nullcontext()
    with torch.inference_mode(), ctx:
        predictor.set_image(rgb)
        for prompt in prompts:
            points = prompt.get('points')
            labels = prompt.get('labels', [1] * len(points)) if points else None
            masks, scores, _ = predictor.predict(
                point_coords=np.asarray(points, np.float32) if points else None,
                point_labels=np.asarray(labels, np.int32) if points else None,
                box=np.asarray(prompt['box'], np.float32) if 'box' in prompt else None,
                multimask_output=True)
            mask = masks[np.argmax(scores)]
            if float(prompt.get('min_ev', 0)) > 0:
                # Fill tiny gaps in lace/cloth and feather positive adaptation.
                positive = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE,
                                           np.ones((3, 3), np.uint8)).astype(np.float32)
                positive = cv2.GaussianBlur(positive, (0, 0), 1.25)
                floor = np.maximum(floor, positive * float(prompt['min_ev']))
            # Small dilation prevents edge leakage after upsampling.
            mask = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
            cap[mask] = np.minimum(cap[mask], float(prompt.get('max_ev', 0.35)))
    del predictor
    clear_gpu()
    return (cap, floor) if return_floor else cap
