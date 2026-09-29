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


def test_denoise_default_respects_styles_and_manual_looks():
    assert RenderOptions(output=Path("out")).resolved_raw_denoise_strength() == 1
    for extra in ({"style": "natural"}, {"style": "phone-natural"},
                  {"auto_look": False}, {"contrast": 1.35}, {"saturation": 1.18},
                  {"raw_denoise_strength": 0}):
        assert RenderOptions(output=Path("out"), **extra).resolved_raw_denoise_strength() == 0
    with pytest.raises(InputError, match="requires --style phone-clear"):
        RenderOptions(output=Path("out"), style="natural", raw_denoise_strength=1).validate()


def test_pipeline_passes_denoise_overlay_only_when_requested(monkeypatch, tmp_path):
    source = tmp_path / "photo.CR2"
    source.write_bytes(b"placeholder")
    fake_tools = ToolPaths(Path("rawtherapee-cli"), Path("ultrahdr_app"), Path("exiftool"))
    monkeypatch.setattr(pipeline, "resolve_tools", lambda **kw: fake_tools)
    observed = []
    def stop_after_development(*args, profile_overlay, **kw):
        observed.append(profile_overlay.read_text() if profile_overlay else None)
        raise ProcessingError("inspection complete")
    monkeypatch.setattr(pipeline, "develop_raw", stop_after_development)
    for extra in ({}, {"style": "natural"}, {"style": "phone-natural"},
                  {"contrast": 1.35, "saturation": 1.18}, {"raw_denoise_strength": 0}):
        with pytest.raises(ProcessingError, match="inspection complete"):
            pipeline.render_raw(source, RenderOptions(output=tmp_path/"out", **extra))
    assert "Luma=15\nLdetail=30\nChroma=60" in observed[0]
    assert observed[1:] == [None] * 4


def test_adaptive_denoise_redevelops_only_when_residual_noise_requires_it(monkeypatch, tmp_path):
    import numpy as np
    import tifffile

    source = tmp_path / "photo.CR2"
    source.write_bytes(b"placeholder")
    fake_tools = ToolPaths(Path("rawtherapee-cli"), Path("ultrahdr_app"), Path("exiftool"))
    monkeypatch.setattr(pipeline, "resolve_tools", lambda **kw: fake_tools)
    calls = []
    def develop(source, destination, *, profile_overlay=None, **kw):
        calls.append((destination.name, profile_overlay.read_text() if profile_overlay else None))
        tifffile.imwrite(destination, np.full((64,64,3), .01, np.float32), photometric="rgb")
    def stop_at_render(scene, *args, **kwargs):
        assert scene.name == calls[-2][0]
        raise ProcessingError("development verified")
    monkeypatch.setattr(pipeline, "develop_raw", develop)
    monkeypatch.setattr(pipeline, "render_pair", stop_at_render)
    for weight, detail, expected in ((0, 0, 1), (.8, 0, 2), (0, 1, 2), (.8, 1, 2)):
        calls.clear()
        monkeypatch.setattr(pipeline, "phone_denoise_decision", lambda *a, **k: {"extra_denoise_weight": weight})
        with pytest.raises(ProcessingError, match="development verified"):
            pipeline.render_raw(source, RenderOptions(output=tmp_path/"out",
                raw_detail_strength=detail, surface_denoise_strength=0))
        assert len(calls) == expected + 1
        assert calls[-1] == ("scene-color-reference.tif", None)
        assert "Luma=15\nLdetail=30" in calls[0][1]
        assert "[Sharpening]" not in calls[0][1]
        if weight:
            assert "Luma=35\nLdetail=6" in calls[-2][1]
        if detail:
            assert "DeconvAmount=65\n" in calls[-2][1]
            assert "DeconvDamping=20\n" in calls[-2][1]
        assert not list((tmp_path/"out").glob(".hdrimg-*"))


def test_detail_and_surface_defaults_preserve_legacy_and_manual_looks():
    options = RenderOptions(output=Path("out"))
    assert options.resolved_raw_detail_strength() == 1
    assert options.resolved_surface_denoise_strength() == 1
    for extra in ({"style":"natural"}, {"style":"phone-natural"},
                  {"auto_look":False}, {"contrast":1.35}, {"saturation":1.18},
                  {"raw_denoise_strength":0}):
        options = RenderOptions(output=Path("out"), **extra)
        assert options.resolved_raw_detail_strength() == 0
        assert options.resolved_surface_denoise_strength() == 0
    for parameter in ("raw_detail_strength", "surface_denoise_strength"):
        for style in ("natural", "phone-natural"):
            with pytest.raises(InputError, match="requires --style phone-clear"):
                RenderOptions(output=Path("out"), style=style, **{parameter:1}).validate()
        with pytest.raises(InputError, match="between 0 and 1"):
            RenderOptions(output=Path("out"), **{parameter:1.1}).validate()
