"""Non-destructive local editor sessions shared by the macOS app and worker.

A cache entry is visible only after its completion record has been published.
Original RAW files and previously exported files are never used as scratch space.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Callable

import numpy as np
import tifffile
from PIL import Image

from .errors import InputError, ProcessingError
from .look import resolve_look
from .metadata import copy_metadata, read_output_metadata, read_source_metadata
from .phone_skin import build_skin_context
from .phone_tone import PhoneSceneDecision
from .pipeline import RenderOptions, prepare_raw_scene
from .raw import RAW_DEVELOPMENT_EV, validate_raw_input
from .render import RenderPolicy, open_scene, render_pair
from .style import STYLE_PRESETS
from .tone import exposure_statistics
from .tools import resolve_tools
from .ultrahdr import encode_ultrahdr, validate_ultrahdr

PREVIEW_EDGE = 1536
CACHE_LIMIT = 10 * 1024**3
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class EditRecipe:
    schema_version: int = SCHEMA_VERSION
    style: str = 'phone-clear'
    white_balance: str = 'auto'
    temperature_k: int = 5600
    tint: float = 0.0
    exposure_ev: float = 0.0
    highlight_ev: float = 0.0
    shadow_ev: float = 0.0
    white_ev: float = 0.0
    black_ev: float = 0.0
    saturation: float = 1.0
    hdr_strength: float = 1.0
    sdr_exposure_ev: float = 0.0

    @classmethod
    def from_dict(cls, data: dict) -> 'EditRecipe':
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise InputError('Unknown editing settings: ' + ', '.join(sorted(unknown)))
        value = cls(**data)
        value.validate()
        return value

    def validate(self):
        if self.schema_version != SCHEMA_VERSION:
            raise InputError('Unsupported editing record version')
        if self.style not in ('phone-clear', 'phone-natural'):
            raise InputError('Choose Clear or Natural')
        if self.white_balance not in ('auto', 'camera', 'custom'):
            raise InputError('Invalid white balance mode')
        for name, low, high in [('temperature_k', 2000, 15000), ('tint', -100, 100),
            ('exposure_ev', -3, 3), ('highlight_ev', -2, 2), ('shadow_ev', -2, 2),
            ('white_ev', -2, 2), ('black_ev', -2, 2),
            ('saturation', .8, 1.2), ('hdr_strength', 0, 1), ('sdr_exposure_ev', -2, 2)]:
            number = getattr(self, name)
            if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or not low <= number <= high:
                raise InputError(f'Invalid {name}; expected {low}..{high}')
        if int(self.temperature_k) != self.temperature_k:
            raise InputError('Temperature must be a whole number')

    def raw_options(self, work: Path) -> RenderOptions:
        return RenderOptions(output=work, style=self.style, white_balance=self.white_balance,
            temperature_k=int(self.temperature_k) if self.white_balance == 'custom' else None,
            tint=2**(self.tint / 100) if self.white_balance == 'custom' else 1.0,
            raw_denoise_strength=1.0, raw_detail_strength=1.0, surface_denoise_strength=1.0,
            skin_protection_strength=1.0, subject_adaptation_strength=1.0)

    def development_key(self):
        # UI-only remembered custom values must not invalidate an Auto scene.
        return {'style': self.style, 'white_balance': self.white_balance,
                'temperature_k': self.temperature_k if self.white_balance == 'custom' else None,
                'tint': self.tint if self.white_balance == 'custom' else None}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.partial')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, path)


def engine_identity() -> str:
    root = Path(__file__).parent
    source = [(str(p.relative_to(root)), file_digest(p)) for p in sorted(root.rglob('*'))
              if p.is_file() and p.suffix in ('.py', '.pp3', '.b64', '.swift')]
    tools = resolve_tools()
    binaries = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in vars(tools).values()]
    from .pipeline import _runtime_versions
    helpers = {key: file_digest(Path(value)) for key in ('HDRIMG_ACCELERATOR', 'HDRIMG_VISION_HELPER')
               if (value := os.environ.get(key)) and Path(value).is_file()}
    return digest({'source': source, 'tools': binaries, 'runtime': _runtime_versions(), 'helpers': helpers})


def source_identity(path: Path) -> dict:
    path = validate_raw_input(path)
    before = path.stat()
    sha = file_digest(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise InputError('The original changed while it was being read')
    return {'path': str(path), 'sha256': sha, 'size': after.st_size, 'mtime_ns': after.st_mtime_ns}


def check_source(source: dict):
    path = Path(source['path'])
    try:
        stat = path.stat()
    except OSError as exc:
        raise InputError('Original unavailable; locate the RAW again') from exc
    if stat.st_size != source['size'] or stat.st_mtime_ns != source['mtime_ns']:
        raise InputError('Original changed; reopen it before continuing')


def resize_scene(scene: np.ndarray, edge: int) -> np.ndarray:
    h, w = scene.shape[:2]
    scale = min(1., edge / max(h, w))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return np.stack([np.asarray(Image.fromarray(scene[..., c]).resize(size, Image.Resampling.BOX))
                     for c in range(3)], axis=-1).astype(np.float32)


def write_scene(path: Path, pixels: np.ndarray, icc: bytes):
    tifffile.imwrite(path, pixels, photometric='rgb', extratags=[(34675, 'B', len(icc), icc, False)])


class EditorStore:
    def __init__(self, cache: Path | None = None, support: Path | None = None, progress: Callable | None = None):
        self.cache = (cache or Path.home() / 'Library/Caches/Img2UltraHDR').expanduser().resolve()
        self.support = (support or Path.home() / 'Library/Application Support/Img2UltraHDR').expanduser().resolve()
        for directory in (self.cache, self.support, self.cache / 'scenes', self.cache / 'renders', self.cache / 'metering', self.cache / 'derived', self.cache / 'developments'):
            directory.mkdir(parents=True, exist_ok=True)
        for family, prefix in [('scenes', '.prepare-'), ('renders', '.render-'), ('developments', '.develop-')]:
            for path in (self.cache / family).glob(prefix + '*'):
                try:
                    pid = int(path.name[len(prefix):].split('-', 1)[0])
                    os.kill(pid, 0)
                except ProcessLookupError:
                    shutil.rmtree(path, ignore_errors=True)
                except (ValueError, PermissionError):
                    pass
        self.progress = progress or (lambda phase: None)
        self.engine = os.environ.get('HDRIMG_ENGINE_ID') or engine_identity()
        # Derived masks must expire with the algorithm as well as their pixels.
        derived = self.cache / 'derived' / self.engine
        derived.mkdir(parents=True, exist_ok=True)
        os.environ['HDRIMG_DERIVED_CACHE'] = str(derived)

    def remember(self, source: dict, recipe: EditRecipe):
        record = {'source': source, 'recipe': asdict(recipe), 'engine': self.engine,
                  'updated': time.time()}
        atomic_json(self.support / 'edits' / (source['sha256'] + '.json'), record)
        atomic_json(self.support / 'last-session.json', record)

    def restore(self, path: Path, expected_sha: str | None = None):
        source = source_identity(path)
        if expected_sha and source['sha256'] != expected_sha:
            raise InputError('Selected RAW does not match the saved original')
        record_path = self.support / 'edits' / (source['sha256'] + '.json')
        record = json.loads(record_path.read_text()) if record_path.is_file() else None
        recipe = EditRecipe.from_dict(record['recipe']) if record else EditRecipe()
        draft_path = self.support / 'drafts' / (source['sha256'] + '.json')
        if draft_path.is_file():
            draft = json.loads(draft_path.read_text())
            if draft.get('updated', 0) > (record or {}).get('updated', 0):
                recipe = EditRecipe.from_dict(draft['recipe'])
        return source, recipe, bool(record and record.get('engine') != self.engine)

    def prune(self, protected: set[Path] | None = None):
        protected = {p.resolve() for p in (protected or set())}
        # Native readers lease active immutable packets while asynchronous GPU
        # uploads are in flight. Expire leases only after their owner exits.
        leases = self.cache / 'leases'
        if leases.exists():
            for lease in leases.glob('*.json'):
                try:
                    record = json.loads(lease.read_text())
                    os.kill(int(record['pid']), 0)
                    protected.update(Path(p).resolve() for p in record['paths'])
                except ProcessLookupError:
                    lease.unlink(missing_ok=True)
                except (OSError, ValueError, KeyError):
                    continue
        entries = []
        for family in ('scenes', 'renders', 'metering', 'derived', 'developments'):
            for path in (self.cache / family).iterdir():
                if path.resolve() in protected or path.name.startswith('.'):
                    continue
                size = sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else path.stat().st_size
                entries.append((path.stat().st_mtime, size, path))
        total = sum(p.stat().st_size for p in self.cache.rglob('*') if p.is_file())
        for _, size, path in sorted(entries):
            if total <= CACHE_LIMIT:
                break
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            total -= size

    def develop_cached(self, identity: dict, source: Path, destination: Path, **options):
        from .raw import develop_raw
        profile = options.get('profile_overlay')
        parameters = {k: options[k] for k in ('white_balance', 'temperature_k', 'tint')}
        parameters['temperature_bias'] = options.get('temperature_bias', 0.0)
        parameters['profile_overlay'] = profile.read_text() if profile is not None else None
        key = digest({'source': identity['sha256'], 'engine': self.engine, 'parameters': parameters})
        target = self.cache / 'developments' / key
        retained = target / 'scene.tif'
        if (target / 'complete.json').is_file() and retained.is_file():
            os.link(retained, destination)
            os.utime(target, None)
            self.progress('正在复用白平衡显影缓存')
            return
        develop_raw(source, destination, **options)
        check_source(identity)
        # Hard links keep all consumers immutable without a second full TIFF
        # copy. Cancellation can only leave a hidden, incomplete cache entry.
        with tempfile.TemporaryDirectory(prefix=f'.develop-{os.getpid()}-', dir=self.cache / 'developments') as temp:
            work = Path(temp)
            os.link(destination, work / 'scene.tif')
            atomic_json(work / 'complete.json', {'key': key, 'parameters': parameters})
            if target.exists():
                shutil.rmtree(target)
            work.rename(target)

    def prepare(self, source: dict, recipe: EditRecipe) -> dict:
        recipe.validate()
        check_source(source)
        key = digest({'source': source['sha256'], 'engine': self.engine,
                      'development': recipe.development_key()})
        target = self.cache / 'scenes' / key
        complete = target / 'complete.json'
        if complete.is_file():
            os.utime(target, None)
            return {**json.loads(complete.read_text()), 'source': source}
        self.prune()
        if shutil.disk_usage(self.cache).free < 4 * 1024**3:
            from .errors import DiskSpaceError
            raise DiskSpaceError('At least 4 GB of free disk space is needed for RAW preparation')
        self.progress('正在显影 RAW')
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix=f'.prepare-{os.getpid()}-', dir=self.cache / 'scenes') as temp:
            work = Path(temp)
            tools = resolve_tools()
            options = recipe.raw_options(work)
            meter = self.cache / 'metering' / (digest([source['sha256'], self.engine]) + '.npy')
            prepared = prepare_raw_scene(Path(source['path']), options, style=STYLE_PRESETS[recipe.style],
                tools=tools, work=work, metering_reference=meter,
                raw_developer=lambda *a, **kw: self.develop_cached(source, *a, **kw))
            scene, info = open_scene(prepared.scene)
            if not info.has_icc_profile:
                raise ProcessingError('RAW scene is missing its linear ICC profile')
            with tifffile.TiffFile(prepared.scene) as tf:
                icc = tf.pages[0].tags[34675].value
            self.progress('正在分析照片与建立预览缓存')
            stats = exposure_statistics(scene, auto_exposure=True, exposure_ev=0, development_ev=RAW_DEVELOPMENT_EV)
            look = resolve_look(scene, base_scene_adjustment_ev=stats.scene_adjustment_ev,
                enabled=True, auto_exposure=True, exposure_ev=None, contrast=None, saturation=None)
            step = max(1, int(np.ceil(max(scene.shape[:2]) / 2048)))
            np.save(work / 'analysis.npy', np.asarray(scene[::step, ::step]))
            decision = prepared.scene_decision
            if decision is None:
                from .phone_tone import phone_scene_decision
                decision = phone_scene_decision(scene[::step, ::step],
                    development_ev=RAW_DEVELOPMENT_EV, peak_nits=1000, refine_diffuse=True)
            write_scene(work / 'preview.tif', resize_scene(scene, PREVIEW_EDGE), icc)
            # Cache the same initial scene/person reference that render_pair builds.
            matte, skin_record = build_skin_context(scene,
                exposure_ev=stats.scene_adjustment_ev + look.exposure_ev)
            if matte is not None:
                np.save(work / 'person.npy', np.asarray(matte, np.float32))
            del scene
            final_scene = work / 'retained-scene.tif'
            prepared.scene.rename(final_scene)
            metadata = read_source_metadata(Path(source['path']), tools)
            check_source(source)
            result = {'key': key, 'scene': str(target / 'retained-scene.tif'),
                'preview': str(target / 'preview.tif'), 'source': source,
                'scene_info': asdict(info), 'metadata': metadata,
                'analysis': {'base_stats': asdict(stats), 'look': asdict(look), 'sample': str(target / 'analysis.npy')},
                'scene_decision': asdict(decision),
                'person': str(target / 'person.npy') if matte is not None else None,
                'skin_record': skin_record, 'raw_development': prepared.raw_development,
                'engine': self.engine, 'elapsed_seconds': time.monotonic() - started}
            keep = {'retained-scene.tif', 'preview.tif', 'analysis.npy', 'person.npy'}
            for path in work.iterdir():
                if path.name not in keep:
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
            atomic_json(work / 'complete.json', result)
            if target.exists():
                shutil.rmtree(target)
            work.rename(target)
        return result

    def render(self, source: dict, recipe: EditRecipe, *, full: bool = False, strip_metadata: bool = False, remember: bool = True, floating_preview: bool = False) -> dict:
        floating_preview = floating_preview and not full
        recipe.validate()
        check_source(source)
        # A retained preview remains usable even if its large prepared RAW
        # scene was evicted. Consult the immutable render before redevelopment.
        scene_key = digest({'source': source['sha256'], 'engine': self.engine,
                            'development': recipe.development_key()})
        fast_key = digest({'scene': scene_key, 'recipe': asdict(recipe), 'full': full,
                           'strip_metadata': strip_metadata if full else True,
                           **({'preview_format': 1, 'gpu_version': 'scene-tone-2'} if floating_preview else {})})
        fast_target = self.cache / 'renders' / fast_key
        if (fast_target / 'complete.json').is_file():
            if remember:
                self.remember(source, recipe)
            os.utime(fast_target, None)
            return {**json.loads((fast_target / 'complete.json').read_text()), 'source': source, 'cache_hit': True}
        prepared = self.prepare(source, recipe)
        if remember:
            self.remember(source, recipe)
        key = digest({'scene': prepared['key'], 'recipe': asdict(recipe), 'full': full,
                      'strip_metadata': strip_metadata if full else True,
                      **({'preview_format': 1, 'gpu_version': 'scene-tone-2'} if floating_preview else {})})
        target = self.cache / 'renders' / key
        complete = target / 'complete.json'
        if complete.is_file():
            os.utime(target, None)
            return {**json.loads(complete.read_text()), 'source': source, 'cache_hit': True}
        alternate = None
        if full:
            alternate_key = digest({'scene': prepared['key'], 'recipe': asdict(recipe), 'full': True,
                                    'strip_metadata': not strip_metadata})
            alternate_path = self.cache / 'renders' / alternate_key
            if (alternate_path / 'complete.json').is_file() and (alternate_path / 'hdr.rgba16f').is_file():
                alternate = json.loads((alternate_path / 'complete.json').read_text())
        protected = {Path(prepared['scene']).parent}
        if alternate:
            protected.add(Path(alternate['sdr']).parent)
        self.prune(protected)
        w, h = prepared['scene_info']['width'], prepared['scene_info']['height']
        required = max(512 * 1024**2, w*h*96 if full else 512 * 1024**2)
        if shutil.disk_usage(self.cache).free < required:
            from .errors import DiskSpaceError
            raise DiskSpaceError('Not enough free disk space to render this photo')
        started = time.monotonic()
        self.progress('正在生成全尺寸图像' if full else '正在更新预览')
        with tempfile.TemporaryDirectory(prefix=f'.render-{os.getpid()}-', dir=self.cache / 'renders') as temp:
            work = Path(temp)
            tools = resolve_tools()
            if alternate:
                # The HDR encoder input is immutable; retaining it allows a
                # privacy-only export to reuse full-size rendering losslessly.
                original = Path(alternate['sdr']).parent
                shutil.copy2(original / 'sdr.jpg', work / 'sdr.jpg')
                os.link(original / 'hdr.rgba16f', work / 'hdr.rgba16f')
                if strip_metadata:
                    from .tools import run_checked
                    run_checked([tools.exiftool, '-overwrite_original', '-EXIF:all=',
                        '-IPTC:all=', '-XMP:all=', work / 'sdr.jpg'], label='metadata removal', timeout=120)
                render_record = alternate['render']
            else:
                # A negative detection is a completed analysis too.
                context = (None, prepared['skin_record'])
                if prepared['person']:
                    context = (Image.fromarray(np.load(prepared['person'])), prepared['skin_record'])
                info = render_pair(Path(prepared['scene'] if full else prepared['preview']),
                    work / 'sdr.jpg', work / 'hdr.rgba16f', auto_exposure=True, exposure_ev=None,
                    development_ev=RAW_DEVELOPMENT_EV, highlight_ev=recipe.highlight_ev,
                    shadow_ev=recipe.shadow_ev, edit_exposure_ev=recipe.exposure_ev,
                    white_ev=recipe.white_ev, black_ev=recipe.black_ev,
                    saturation_scale=recipe.saturation, hdr_strength=recipe.hdr_strength,
                    sdr_exposure_ev=recipe.sdr_exposure_ev, peak_nits=1000,
                    style=STYLE_PRESETS[recipe.style], _analysis=prepared['analysis'], _skin_context=context,
                    _processing_policy=RenderPolicy(preview_workers=3 if full else 4),
                    # Natural's accepted half-float output can depend on its
                    # original block shape at rounding boundaries. Keep it.
                    chunk_rows=256 if not full and recipe.style == 'phone-clear' else 512,
                    _preview_output=work / 'sdr.rgba16f' if floating_preview else None,
                    _skip_sdr_jpeg=floating_preview,
                    _scene_decision=PhoneSceneDecision(**prepared['scene_decision']) if prepared['scene_decision'] else None)
                render_record = asdict(info)
            render_width, render_height = render_record['scene']['width'], render_record['scene']['height']
            gamut = render_record['style']['sdr_gamut']
            if full and not strip_metadata:
                copy_metadata(Path(source['path']), work / 'sdr.jpg', tools, sdr_gamut=gamut)
            packet = None
            if floating_preview:
                from .preview import make_packet
                packet = make_packet(work, target, prepared, recipe, render_record)
            else:
                self.progress('正在编码 Ultra HDR')
                encode_ultrahdr(work / 'sdr.jpg', work / 'hdr.rgba16f', work / 'ultrahdr.jpg',
                    width=render_width, height=render_height, peak_nits=1000,
                    max_boost=render_record['max_content_boost'], gainmap_quality=render_record['gainmap_quality'],
                    sdr_gamut=gamut, tools=tools)
            validation = None
            if full:
                self.progress('正在检查导出图像')
                validation = asdict(validate_ultrahdr(work / 'ultrahdr.jpg', width=render_width,
                    height=render_height, work_dir=work, tools=tools))
                output_metadata = read_output_metadata(work / 'ultrahdr.jpg', tools)
                if output_metadata.get('Orientation') not in (None, 1):
                    raise ProcessingError('Unexpected output orientation')
                if not strip_metadata and prepared['metadata'].get('Make') != output_metadata.get('Make'):
                    raise ProcessingError('Camera metadata was not retained')
            check_source(source)
            result = {'key': key, 'source': source, 'recipe': asdict(recipe), 'engine': self.engine,
                'sdr': str(target / 'sdr.jpg'), 'ultrahdr': str(target / 'ultrahdr.jpg'),
                'width': render_width, 'height': render_height, 'full': full,
                'render': render_record, 'validation': validation, 'strip_metadata': strip_metadata,
                'raw_development': prepared['raw_development'], 'processing_policy': asdict(RenderPolicy()),
                'elapsed_seconds': time.monotonic() - started, 'cache_hit': False}
            if packet:
                result['preview_packet'] = packet
                result.pop('sdr'); result.pop('ultrahdr')
            for path in work.iterdir():
                keep = ('scene.rgba16f', 'sdr.rgba16f', 'hdr.rgba16f') if floating_preview else (('sdr.jpg', 'ultrahdr.jpg', 'hdr.rgba16f') if full else ('sdr.jpg', 'ultrahdr.jpg'))
                if path.name not in keep:
                    path.unlink()
            atomic_json(work / 'complete.json', result)
            if target.exists():
                shutil.rmtree(target)
            work.rename(target)
        self.prune(protected | {target})
        return result

    def export(self, source: dict, recipe: EditRecipe, destination: Path, *, include_sdr=False,
               strip_metadata=False, overwrite=False) -> dict:
        destination = destination.expanduser().absolute()
        if destination.suffix.lower() not in ('.jpg', '.jpeg'):
            raise InputError('Export must use a .jpg or .jpeg filename')
        destination.parent.mkdir(parents=True, exist_ok=True)
        sdr_destination = destination.with_name(destination.stem.removesuffix('_ultrahdr') + '_sdr.jpg')
        targets = [destination] + ([sdr_destination] if include_sdr else [])
        if len(set(targets)) != len(targets) or Path(source['path']) in targets:
            raise InputError('Output filenames must be distinct from each other and the original')
        if not overwrite and any(p.exists() for p in targets):
            raise InputError('An output file already exists; choose another name or confirm replacement')
        result = self.render(source, recipe, full=True, strip_metadata=strip_metadata)
        check_source(source)
        self.progress('正在保存文件')
        # Copy all outputs before the short commit. Roll back on ordinary errors;
        # worker makes this commit non-cancellable so SIGTERM cannot split it.
        with tempfile.TemporaryDirectory(prefix=f'.img2uhdr-{os.getpid()}-', dir=destination.parent) as temp:
            work = Path(temp)
            staged = []
            for i, output in enumerate(targets):
                copy = work / f'{i}.jpg'
                shutil.copy2(result['ultrahdr'] if i == 0 else result['sdr'], copy)
                staged.append(copy)
            backups, published = {}, []
            for i, output in enumerate(targets):
                if output.exists():
                    if not overwrite:
                        raise InputError('Output appeared while rendering; choose another filename')
                    backup = work / f'backup-{i}'
                    shutil.copy2(output, backup)
                    backups[output] = backup
            import signal
            old_handler = signal.signal(signal.SIGTERM, signal.SIG_IGN)
            try:
                for copy, output in zip(staged, targets):
                    os.replace(copy, output)
                    published.append(output)
            except BaseException:
                for output in published:
                    if output in backups:
                        os.replace(backups[output], output)
                    else:
                        output.unlink(missing_ok=True)
                raise
            finally:
                signal.signal(signal.SIGTERM, old_handler)
        receipt = {**result, 'exported': str(destination),
                   'exported_sdr': str(sdr_destination) if include_sdr else None}
        try:
            atomic_json(self.support / 'exports' / (digest([str(destination), time.time_ns()]) + '.json'), receipt)
        except OSError as exc:
            receipt['warning'] = '图像已保存，但本地导出记录保存失败：' + str(exc)
        return receipt
