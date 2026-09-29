from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StyleSettings:
    name: str
    midtone_lift_ev: float
    highlight_rolloff: float
    local_contrast: float
    vibrance: float
    sdr_gamut: str
    algorithm_version: int = 1

    def as_record(self) -> dict[str, str | float]:
        return asdict(self)


NATURAL_STYLE = StyleSettings("natural", 0.0, 0.0, 0.0, 0.0, "srgb")

STYLE_PRESETS = {
    "natural": NATURAL_STYLE,
    "phone-natural": StyleSettings("phone-natural", 0.30, 0.35, 0.12, 0.15, "display-p3"),
    "phone-clear": StyleSettings("phone-clear", 0.38, 0.42, 0.22, 0.23, "display-p3", 6),
}

DEFAULT_STYLE_NAME = "phone-clear"
DEFAULT_STYLE = STYLE_PRESETS[DEFAULT_STYLE_NAME]


def resolve_style(
    name: str,
    *,
    midtone_lift_ev: float | None,
    highlight_rolloff: float | None,
    local_contrast: float | None,
    vibrance: float | None,
    sdr_gamut: str | None,
) -> StyleSettings:
    try:
        preset = STYLE_PRESETS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown style: {name}") from exc
    return StyleSettings(
        name=name,
        midtone_lift_ev=preset.midtone_lift_ev if midtone_lift_ev is None else midtone_lift_ev,
        highlight_rolloff=preset.highlight_rolloff if highlight_rolloff is None else highlight_rolloff,
        local_contrast=preset.local_contrast if local_contrast is None else local_contrast,
        vibrance=preset.vibrance if vibrance is None else vibrance,
        sdr_gamut=preset.sdr_gamut if sdr_gamut is None else sdr_gamut,
        algorithm_version=preset.algorithm_version,
    )
