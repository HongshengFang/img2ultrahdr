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

    @property
    def clear_v7(self) -> bool:
        return self.name == "phone-clear" and self.algorithm_version == 7

    @property
    def clear_v8(self) -> bool:
        return self.name == "phone-clear" and self.algorithm_version == 8

    @property
    def clear_float(self) -> bool:
        return self.clear_v7 or self.clear_v8

    @property
    def pale_skin(self) -> bool:
        return self.clear_float or (self.name == "phone-natural" and self.algorithm_version >= 7)

    @property
    def pale_boundaries(self) -> bool:
        return self.clear_float or (self.name == "phone-natural" and self.algorithm_version >= 8)

    def as_record(self) -> dict[str, str | float]:
        return asdict(self)


NATURAL_STYLE = StyleSettings("natural", 0.0, 0.0, 0.0, 0.0, "srgb")

STYLE_PRESETS = {
    "natural": NATURAL_STYLE,
    "phone-natural": StyleSettings("phone-natural", 0.38, 0.42, 0.0, 0.23, "display-p3", 8),
    "phone-clear": StyleSettings("phone-clear", 0.38, 0.42, 0.22, 0.23, "display-p3", 7),
}

RAW_PHONE_STYLES = frozenset({"phone-natural", "phone-clear"})

DEFAULT_STYLE_NAME = "phone-clear"
DEFAULT_STYLE = STYLE_PRESETS[DEFAULT_STYLE_NAME]

# Retained evaluation entry point for reproducing the accepted R4 recipe.
# The public Clear preset and this recipe must remain equivalent.
PHONE_CLEAR_CANDIDATE = StyleSettings("phone-clear", .38, .42, .22, .23, "display-p3", 7)


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

# Explicit experimental entry; never selected by public/default presets.
PHONE_CLEAR_V8_EXPERIMENT = StyleSettings("phone-clear", .38, .42, .22, .23, "display-p3", 8)
