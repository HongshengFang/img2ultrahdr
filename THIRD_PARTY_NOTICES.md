# Third-party notices

## Optional JPEG HDR research pipeline / IntrinsicHDR

`src/hdrimg/jpeg_hdr/ai.py` adapts the inference computations of
[IntrinsicHDR](https://github.com/compphoto/IntrinsicHDR), by Sebastian Dille,
Chris Careaga and Yağız Aksoy (ECCV 2024). This adapter and its external
IntrinsicHDR dependency are **for academic use only**, under the upstream
[license reproduced here](docs/licenses/IntrinsicHDR.txt). The root MIT license
does not override these terms. Cite *Intrinsic Single-Image HDR Reconstruction*
when using the code or models. This optional pipeline is not an unrestricted
commercial-use feature of the MIT-licensed RAW application.

Model weights and the complete IntrinsicHDR source are downloaded from the
official repository into a local ignored runtime directory, not committed here.
The setup also downloads [SAM2](https://github.com/facebookresearch/sam2)
(Apache-2.0) and [libultrahdr](https://github.com/google/libultrahdr)
(Apache-2.0). The Windows codec and runtime packages come from MSYS2, with
their individual license files retained in the local `native/licenses/` directory.
No third-party models, binaries, or photographs are distributed in this change.

## Rec2020-elle-V4-g10.icc

The encoded ICC profile in `src/hdrimg/profiles/Rec2020-elle-V4-g10.icc.b64`
was created by Elle Stone and obtained from
<https://github.com/ellelstone/elles_icc_profiles>.

Copyright 2016 Elle Stone. Licensed under
[CC BY-SA 3.0 Unported](https://creativecommons.org/licenses/by-sa/3.0/).
The profile is redistributed unchanged; Base64 is only a transport encoding.
