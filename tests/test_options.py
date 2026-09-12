from pathlib import Path

import pytest

from hdrimg.errors import InputError
from hdrimg.pipeline import RenderOptions


@pytest.mark.parametrize(
    "field,value",
    [
        ("exposure_ev", 6),
        ("highlight_ev", -3),
        ("hdr_strength", 1.1),
        ("peak_nits", 300),
        ("tint", 0.1),
        ("contrast", 2.1),
        ("saturation", 0.7),
    ],
)
def test_option_ranges(field, value):
    values = {"output": Path("out"), field: value}
    with pytest.raises(InputError):
        RenderOptions(**values).validate()


def test_custom_white_balance_requires_temperature():
    with pytest.raises(InputError):
        RenderOptions(output=Path("out"), white_balance="custom").validate()
