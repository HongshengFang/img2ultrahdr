"""Re-evaluate an immutable RAW audit from its delivered JPEGs."""
import argparse
import json
from pathlib import Path

import numpy as np

from audit_phone_clear import _read_sdr, _read_hdr, _metrics, _spatial_metrics, _regions
from audit_phone_raw_first import reduced
from hdrimg.color import REC2020_TO_DISPLAY_P3
from hdrimg.tools import resolve_tools, run_checked


def run(source: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    report = json.loads((source/'audit.json').read_text())
    specs = json.loads(Path('docs/phone-clear-regions.json').read_text())
    tools = resolve_tools(require_raw=False, require_exif=False)
    for row in report['pairs']:
        folder = f'{row["id"]:02}_{row["stem"]}'
        dest = output/folder; dest.mkdir(exist_ok=True)
        original = next((source/folder).glob('*_ultrahdr.jpg')).resolve()
        decoded = dest/'decoded_hdr.rgba16f'
        run_checked([tools.ultrahdr,'-m','1','-j',original,'-o','0','-O','4','-z',decoded],
                    label='decode frozen baseline',timeout=600)
        sdr, size = _read_sdr(original,step=1)
        sdr, hdr = reduced(sdr), reduced(_read_hdr(decoded,size,step=1))
        with np.load(source/folder/'previews.npz') as before:
            ps, ph = before['phone_sdr'], before['phone_hdr']
        converted = hdr @ REC2020_TO_DISPLAY_P3.T
        row['repo'] = _metrics(sdr,hdr,hdr_gamut='rec2020')
        row['spatial'] = _spatial_metrics(ps,sdr,ph,converted)
        row['regions'] = _regions(ps,sdr,ph,converted,specs[str(row['id'])])
        row['metric_source'] = 'final Ultra HDR decoded SDR/HDR'
        np.savez_compressed(dest/'previews.npz',phone_sdr=ps,phone_hdr=ph,repo_sdr=sdr,repo_hdr=converted)
        (dest/'record.json').write_text(json.dumps(row,indent=2)+'\n')
        for name, target in [('sdr.jpg',original),('hdr.rgba16f',decoded.name),(original.name,original)]:
            link=dest/name
            if not link.exists():link.symlink_to(target)
        print('DECODED',row['id'],flush=True)
    report['scope'] = 'Frozen Clear V6; metrics recomputed from final decoded deliverables'
    (output/'audit.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();run(a.source,a.output)
