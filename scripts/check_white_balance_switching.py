"""Exercise real CR2/RAF mode changes in an isolated worker/cache.

Cancellation is tested during RAW development, then all three modes and cache
returns complete. Only this output directory is written; daily edits are safe.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from hdrimg.editor import EditRecipe, atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('sources', nargs='+', type=Path)
    parser.add_argument('--rapid-only', action='store_true', help='Only stress cancellation, without preparing all modes')
    args = parser.parse_args()
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    config = json.loads((Path.home()/'Applications/Img2UltraHDR.app/Contents/Resources/engine.json').read_text())
    env = dict(os.environ, PYTHONUNBUFFERED='1', HDRIMG_VISION_FAST_TILES='1', **config['tools'])
    resources = Path.home()/'Applications/Img2UltraHDR.app/Contents/Resources'
    env.update(HDRIMG_ACCELERATOR=str(resources/'libhdreditor.dylib'), HDRIMG_VISION_HELPER=str(resources/'phone-subject'))
    events = queue.Queue(); history = []; rows = []; serial = 0
    with (root/'worker.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'hdrimg.worker'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1, env=env)
        def read():
            for line in process.stdout:
                events.put(json.loads(line))
        threading.Thread(target=read, daemon=True).start()
        def send(command, source=None, mode='auto'):
            nonlocal serial
            serial += 1
            message = dict(command=command, id=str(serial), session_id=str(source), revision=serial,
                           cache=str(root/'cache'), support=str(root/'support'), preview_format='float_v1')
            if source:
                message.update(source=str(source.resolve()), recipe=asdict(EditRecipe(white_balance=mode)))
            process.stdin.write(json.dumps(message)+'\n'); process.stdin.flush()
            return str(serial)
        def wait(identity, wanted='result', timeout=180):
            deadline = time.monotonic()+timeout
            while time.monotonic()<deadline:
                if process.poll() is not None: raise RuntimeError('Controller exited during WB switching')
                try: event = events.get(timeout=.1)
                except queue.Empty: continue
                history.append(event)
                if event.get('id')!=identity: continue
                if event.get('event')=='error': raise RuntimeError(event)
                if wanted=='develop' and event.get('phase_code')=='develop': return event
                if event.get('event')==wanted: return event
            raise TimeoutError(f'{identity}: {wanted}')
        try:
            for source in args.sources:
                identity = send('preview', source, 'auto'); wait(identity, 'develop')
                time.sleep(1)  # RawTherapee is in flight, not just Python startup.
                for i in range(18):
                    identity = send('preview', source, ['camera','custom','auto'][i%3])
                    time.sleep([.02,.08,.3][i%3])
                began = time.monotonic(); cancel = send('cancel'); wait(cancel, 'cancelled', 5)
                rows.append(dict(source=source.name, kind='cancel_after_18_switches', seconds=time.monotonic()-began))
                if args.rapid_only:
                    continue
                for mode in ['auto','camera','custom','auto','camera','custom']:
                    began = time.monotonic(); identity = send('preview', source, mode)
                    event = wait(identity); result = event['result']
                    assert result['recipe']['white_balance']==mode
                    assert result['preview_packet']['anchor_recipe']['white_balance']==mode
                    rows.append(dict(source=source.name, kind=mode, seconds=time.monotonic()-began,
                                     cache_hit=result['cache_hit'], development=result['raw_development']))
                    atomic_json(root/'checks.json', dict(rows=rows, controller_alive=process.poll() is None))
                    print(json.dumps({k:v for k,v in rows[-1].items() if k!='development'}), flush=True)
            send('close'); process.wait(timeout=5)
            assert process.returncode==0
        finally:
            if process.poll() is None:
                try: send('close'); process.wait(timeout=5)
                except Exception: process.kill(); process.wait()
            atomic_json(root/'events.json', history)
    atomic_json(root/'checks.json', dict(rows=rows, passed=True, controller_exit_code=process.returncode))


if __name__=='__main__': main()
