"""Propagate protection limits smoothly over the original image's pixels."""
import cv2
import numpy as np


def protection_envelope(cap_low, luminance_high, *, spatial_ev=.025, edge_slope=.25):
    """Lower caps outside protected objects without blurring the SDR image.

    A nearest-neighbor semantic mask can put a several-stop jump on a flat
    surface. A one-sided geodesic envelope limits each cap step to a small
    spatial allowance plus the actual source luminance change. Strong source
    edges allow faster transitions; similar neighboring pixels get similar
    limits. Protected interiors remain at or below the requested cap.
    """
    shape = (luminance_high.shape[1], luminance_high.shape[0])
    initial = cv2.resize(np.asarray(cap_low, np.float32), shape,
                         interpolation=cv2.INTER_LINEAR)
    if not np.isfinite(initial).all() or np.any(initial < 0):
        raise ValueError('Protection caps must be finite and nonnegative')
    if spatial_ev <= 0 or edge_slope < 0:
        raise ValueError('Invalid protection transition allowances')
    guide = np.log2(np.maximum(np.asarray(luminance_high, np.float32), .01))
    # The source does not change between envelope iterations. Computing its
    # cumulative graph distances once avoids rescanning gradients every time.
    distances_by_axis = []
    for axis in (1, 0):
        g = guide if axis == 1 else guide.T
        distances = np.empty(g.shape, np.float64)
        distances[:, 0] = 0
        for start in range(0, len(g), 96):
            stop = start + 96
            cost = spatial_ev + edge_slope * np.abs(np.diff(g[start:stop].astype(np.float64), axis=1))
            np.cumsum(cost, axis=1, out=distances[start:stop, 1:])
        distances_by_axis.append(distances)
    result = initial.copy()
    change = 0.
    for iteration in range(64):
        change = 0.
        for axis, distances in zip((1, 0), distances_by_axis):
            values = result if axis == 1 else result.T
            # Full-resolution CPU processing, with bounded temporary memory.
            for start in range(0, len(values), 96):
                stop = start + 96
                before = values[start:stop].astype(np.float64)
                d = distances[start:stop]
                after = np.minimum(
                    d + np.minimum.accumulate(before - d, axis=1),
                    -d + np.minimum.accumulate((before + d)[:, ::-1], axis=1)[:, ::-1])
                change = max(change, float(np.max(before - after)))
                values[start:stop] = after
        if change < 2e-7:
            break
    violation = 0.
    for axis in (0, 1):
        allowance = spatial_ev + edge_slope * np.abs(np.diff(guide, axis=axis))
        violation = max(violation, float(np.max(np.abs(np.diff(result, axis=axis)) - allowance, initial=0)))
    if violation > 1e-5:
        raise RuntimeError('Protection transition envelope did not converge')
    result = np.maximum(np.minimum(result, initial), 0)
    return result, dict(method='native-luminance one-sided geodesic protection cap',
                        spatial_ev_per_pixel=spatial_ev, source_edge_slope=edge_slope,
                        iterations=iteration + 1, last_change_ev=change,
                        gradient_allowance_violation_ev=violation,
                        changed_pixel_fraction=float(np.mean(result < initial - 1e-5)))
