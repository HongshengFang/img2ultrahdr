from pathlib import Path

import pytest

from hdrimg import pipeline, tools
from hdrimg.errors import DependencyError, InputError, ProcessingError
from hdrimg.pipeline import RenderOptions, _check_conflicts, _output_paths
from hdrimg.raw import RAW_DEVELOPMENT_EV, _white_balance_overlay, validate_raw_input
from hdrimg.tools import ToolPaths


def test_wrong_extension_and_missing_input_are_rejected(tmp_path: Path):
    wrong = tmp_path / "photo.jpg"
    wrong.write_bytes(b"jpeg")
    with pytest.raises(InputError, match="supports CR2 and RAF"):
        validate_raw_input(wrong)
    with pytest.raises(InputError, match="does not exist"):
        validate_raw_input(tmp_path / "missing.CR2")


def test_existing_output_requires_explicit_overwrite(tmp_path: Path):
    source = tmp_path / "photo.CR2"
    paths = _output_paths(source, tmp_path, keep=False)
    paths["sdr"].write_bytes(b"existing")
    with pytest.raises(InputError, match="--overwrite"):
        _check_conflicts(paths, overwrite=False)
    _check_conflicts(paths, overwrite=True)


def test_missing_dependencies_are_reported_together(monkeypatch):
    monkeypatch.setattr(tools, "find_tool", lambda _name: None)
    with pytest.raises(DependencyError, match="rawtherapee-cli") as error:
        tools.resolve_tools()
    assert "ultrahdr_app" in str(error.value)
    assert "exiftool" in str(error.value)


def test_unicode_space_path_and_failed_raw_cleanup(monkeypatch, tmp_path: Path):
    source = tmp_path / "坏 文件.CR2"
    source.write_bytes(b"not actually a raw file")
    output = tmp_path / "输出 文件"
    fake_tools = ToolPaths(Path("rawtherapee-cli"), Path("ultrahdr_app"), Path("exiftool"))
    monkeypatch.setattr(pipeline, "resolve_tools", lambda **_kwargs: fake_tools)

    def fail_development(*_args, work_dir: Path, **_kwargs):
        assert work_dir.is_dir()
        raise ProcessingError("corrupt RAW")

    monkeypatch.setattr(pipeline, "develop_raw", fail_development)
    with pytest.raises(ProcessingError, match="corrupt RAW"):
        pipeline.render_raw(source, RenderOptions(output=output))
    assert output.is_dir()
    assert not list(output.glob(".hdrimg-*"))
    assert not list(output.glob("坏 文件_*"))


def test_raw_overlay_reserves_two_stops_and_supports_custom_white_balance():
    text = _white_balance_overlay("custom", 5600, 1.05)
    assert "Setting=Custom" in text
    assert "Temperature=5600" in text
    assert "Green=1.050000" in text
    assert f"Compensation={RAW_DEVELOPMENT_EV:.6f}" in text
