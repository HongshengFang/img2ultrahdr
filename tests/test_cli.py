from pathlib import Path
from types import SimpleNamespace

from hdrimg import cli
from hdrimg.errors import ProcessingError


def test_render_defaults_to_auto_look_and_accepts_manual_overrides():
    automatic = cli._parser().parse_args(["render", "photo.RAF"])
    assert automatic.auto_look is True
    assert automatic.exposure_ev is None
    assert automatic.sdr_exposure_ev == 0.0
    assert automatic.contrast is None
    assert automatic.saturation is None
    assert automatic.warm_color_separation == 0.0
    assert automatic.style == "phone-clear"

    manual = cli._parser().parse_args(
        [
            "render",
            "photo.RAF",
            "--exposure-ev",
            "0.2",
            "--contrast",
            "1.6",
            "--saturation",
            "1.15",
            "--warm-color-separation",
            "0.8",
        ]
    )
    assert manual.exposure_ev == 0.2
    assert manual.contrast == 1.6
    assert manual.saturation == 1.15
    assert manual.warm_color_separation == 0.8

    legacy = cli._parser().parse_args(["render", "photo.RAF", "--style", "natural"])
    assert legacy.style == "natural"


def test_batch_continues_after_one_failure(monkeypatch, tmp_path: Path):
    calls = []

    def fake_render(source, options):
        calls.append(source)
        if source.suffix.lower() == ".cr2":
            raise ProcessingError("broken fixture")
        return SimpleNamespace(
            sdr=tmp_path / "ok_sdr.jpg",
            ultrahdr=tmp_path / "ok_ultrahdr.jpg",
            manifest=tmp_path / "ok_render.json",
        )

    monkeypatch.setattr(cli, "render_raw", fake_render)
    result = cli.main(
        ["render", "broken.CR2", "working.RAF", "--output", str(tmp_path)]
    )
    assert result == 4
    assert calls == [Path("broken.CR2"), Path("working.RAF")]


def test_duplicate_stems_are_rejected(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(cli, "render_raw", lambda *_: None)
    result = cli.main(
        ["render", "first/photo.CR2", "second/photo.RAF", "--output", str(tmp_path)]
    )
    assert result == 2
