"""Run and preserve a complete 12-scene phone-clear experiment."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import tarfile
import shutil
from datetime import datetime, timezone
from pathlib import Path


def run(output: Path, reference: Path, hypothesis: str, scene_cache: Path, skip_phone_color: bool = False,
        residual_models: Path | None = None, source_root: Path = Path('.')):
    # A missing cache must not silently become an unrelated RAW development.
    expected = json.loads((reference / 'audit.json').read_text())['pairs']
    missing = [row['stem'] for row in expected
               if not (scene_cache / (row['stem'] + '.tif')).is_file()]
    if missing:
        raise ValueError(f'Incomplete experiment scene cache: {missing}')
    output.mkdir(parents=True, exist_ok=False)
    state = {"started_utc": datetime.now(timezone.utc).isoformat(),
             "hypothesis": hypothesis, "reference": str(reference.resolve()),
             "scene_cache": str(scene_cache.resolve())}
    state["source_root"] = str(source_root.resolve())
    state["skip_phone_color_refinement"] = skip_phone_color
    if residual_models:
        shutil.copytree(residual_models,output/'calibration_models')
        state['residual_models']=str((output/'calibration_models').resolve())
    (output / "experiment.json").write_text(json.dumps(state, indent=2)+"\n")
    with tarfile.open(output / "source.tar.gz", "w:gz") as archive:
        for folder in ("src", "scripts", "tests", "docs"):
            for path in sorted((source_root / folder).rglob("*")):
                if path.is_file() and "__pycache__" not in str(path):
                    archive.add(path, arcname=path.relative_to(source_root))
        archive.add(source_root / "README.md", arcname="README.md")
        archive.add(source_root / "pyproject.toml", arcname="pyproject.toml")
    frozen = output / "frozen"
    with tarfile.open(output / "source.tar.gz") as archive:
        archive.extractall(frozen, filter="data")
    env = {**os.environ, "PYTHONPATH": str((frozen / "src").resolve())}
    with (output / "audit.log").open("w") as log:
        subprocess.run([
            sys.executable, str(frozen / "scripts/audit_phone_clear.py"), "jpg_hdr_sample_effect",
            "--output", str(output), "--scene-cache", str(scene_cache),
            "--reference-audit", str(reference / "audit.json"),
            "--keep-renders", "--encode-check", "--regions", "docs/phone-clear-regions.json",
            *(["--skip-phone-color-refinement"] if skip_phone_color else []),
            *(["--residual-models",state['residual_models']] if residual_models else []),
        ], stdout=log, stderr=subprocess.STDOUT, check=True, env=env)
    with (output / "tests.log").open("w") as log:
        tests = subprocess.run([sys.executable, "-m", "pytest", "-q", str(frozen / "tests")],
                               stdout=log, stderr=subprocess.STDOUT, env=env)
    state["tests_exit_code"] = tests.returncode
    subprocess.run([sys.executable, str(frozen / "scripts/review_phone_round.py"), str(reference), str(output)],check=True,env=env)
    state["finished_utc"] = datetime.now(timezone.utc).isoformat()
    (output / "experiment.json").write_text(json.dumps(state, indent=2)+"\n")
    print(json.dumps(state, indent=2), flush=True)
    if tests.returncode:
        raise SystemExit(tests.returncode)


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("output",type=Path)
    p.add_argument("--reference",type=Path,required=True)
    p.add_argument("--hypothesis",required=True)
    p.add_argument("--scene-cache",type=Path,default=Path("outputs/phone_reanalysis/scenes"))
    p.add_argument("--skip-phone-color-refinement",action="store_true")
    p.add_argument("--residual-models",type=Path)
    p.add_argument("--source-root", type=Path, default=Path('.'))
    args=p.parse_args();run(args.output,args.reference,args.hypothesis,args.scene_cache,args.skip_phone_color_refinement,args.residual_models,args.source_root)
