"""JSON-lines controller. Heavy jobs own process groups; no HTTP listener."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

PROTOCOL = 2


def error_code(error):
    from .errors import DependencyError, DiskSpaceError, InputError, LocalSelectionError
    if isinstance(error, DependencyError): return 'missing_dependency'
    if isinstance(error, DiskSpaceError) or isinstance(error, OSError) and error.errno == 28: return 'disk_full'
    if isinstance(error, PermissionError): return 'permission_denied'
    if isinstance(error, (InputError, json.JSONDecodeError)): return 'invalid_input'
    if isinstance(error, LocalSelectionError): return 'local_asset_missing'
    return 'processing_failed'


def emit(data):
    print(json.dumps(data, ensure_ascii=False, allow_nan=False), flush=True)


def signal_task_group(pid, sig):
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        # Darwin can retain a group containing only zombies briefly after the
        # leader has been reaped. Such a group has nobody left to signal.
        # Ignore only that case; never hide a refusal to stop a live process.
        members = task_group_members(pid)
        # Darwin can reject a group signal during process teardown even when
        # its remaining members can be signalled individually. Restrict the
        # fallback to this exact group and our own uid; never sweep by name.
        for member, uid, state in members:
            if uid != os.getuid():
                raise PermissionError(f'Cannot stop task group {pid}: member {member} belongs to another user')
            try:
                if os.getpgid(member) != pid:
                    continue
                os.kill(member, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                if any(p == member for p, _, _ in task_group_members(pid)):
                    raise
        print(f'Task group {pid}: individual signal fallback ({len(members)} live members)', file=sys.stderr)


def task_group_members(group):
    listing = subprocess.check_output(['ps', '-axo', 'pid=,pgid=,uid=,stat='], text=True)
    return [(int(parts[0]), int(parts[2]), parts[3]) for line in listing.splitlines()
            if len(parts := line.split()) == 4 and int(parts[1]) == group and not parts[3].startswith('Z')]


def watch_controller():
    owner = os.environ.get('HDRIMG_CONTROLLER_PID')
    if not owner:
        return
    import threading
    def watch():
        while True:
            time.sleep(.25)
            if os.getppid() != int(owner):
                os.killpg(os.getpgrp(), signal.SIGTERM)
                time.sleep(.25)
                os.killpg(os.getpgrp(), signal.SIGKILL)
    threading.Thread(target=watch, daemon=True).start()


def job(request):
    watch_controller()
    from dataclasses import asdict
    from . import tools
    from .editor import EditorStore, EditRecipe, atomic_json, json_record
    envelope = {k: request.get(k) for k in ('id', 'session_id', 'revision')}
    def progress(phase):
        emit({**envelope, 'event': 'progress', 'phase': phase, 'phase_code': phase_code(phase)})
    store = EditorStore(cache=Path(request['cache']) if request.get('cache') else None,
                        support=Path(request['support']) if request.get('support') else None,
                        progress=progress)
    tools.PROGRESS_CALLBACK = lambda label: progress(stage_label(label))
    try:
        command = request['command']
        if command == 'hello':
            from .doctor import run_doctor
            stamp = store.support / 'doctor.json'
            previous = json_record(stamp) or {}
            checks = previous.get('checks')
            if (previous.get('engine') == store.engine and previous.get('ok') is True and
                    isinstance(checks, list) and checks and all(isinstance(c, dict) and
                    c.get('ok') is True and isinstance(c.get('name'), str) and
                    isinstance(c.get('detail'), str) for c in checks)):
                checks = previous['checks']; ok = True
            else:
                progress('正在检查本机图像工具')
                ok, values = run_doctor()
                checks = [asdict(value) for value in values]
                atomic_json(stamp, {'engine': store.engine, 'checks': checks, 'ok': ok})
            if not ok:
                from .errors import DependencyError
                raise DependencyError('\n'.join(c['name'] + ': ' + c['detail'] for c in checks if not c['ok']))
            emit({**envelope, 'event': 'result', 'protocol': PROTOCOL, 'engine': store.engine,
                  'checks': checks, 'capabilities': ['float_preview_v1', 'float_preview_v2', 'local_adjustments_v1', 'structured_progress']})
            return
        source, saved_recipe, changed = store.restore(Path(request['source']), request.get('expected_sha'))
        recipe = EditRecipe.from_dict(request['recipe']) if request.get('recipe') else saved_recipe
        if command == 'select_region':
            result = store.select_region(source, recipe, request.get('point'))
            emit({**envelope, 'event': 'result', 'result': result})
            return
        if not request.get('comparison'):
            store.remember(source, recipe)
        emit({**envelope, 'event': 'opened', 'source': source, 'recipe': asdict(recipe),
              'engine_changed': changed})
        if command == 'export':
            result = store.export(source, recipe, Path(request['destination']),
                include_sdr=bool(request.get('include_sdr')), strip_metadata=bool(request.get('strip_metadata')),
                overwrite=bool(request.get('overwrite')))
        elif command in ('prepare', 'preview', 'full_render'):
            result = store.render(source, recipe, full=command == 'full_render',
                                  remember=not request.get('comparison'),
                                  floating_preview=request.get('preview_format') in ('float_v1', 'float_v2'),
                                  preview_version=2 if request.get('preview_format') == 'float_v2' else 1)
        else:
            raise ValueError('Unknown command: ' + command)
        if store.restore_warning:
            result['warning'] = store.restore_warning
        emit({**envelope, 'event': 'result', 'result': result})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit({**envelope, 'event': 'error', 'message': str(exc), 'type': type(exc).__name__, 'error_code': error_code(exc),
              **({'region_id': exc.region_id} if hasattr(exc, 'region_id') else {})})
        sys.exit(1)


def phase_code(phase):
    return {'正在显影 RAW': 'develop', '正在分析照片与建立预览缓存': 'analyze',
        '正在生成全尺寸图像': 'full_render', '正在更新预览': 'refine',
        '正在编码 Ultra HDR': 'encode', '正在检查导出图像': 'validate',
        '正在检查本机图像工具': 'dependencies', '正在准备色彩信息': 'color',
        '正在检查 HDR 文件': 'validate', '正在处理拍摄信息': 'metadata', '正在保存文件': 'save'}.get(phase, 'process')


def stage_label(label):
    if label.startswith('RAW development'):
        return '正在显影 RAW'
    if 'ICC' in label:
        return '正在准备色彩信息'
    if 'encoding' in label:
        return '正在编码 Ultra HDR'
    if 'decode' in label or 'probe' in label:
        return '正在检查 HDR 文件'
    if 'metadata' in label:
        return '正在处理拍摄信息'
    return '正在处理照片'


class Controller:
    def __init__(self):
        self.task = None
        self.process = None
        self.request = None
        self.started = asyncio.Event()
        import tempfile
        import shutil
        for path in Path(tempfile.gettempdir()).glob('img2uhdr-job-*-*'):
            try:
                owner = int(path.name.split('-')[2])
                os.kill(owner, 0)
            except ProcessLookupError:
                shutil.rmtree(path, ignore_errors=True)
            except (ValueError, PermissionError):
                pass

    async def stop(self):
        if self.task and not self.task.done() and self.process is None:
            await self.started.wait()
        pid = self.process.pid if self.process else None
        if self.process:
            if self.process.returncode is None:
                signal_task_group(pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except asyncio.TimeoutError:
                    pass
            # A dead leader can still have live RawTherapee descendants.
            signal_task_group(pid, signal.SIGKILL)
            deadline = time.monotonic() + 1
            while task_group_members(pid):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Previous task is still stopping; retry shortly')
                await asyncio.sleep(.05)
        if self.task:
            await asyncio.wait_for(asyncio.shield(self.task), 1)
        if pid:
            import shutil
            root = Path(self.request['cache']) if self.request and self.request.get('cache') else Path.home() / 'Library/Caches/Img2UltraHDR'
            for family, prefix in [('scenes', '.prepare-'), ('renders', '.render-'), ('developments', '.develop-')]:
                for path in (root / family).glob(f'{prefix}{pid}-*'):
                    shutil.rmtree(path, ignore_errors=True)
            if self.request and self.request.get('destination'):
                parent = Path(self.request['destination']).expanduser().absolute().parent
                for path in parent.glob(f'.img2uhdr-{pid}-*'):
                    shutil.rmtree(path, ignore_errors=True)
        self.task = self.process = None

    async def run_job(self, request):
        identity = {k: request.get(k) for k in ('id', 'session_id', 'revision')}
        terminal = False
        import tempfile
        import shutil
        # All nested float buffers and detector scratch belong to this job.
        # SIGKILL cannot run Python context-manager cleanup, so the controller
        # owns this directory and removes it after the whole group has stopped.
        scratch = tempfile.mkdtemp(prefix=f'img2uhdr-job-{os.getpid()}-')
        try:
            environment = dict(os.environ, TMPDIR=scratch, HDRIMG_CONTROLLER_PID=str(os.getpid()))
            self.process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'hdrimg.worker', '--job',
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=None, start_new_session=True, env=environment, limit=2*1024*1024)
            self.started.set()
            self.process.stdin.write((json.dumps(request) + '\n').encode())
            await self.process.stdin.drain()
            self.process.stdin.close()
            async for line in self.process.stdout:
                try:
                    event = json.loads(line)
                    terminal |= event.get('event') in ('result', 'error')
                    emit(event)
                except (ValueError, UnicodeDecodeError):
                    print(line.decode(errors='replace'), file=sys.stderr)
            code = await self.process.wait()
            if not terminal:
                emit({**identity, 'event': 'cancelled' if code < 0 else 'error',
                      'message': '任务已取消' if code < 0 else '后台任务意外结束，请重试'})
        except Exception as exc:
            emit({**identity, 'event': 'error', 'message': str(exc), 'error_code': error_code(exc)})
        finally:
            if self.process is not None:
                try:
                    signal_task_group(self.process.pid, signal.SIGKILL)
                except OSError:
                    # stop() verifies the group before starting another job.
                    # A cleanup failure must not terminate the controller.
                    traceback.print_exc(file=sys.stderr)
            shutil.rmtree(scratch, ignore_errors=True)
            self.started.set()

    async def run(self):
        reader = asyncio.StreamReader(limit=1024*1024)
        await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        try:
            while line := await reader.readline():
                request = None
                stopping = False
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        from .errors import InputError
                        raise InputError('Worker request must be an object')
                    command = request.get('command')
                    if command in ('cancel', 'close', 'hello', 'prepare', 'preview', 'full_render', 'export', 'select_region'):
                        stopping = True
                        await self.stop()
                        stopping = False
                    if command == 'cancel':
                        emit({**{k: request.get(k) for k in ('id', 'session_id', 'revision')}, 'event': 'cancelled'})
                    elif command == 'close':
                        break
                    elif command in ('hello', 'prepare', 'preview', 'full_render', 'export', 'select_region'):
                        self.request = request
                        self.started.clear()
                        self.task = asyncio.create_task(self.run_job(request))
                        # Yield so the process starts before processing a buffered cancel.
                        await asyncio.sleep(0)
                    else:
                        emit({'event': 'error', 'id': request.get('id'), 'message': 'Unsupported command'})
                except Exception as exc:
                    traceback.print_exc(file=sys.stderr)
                    identity = {k: request.get(k) for k in ('id', 'session_id', 'revision')} if isinstance(request, dict) else {}
                    emit({**identity, 'event': 'error', 'message': str(exc),
                          'error_code': 'cancel_failed' if stopping else error_code(exc)})
        finally:
            try:
                await self.stop()
            except Exception:
                traceback.print_exc(file=sys.stderr)


def main():
    if '--job' in sys.argv:
        request = json.loads(sys.stdin.readline())
        try:
            job(request)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            emit({**{k: request.get(k) for k in ('id', 'session_id', 'revision')},
                  'event': 'error', 'message': str(exc), 'type': type(exc).__name__, 'error_code': error_code(exc)})
            sys.exit(1)
    else:
        asyncio.run(Controller().run())


if __name__ == '__main__':
    main()
