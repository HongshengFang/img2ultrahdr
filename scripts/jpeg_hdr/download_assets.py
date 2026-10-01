"""Download official weights and MSYS2 reference-codec binaries locally."""
from pathlib import Path
import concurrent.futures, hashlib, json, tarfile, os
import requests
import zstandard

WORK = Path(os.environ.get('IMG2UHDR_JPEG_HOME', '.jpeg-hdr')).expanduser().resolve()
WEIGHTS = WORK / 'weights'
NATIVE = WORK / 'native'
RELEASE = 'https://github.com/compphoto/IntrinsicHDR/releases/download/v1.0/'

def download(url, path):
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.part')
    print('Downloading', path.name, flush=True)
    with requests.get(url, stream=True, timeout=(30, 180)) as r:
        r.raise_for_status()
        with temp.open('wb') as f:
            for chunk in r.iter_content(1024 * 1024):
                f.write(chunk)
    temp.replace(path)
    return path

def main():
    names = ['vivid_bird_318_300.pt', 'fluent_eon_138_200.pt',
             'sh_weights.ckpt', 'alb_weights.ckpt', 'ref_weights.ckpt']
    jobs = [(RELEASE + n, WEIGHTS / n) for n in names]
    jobs.append(('https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt',
                 WEIGHTS / 'sam2.1_hiera_tiny.pt'))
    # Native packages stay in the workspace; no system MSYS2 installation needed.
    packages = ['libultrahdr-1.5.1-1', 'gcc-libs-16.2.0-4', 'libgcc-16.2.0-4', 'libstdc++-16.2.0-4', 'zlib-1.3.2-2',
                'libjpeg-turbo-3.2.0-1', 'libwinpthread-14.0.0.r426.g4564ee4b5-1',
                'angleproject-2.1.r25748.890b5d8f-6']
    for name in packages:
        filename = 'mingw-w64-ucrt-x86_64-' + name + '-any.pkg.tar.zst'
        jobs.append(('https://mirror.msys2.org/mingw/ucrt64/' + filename, WORK / 'downloads' / filename))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda job: download(*job), jobs))
    NATIVE.mkdir(exist_ok=True)
    manifest = []
    for url, path in jobs:
        with path.open('rb') as downloaded:
            digest = hashlib.file_digest(downloaded, 'sha256').hexdigest()
        manifest.append(dict(url=url, file=str(path.relative_to(WORK)), sha256=digest))
        if not path.name.endswith('.zst'):
            continue
        with path.open('rb') as f, zstandard.ZstdDecompressor().stream_reader(f) as stream:
            with tarfile.open(fileobj=stream, mode='r|') as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    if member.name.startswith('ucrt64/bin/') or member.name.endswith('ultrahdr_api.h'):
                        target = NATIVE / Path(member.name).name
                    elif '/share/licenses/' in member.name:
                        target = NATIVE / 'licenses' / member.name.split('/share/licenses/', 1)[1]
                    else:
                        continue
                    if not target.resolve().is_relative_to(NATIVE.resolve()):
                        raise ValueError(f'Unsafe archive path: {member.name}')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(member).read())
    (WORK / 'downloads-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Weights and codec ready.', flush=True)

if __name__ == '__main__':
    main()
