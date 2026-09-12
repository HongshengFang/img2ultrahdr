from importlib.resources import files
from pathlib import Path
import base64


def test_raw_profile_preserves_unclipped_linear_rec2020():
    profile = Path(str(files("hdrimg").joinpath("profiles/raw-unclipped.pp3")))
    text = profile.read_text(encoding="utf-8")
    assert "ClampOOG=false" in text
    assert "WorkingProfile=Rec2020" in text
    assert "WorkingTRC=none" in text
    assert "OutputProfile=Rec2020-elle-V4-g10" in text
    assert "Method=amaze" in text
    assert "Method=3-pass (best)" in text
    assert "CMethod=AUT" in text
    assert "LcMode=lfauto" in text


def test_packaged_linear_icc_is_valid():
    path = Path(
        str(files("hdrimg").joinpath("profiles/Rec2020-elle-V4-g10.icc.b64"))
    )
    data = base64.b64decode(path.read_text(encoding="ascii"))
    assert len(data) >= 128
    assert data[36:40] == b"acsp"
