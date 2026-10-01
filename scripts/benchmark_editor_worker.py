"""Measure cached previews through the actual JSONL controller and child jobs.

Existing, unchanged RAW float inputs are hard-linked into a new test cache.
Their original engine IDs are recorded; only test manifests are relabeled to
the current engine, so normal source hashing and child imports are timed too.
Pause PIDs must belong to this task's own validation jobs, never user apps.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

import numpy as np
from hdrimg.editor import EditRecipe, EditorStore, atomic_json, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prepared_cache', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--pause-pid', type=int, action='append', default=[])
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ.pop('HDRIMG_ENGINE_ID', None)
    store = EditorStore(cache=args.output/'cache', support=args.output/'support')
    fixtures = []
    for manifest in sorted((args.prepared_cache/'scenes').glob('*/complete.json')):
        prepared = json.loads(manifest.read_text())
        source = prepared['source']
        key = digest({'source': source['sha256'], 'engine': store.engine,
                      'development': EditRecipe().development_key()})
        old, new = manifest.parent.resolve(), store.cache/'scenes'/key
        new.mkdir()
        for file in old.iterdir():
            if file.name != 'complete.json':
                os.link(file, new/file.name)

        def remap(value):
            if isinstance(value, str): return value.replace(str(old), str(new))
            if isinstance(value, list): return [remap(v) for v in value]
            if isinstance(value, dict): return {k: remap(v) for k, v in value.items()}
            return value

        prepared = remap(prepared)
        prepared['benchmark_original_engine'] = prepared['engine']
        prepared.update(engine=store.engine, key=key)
        atomic_json(new/'complete.json', prepared)
        fixtures.append((source, prepared['benchmark_original_engine']))
    assert fixtures, 'No prepared scenes'
    paused = []

    def resume():
        for group in paused:
            try: os.killpg(group, signal.SIGCONT)
            except ProcessLookupError: pass
        paused.clear()

    def interrupted(sig, frame):
        resume()
        raise SystemExit(128+sig)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    rows = []
    with (args.output/'worker.log').open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'hdrimg.worker'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = b''
        try:
            for repeat in range(args.repeats):
                for source, original_engine in fixtures:
                    recipe = EditRecipe(exposure_ev=.271+repeat*.011, shadow_ev=.2)
                    request = {'command': 'preview', 'id': str(len(rows)), 'session_id': 'benchmark',
                        'revision': len(rows), 'source': source['path'], 'expected_sha': source['sha256'],
                        'recipe': asdict(recipe), 'preview_format': 'float_v1',
                        'cache': str(store.cache), 'support': str(store.support)}
                    try:
                        for pid in args.pause_pid:
                            group = os.getpgid(pid)
                            if group == os.getpgrp(): raise ValueError('Cannot pause benchmark group')
                            os.killpg(group, signal.SIGSTOP)
                            paused.append(group)
                        start = time.monotonic()
                        process.stdin.write(json.dumps(request).encode()+b'\n')
                        process.stdin.flush()
                        result = None
                        while result is None and time.monotonic()-start < 90:
                            if b'\n' not in pending:
                                if not selector.select(.2): continue
                                part = os.read(process.stdout.fileno(), 65536)
                                if not part: raise RuntimeError('Controller closed')
                                pending += part
                                continue
                            line, pending = pending.split(b'\n', 1)
                            event = json.loads(line)
                            if event.get('event') == 'error': raise RuntimeError(event)
                            if event.get('phase_code') == 'develop':
                                raise RuntimeError('Cached fixture was unexpectedly redeveloped')
                            if event.get('event') == 'result': result = event['result']
                        elapsed = time.monotonic()-start
                        if result is None: raise TimeoutError(source['path'])
                        assert not result['cache_hit']
                    finally:
                        resume()
                    rows.append({'source': Path(source['path']).name, 'repeat': repeat,
                        'seconds': elapsed, 'render_seconds': result['elapsed_seconds'],
                        'prepared_engine': original_engine, 'current_engine': store.engine})
                    values = [row['seconds'] for row in rows]
                    atomic_json(args.output/'benchmark.json', {'rows': rows,
                        'median_seconds': float(np.median(values)),
                        'p95_seconds': float(np.percentile(values, 95)),
                        'method': 'JSONL request to result, including source fingerprint and child startup; fixed unchanged prepared floats, no RAW redevelopment; excludes native texture upload.'})
                    print(json.dumps(rows[-1]), flush=True)
                    time.sleep(.2)
        finally:
            resume()
            try:
                process.stdin.write(b'{"command":"close"}\n')
                process.stdin.flush()
            except BrokenPipeError:
                pass
            process.communicate(timeout=8)
            selector.close()


if __name__ == '__main__':
    main()
