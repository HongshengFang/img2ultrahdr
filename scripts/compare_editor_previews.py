"""Compare decoded Ultra HDR previews with downsized full-resolution exports."""
import argparse,json,subprocess,tempfile,time,os
from pathlib import Path
import numpy as np
from PIL import Image
from hdrimg.editor import atomic_json
from hdrimg.tools import resolve_tools

p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--follow-pid',type=int)
a=p.parse_args();root=a.root.resolve();tools=resolve_tools();rows=[];seen=set()
while True:
    suite=json.loads((root/'raw-suite.json').read_text()) if (root/'raw-suite.json').exists() else []
    for row in suite:
        if row['source'] in seen:continue
        manifests=[json.loads(p.read_text()) for p in (root/'cache/renders').glob('*/complete.json')]
        preview=next((m for m in manifests if not m['full'] and Path(m['source']['path']).name==row['source']),None)
        if not preview:continue
        with tempfile.TemporaryDirectory(prefix='editor-hdr-comparison-') as temp:
            work=Path(temp);arrays=[]
            for label,path in [('small',preview['ultrahdr']),('large',row['exported'])]:
                with Image.open(path) as jpg:w,h=jpg.size
                raw=work/f'{label}.raw'
                subprocess.run([str(tools.ultrahdr),'-m','1','-j',path,'-o','0','-O','4','-z',str(raw)],check=True,capture_output=True)
                rgb=np.memmap(raw,dtype='<f2',mode='r',shape=(h,w,4))
                y=np.empty((h,w),np.float32)
                for start in range(0,h,256):
                    y[start:start+256]=np.asarray(rgb[start:start+256,:,:3],np.float32)@np.array([.2627,.678,.0593],np.float32)
                del rgb
                y=np.asarray(Image.fromarray(y).resize((preview['width'],preview['height']),Image.Resampling.BOX))
                arrays.append(y)
            small,large=arrays;valid=(small>.02)&(large>.02)
            ev=np.abs(np.log2(np.maximum(small,1e-7)/np.maximum(large,1e-7)))
            diff=np.stack([np.clip(ev/.2,0,1),np.zeros_like(ev),np.zeros_like(ev)],axis=-1)
            Image.fromarray(np.rint(diff*255).astype(np.uint8)).save(root/(Path(row['source']).stem+'_hdr_difference.png'))
            record={'source':row['source'],'hdr_preview_ev_median_p95':np.percentile(ev[valid],[50,95]).tolist(),
                    'fraction_above_0_2_ev':float(np.mean(ev[valid]>.2))}
            rows.append(record);seen.add(row['source']);atomic_json(root/'hdr-preview-comparison.json',rows);print(json.dumps(record),flush=True)
    if not a.follow_pid:break
    try:os.kill(a.follow_pid,0)
    except ProcessLookupError:break
    time.sleep(5)
