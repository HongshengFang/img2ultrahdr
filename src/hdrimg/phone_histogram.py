"""Bounded local histogram contrast with head and illumination protection.

Cues must come from an independent SDR reference so an SDR-only exposure
change cannot alter the HDR rendition. Smooth tiles and deep shadows are
protected; local mean correction limits unwanted changes in illumination.
"""
import numpy as np
from PIL import Image
from .phone_tone import _box_mean, _smoothstep


def local_histogram_fields(y: np.ndarray, strength: float = .65, faces: list[dict] | None = None):
    y = np.clip(np.nan_to_num(y), 0, 1).astype(np.float32)
    if min(y.shape) < 8:
        one = Image.fromarray(np.ones_like(y), mode="F")
        return one, one.copy(), {"reason":"too_small"}
    light = np.cbrt(y)
    h, w = y.shape
    ny, nx, bins = 8, 8, 256
    edges_y = np.linspace(0, h, ny+1).astype(int)
    edges_x = np.linspace(0, w, nx+1).astype(int)
    centers = (np.arange(bins)+.5)/bins
    luts = np.empty((ny, nx, bins), np.float32)
    detail = light - _box_mean(light, 2)
    weights = np.zeros((ny, nx), np.float32)
    for i in range(ny):
        for j in range(nx):
            sl = np.s_[edges_y[i]:edges_y[i+1], edges_x[j]:edges_x[j+1]]
            tile = light[sl]
            hist = np.histogram(tile, bins=bins, range=(0, 1))[0].astype(float)
            limit = max(1, 2*tile.size/bins)
            clipped = np.minimum(hist, limit)
            clipped += (hist.sum()-clipped.sum())/bins
            cdf = (np.cumsum(clipped)-.5*clipped)/max(clipped.sum(), 1)
            median = float(np.median(tile))
            cdf += median - np.interp(median, centers, cdf)
            luts[i,j] = np.clip(cdf, 0, 1)
            # Reject nearly smooth sky/flat noise after area reduction.
            texture = float(np.percentile(np.abs(detail[sl]), 80))
            weights[i,j] = _smoothstep((texture-.004)/.018)
    yy = (np.arange(h)+.5)*ny/h-.5
    xx = (np.arange(w)+.5)*nx/w-.5
    y0 = np.floor(yy).astype(int); x0 = np.floor(xx).astype(int)
    fy = (yy-y0)[:,None]; fx = (xx-x0)[None,:]
    y1 = np.clip(y0+1,0,ny-1);x1=np.clip(x0+1,0,nx-1)
    y0 = np.clip(y0,0,ny-1);x0=np.clip(x0,0,nx-1)
    b = np.clip(light*bins-.5,0,bins-1)
    b0 = np.floor(b).astype(int);b1=np.minimum(b0+1,bins-1);fb=b-b0
    mapped = np.zeros_like(y); support = np.zeros_like(y)
    for iy, wy in ((y0,1-fy),(y1,fy)):
        for ix, wx in ((x0,1-fx),(x1,fx)):
            weight=wy*wx
            mapped += weight*((1-fb)*luts[iy[:,None],ix[None,:],b0]+fb*luts[iy[:,None],ix[None,:],b1])
            support += weight*weights[iy[:,None],ix[None,:]]
    target = np.clip(mapped**3,1e-6,1-1e-6)
    safe = np.clip(y,1e-6,1-1e-6)
    delta = np.log2(target/(1-target))-np.log2(safe/(1-safe))
    delta = np.clip(delta,-.65,.65)*strength*support
    preliminary = y*np.exp2(delta)/(1-y+y*np.exp2(delta))
    radius = max(2, round(min(h,w)*.045))
    illumination_shift = np.log2((_box_mean(preliminary,radius)+.01)/(_box_mean(y,radius)+.01))
    illumination_shift = _box_mean(illumination_shift,radius)
    corrected = np.clip(preliminary*np.exp2(-illumination_shift),1e-6,1-1e-6)
    delta = np.log2(corrected/(1-corrected))-np.log2(safe/(1-safe))
    guard = _smoothstep((y-.035)/.13)
    protected = np.zeros_like(y)
    yy,xx = np.mgrid[:h,:w].astype(np.float32)
    for face in faces or []:
        x0,y0,bw,bh = [face[k] for k in ('x','y','width','height')]
        dx = (xx/w-(x0+bw/2))/max(bw,.001)
        dy = (yy/h-(y0+.35*bh))/max(bh,.001)
        protected = np.maximum(protected,np.clip(3*np.exp(-1.6*(dx*dx+(dy/1.6)**2)),0,1))
    delta = np.clip(delta,-.65*strength,.65*strength)*guard*(1-protected)
    delta = _box_mean(delta,1)
    odds = np.exp2(delta)
    after = y*odds/(1-y+y*odds)
    ratio = np.divide(after,y,out=np.ones_like(y),where=y>1e-6)
    return Image.fromarray(ratio,mode='F'), Image.fromarray(odds,mode='F'), {
        'method':'illumination-preserving head-protected local histogram', 'strength':strength,
        'max_abs_odds_ev':float(np.max(np.abs(delta))), 'mean_support':float(np.mean(support))}
