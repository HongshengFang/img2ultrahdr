# Img2UltraHDR

**Img2UltraHDR** is a free, open-source image-processing tool that converts camera RAW files and standard images into natural, ready-to-use SDR JPEGs and Ultra HDR JPEGs with gain maps. It automatically analyzes exposure, contrast, saturation, color gamut, and recoverable highlights while preserving full manual control and reproducible rendering records. It currently supports Canon CR2 and Fujifilm RAF files, with plans to expand support to JPEG, PNG, TIFF, HEIF, and other formats.

Img2UltraHDR 目前把 Canon CR2 或 Fujifilm RAF 显影为一张独立可用的 SDR JPEG，以及一张包含 HDR gain map 的 Ultra HDR JPEG。SDR 和 HDR 从同一份线性浮点底稿生成，因此白平衡、曝光和主体观感保持一致，HDR 主要扩展真实高光。

## 安装

目前支持 Apple Silicon macOS。所有依赖均为免费开源软件。

```bash
brew bundle
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
img2uhdr doctor
```

主命令是 `img2uhdr`；同时保留 `hdrimg` 作为兼容别名。`doctor` 会检查 RawTherapee、libultrahdr、ExifTool、线性 Rec.2020 profile 和临时目录，并完成一次小型 Ultra HDR 编解码。

全尺寸 RAW 会让 RawTherapee 和 libultrahdr 分配整帧缓冲。实测 30.2MP 文件在 libultrahdr 编码阶段约占 1.29 GB；建议运行前留出至少 3 GB 可用内存。Python 色彩与亮度渲染本身按 512 行分块处理。

## 使用

自然风格默认处理。默认开启自动观感，会根据每张照片的中央亮度、动态范围和颜色浓度分别决定曝光补偿、对比度和饱和度：

```bash
img2uhdr render photo.CR2 --output outputs
img2uhdr render photo.RAF --output outputs
```

自动结果不合审美时，可以只覆盖需要改变的项目，其余项目继续自动计算：

```bash
img2uhdr render *.CR2 \
  --output outputs \
  --exposure-ev 0.3 \
  --highlight-ev -0.5 \
  --hdr-strength 0.85 \
  --peak-nits 1000 \
  --contrast 1.55 \
  --saturation 1.15
```

如果暖色肤色与棕红色头发显得过于接近，可使用 `--warm-color-separation 1`，让亮部暖色略亮、偏金黄，暗部暖色略偏红。它是可选的观感控制，默认值为 `0`。

`--exposure-ev` 是自动曝光之上的观感补偿；显式传入它会覆盖自动观感给出的补偿。`--contrast` 的范围是 `1.0..2.0`，`--saturation` 的范围是 `0.8..1.5`，显式传入时同样覆盖各自的自动结果。使用 `--no-auto-look` 可关闭自动观感，未指定项目会回到固定基线 `0 EV / 1.35 / 1.10`；`--no-auto-exposure` 则关闭曝光自动计算。

自定义白平衡：

```bash
img2uhdr render photo.RAF \
  --output outputs \
  --white-balance custom \
  --temperature-k 5600 \
  --tint 1.0
```

检查成品是否包含可解码的 gain map：

```bash
img2uhdr inspect outputs/photo_ultrahdr.jpg
```

在 Mac 上直接用 Chrome 打开成品：

```bash
open -a "Google Chrome" outputs/photo_ultrahdr.jpg
```

聊天窗口、Markdown 预览和部分缩略图只显示 SDR fallback，不能用截图判断 HDR。`inspect` 中的 `visible_hdr_content` 应为 `true`，并且 `decoded_hdr_peak_luminance` 应大于 `1.0`；`1.0` 代表 203 nits 的 SDR 白。

每个输入默认输出：

- `photo_sdr.jpg`：sRGB、质量 95、4:4:4 的 SDR JPEG。
- `photo_ultrahdr.jpg`：SDR base + 彩色 gain map。
- `photo_render.json`：完整参数、工具版本、曝光统计和验证结果。

添加 `--keep-intermediates` 会保留 `photo_scene.tif` 和 `photo_hdr.rgba16f`。默认不覆盖文件；需要替换时明确使用 `--overwrite`。使用 `--strip-metadata` 可去掉拍摄元数据，但会保留正确显示所需的 ICC 信息。

## 画质检查

建议在 HDR 屏幕上分别用 Safari、Chrome 和 macOS 照片查看 Ultra HDR，同时单独检查 SDR JPEG。HDR 成片应保持中间调和肤色稳定，只让阳光、灯光、反射与明亮云层获得额外亮度。

真实 RAW 回归样片放在未跟踪的 `samples/` 目录。自动测试可通过以下命令运行：

```bash
pytest
```

八张真实样片的覆盖范围和验收状态记录在 [`docs/visual-acceptance.md`](docs/visual-acceptance.md)。没有相应样片或 HDR 屏幕复核时，该项会明确保留为“待验收”。

## 工作流程

1. RawTherapee 使用 Unclipped 配置和 gamma 1.0 Rec.2020 ICC 显影为 32-bit float TIFF。显影时预留 2 EV 高光余量，后续在线性曝光计算中精确补回，避免 ICC 输出阶段先把高光压到 1.0。
2. Python 先估算基础曝光，再从缩小预览测量中央亮度、亮部/暗部跨度和 OKLab 色度，自动决定观感曝光补偿、对比度和饱和度；每个决定及其测量依据都会写入 manifest。随后为 SDR 加入以 18% 灰为锚点的黑位和中段对比，并从同一底稿生成 sRGB SDR 和线性 Rec.2020 HDR rendition。
3. HDR rendition 只对最亮约 5% 的画面逐渐使用显示器 headroom；默认把 99.5 百分位高光放在约 440 nits，同时保持中间调接近 SDR。
4. libultrahdr 从两份 rendition 计算彩色 gain map 并封装 Ultra HDR JPEG。
5. 每张成品都经过 probe、SDR decode 和线性 HDR decode；`inspect` 还会报告解码后是否真的存在超过 SDR 白的像素。

RawTherapee 和 libultrahdr 作为外部程序调用。本项目采用 MIT License。
项目携带的线性 Rec.2020 ICC 来自 Elle Stone，详情见 `THIRD_PARTY_NOTICES.md`。
