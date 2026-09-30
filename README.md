# Img2UltraHDR

**Img2UltraHDR** is a free, open-source image-processing tool that converts camera RAW files and standard images into natural, ready-to-use SDR JPEGs and Ultra HDR JPEGs with gain maps. It automatically analyzes exposure, contrast, saturation, color gamut, and recoverable highlights while preserving full manual control and reproducible rendering records. It currently supports Canon CR2 and Fujifilm RAF files, with plans to expand support to JPEG, PNG, TIFF, HEIF, and other formats.

Img2UltraHDR 目前把 Canon CR2 或 Fujifilm RAF 显影为一张独立可用的 SDR JPEG，以及一张包含 HDR gain map 的 Ultra HDR JPEG。SDR 和 HDR 从同一份线性浮点底稿生成，白平衡与主体颜色一致；需要时可单独调整 SDR 亮度，HDR 曲线则利用显示器的额外亮度空间。

## 安装

目前支持 Apple Silicon macOS。核心显影、色彩处理和 Ultra HDR 编解码依赖为免费开源软件；默认 `phone-clear` 的可选人像局部调整还会调用 macOS 自带的 Apple Vision 框架。

```bash
brew bundle
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
img2uhdr doctor
```

主命令是 `img2uhdr`；同时保留 `hdrimg` 作为兼容别名。`doctor` 会检查 RawTherapee、libultrahdr、ExifTool、线性 Rec.2020 与 Display P3 profile 和临时目录，并完成一次小型 Ultra HDR 编解码。

全尺寸 RAW 会让 RawTherapee 和 libultrahdr 分配整帧缓冲。实测 30.2MP 文件在 libultrahdr 编码阶段约占 1.29 GB；建议运行前留出至少 3 GB 可用内存。Python 色彩与亮度渲染本身按 512 行分块处理。

## 使用

默认使用 `phone-clear` 手机自然明快风格。自动观感仍会根据每张照片的中央亮度、动态范围和颜色浓度分别决定曝光补偿、对比度和饱和度：

当前默认为 **`phone-clear` V8 R5**：在 RAW 阶段校正白平衡，以固定相机白平衡参考测光并修正室外误压暗；之后在浮点底稿中加强受光表面、浅色衣物与光源的 HDR 亮感，保留暗部深度、颜色和自然边缘。峰值仍为 1000 nit，合成局部调整保守限制为 ±0.5 EV。[V8 R5 实施与验证](docs/phone-clear-v8-r5.md)记录当前配方、取舍及用户观看反馈；[V7 实施与验收](docs/phone-clear-v7-r4.md)保留历史基准。[RAW 优先说明](docs/phone-clear-v6-raw-wb.md)记录处理范围、控制方式与验证方法；[V5 肤色保护说明](docs/phone-clear-v5-skin.md)和 [V4 恢复记录](docs/phone-clear-v4-checkpoint.json)保留历史依据。原始样本、`result/`、`result_natural/` 成片和备份、`outputs/` 实验结果只保留在本地，不随 Git 同步。

2026-09-29 的[十二张完整 RAW 复核](docs/phone-clear-raw-first-review.md)已重新覆盖 V6 生产流程，并保留细节前移候选与 RAW 白平衡专项对照。该次复核明确了环境光颜色和逆光曝光的改进方向，未改变当时的 V6 默认。Phone Natural V8 按用户确认冻结。

[Clear V7 首轮记录](docs/phone-clear-v7.md)保留了未通过验收的原因。[R4 最终实施与验收](docs/phone-clear-v7-r4.md)完成真实图像边缘、环境暖光、亮岩纹理和最终编码过渡修正；十二张同拍样片、37 张 Canon/Fuji RAW、自动边界检查与 Mac HDR 方向和过渡确认已完成，当时将 Clear 默认更新为 V7。十二张逐张复核记录为 9 张改善、3 张持平；这是实施方判断，用户确认的是实屏方向与自然过渡。Natural V8 保持冻结，原有 37 张成片校验值不变。

2026-09-30 的 V8 R5 完成十二张同拍样片与 37 张相机 RAW 全尺寸验证。用户确认相比 Natural 更有亮感和 HDR 突出感，未见黑边或边缘颜色缺失，并明确要求设为默认。此次只提升已验证的 R5 配方，没有追加新参数；[默认切换检查](docs/phone-clear-v8-promotion.json)单独记录输出等价性与 Natural 回归。原有紫边不纳入本轮修复范围。

```bash
img2uhdr render photo.CR2 --output outputs
img2uhdr render photo.RAF --output outputs
```

选择已确认的自然人像效果：

```bash
img2uhdr render photo.CR2 --style phone-natural --output outputs/phone-natural
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

手机风格参考 12 组同拍手机 DNG／Ultra HDR JPEG。**`phone-clear` 是默认版本**：SDR 会根据场景明暗调整底图，HDR 则独立区分普通中间调、受光表面与光源，并控制高光肩部和阴影深度；蓝色和暖色还会进行轻微的选择性校准。**Phone Natural**（`phone-natural`）已由视觉确认后的 V8 替换原版：沿用 RAW 优先白平衡，默认关闭会放大人物轮廓阴影的局部增强，加强脚踝等浅肤色区域的颜色保护，并保留自然的明暗与 HDR。详见 [phone-natural V8](docs/phone-natural-v8.md)。两者都会真正转换为 Display P3 并嵌入对应 ICC；只更改输出色域不会自动产生这种影调。需要原来的自然风格与 sRGB 输出时，显式使用 `--style natural`。

```bash
img2uhdr render pics/0N6A9479.CR2 \
  --output outputs/phone-clear \
  --exposure-ev 1.0 \
  --white-balance custom --temperature-k 5500 --tint 1.0 \
  --contrast 1.35 --saturation 1.18
```

这组固定参数用于和此前成片直接比较。`phone-clear` 保留 `--midtone-lift-ev 0.38`、`--highlight-rolloff 0.42`、`--local-contrast 0.22`、`--vibrance 0.23`、`--sdr-gamut display-p3`，并加入场景自适应 SDR/HDR 处理。新版 `phone-natural` 参数为 `0.38 / 0.42 / 0 / 0.23 / Display P3`，`algorithm_version` 为 8；关闭局部对比也会跳过宽范围显示细节恢复、局部直方图与去雾式材质反差，仍保留 RAW 细节与独立的人像影调。每一项都可单独覆盖，例如 `--vibrance 0.18` 或 `--sdr-gamut srgb`。两种风格的版本都写入 manifest：`phone-clear` 与 `phone-natural` 均为 8（各自独立的风格版本），分别见 [Clear V8 R5 说明](docs/phone-clear-v8-r5.md)和 [自然人像说明](docs/phone-natural-v8.md)；[v4 复核记录](docs/phone-clear-v4-validation.md)、[空间影调复核](docs/phone-clear-spatial-calibration.md)、[自适应校准记录](docs/phone-clear-adaptive-calibration.md)和[先前的校准记录](docs/phone-natural-calibration.md)保留作为历史基准。

`--exposure-ev` 是自动曝光之上的观感补偿；显式传入它会覆盖自动观感给出的补偿。在两种手机风格中，显式的正曝光还会给 RAW 最亮的一小部分保留额外 HDR 余量。`--contrast` 的范围是 `1.0..2.0`，`--saturation` 的范围是 `0.8..1.5`，显式传入时同样覆盖各自的自动结果。使用 `--no-auto-look` 可关闭自动观感，未指定项目会回到固定基线 `0 EV / 1.35 / 1.10`；`--no-auto-exposure` 则关闭曝光自动计算。

`--sdr-exposure-ev` 可在场景判断结果之上单独调节 SDR 亮度，范围为 `-2..+2 EV`。例如 `--exposure-ev 0.95 --sdr-exposure-ev -0.45` 可以让 SDR 的人脸和浅色衣服少一些发白，随后重新计算 gain map。`phone-clear` 与新版 `phone-natural` 提供三个独立控制：`--sdr-adaptation-strength 0..1`（默认 1，设为 0 可关闭新增的场景 SDR 调整）、`--hdr-midtone-gain 1..3`（默认按场景计算）和 `--hdr-shoulder-strength 0..1`（默认 0.7）。这些控制不改变 `natural` 的原有处理。

两种手机风格的自动处理都会先做适量 RAW 色彩降噪；只有平坦暗部的实测残留噪声足够高时，才增加亮度降噪并重新显影。`--raw-denoise-strength 0` 可关闭，`0..1` 可调强度。显式指定对比度或饱和度、或关闭自动观感时，默认不启用这一层；显式传入正强度可重新启用。显影决定和实际配置会记录在 manifest 中。

默认自动处理还会在降噪后加入适量反卷积锐化，并对结构平缓的蓝色区域做额外降噪。`--raw-detail-strength 0..1` 控制前者，`--surface-denoise-strength 0..1` 控制后者；设为 `0` 可分别关闭。锐化默认在自动观感和 RAW 降噪启用时生效，蓝色区域降噪默认跟随 RAW 降噪强度。蓝色区域由颜色、纹理与噪声估计产生，并非精确天空分割；细云纹可能略平滑。两项处理的实际决定也会记录在 manifest 中。平滑蓝色区域另从同参数、未降噪RAW参考中保留色度，以减少降噪可能引入的天空色带；仍使用降噪后的亮度，这会增加一次显影开销。参考处理也记入 manifest。

Clear V8 沿用 V7 的曝光场，将人物、背景和材质增强纳入同一曝光预算，并在最终 SDR/HDR 原尺寸图像中分别限制增益变化，帮助保留岩纹、织物和植被层次。人物整体调整自然延伸到轮廓；不可靠区域减少额外局部增强，不沿边缘补白、补黑或重新上色。`--local-contrast 0` 可关闭这部分局部反差；继续保留单独的 `--subject-adaptation-strength` 人像影调控制。

`--subject-adaptation-strength 0..1` 控制自动人像局部调整，默认 1。它使用本机 Apple Vision 检测脸部和人物区域，限制过亮的脸部与头发，并在符合条件的逆光场景调整浅色衣物。照片不上传；首次使用会在本地缓存编译辅助程序。检测暂时不可用时重试一次；同一独立参考图的成功结果在当前进程内有限复用，避免单独调SDR曝光时重复检测不一致。框架、编译器或检测持续不可用时自动回退，并记录原因；设为 0 可关闭。显式覆盖对比度或饱和度时不执行自动人像调整。

自动 HDR 使用独立于 `--sdr-exposure-ev` 的参考影调和色彩，避免单独调暗 SDR 时连带改变 HDR。峰值仍受 `--peak-nits` 限制。样张对照用于逐步接近手机观感，不代表复原手机的私有处理流程；DNG 校准脚本也不等于生产命令已开放 DNG 输入支持。

`--skin-protection-strength 0..1` 控制自动肤色保护，默认 1。手机风格在 RAW 自动白平衡显影后，用匹配降噪与锐化设置的相机白平衡参考，有限保留人物浅肤色的颜色；仅使用平滑颜色参考，维持主图亮度与细节。后续继续减少肤色区域的淡暖色去色；`phone-natural` V8 在 RAW 阶段扩大淡肤色的连续保护范围，后期独立使用人物覆盖率保护淡暖色，Clear V8 继续复用这套边缘保护，避免浅肤色置信度降为零后出现灰边，同时保持这一步的亮度与人物范围外的处理结果。人物身上的淡暖色衣物也可能保留更多颜色。明显偏橙的原始颜色仍允许较大的校正。人物区域来自本机 Apple Vision，肤色由连续颜色权重估计，并非精确皮肤分割；暖色衣物与皮肤相近时仍可能被部分保护。检测不可用时使用较弱的纯颜色保护，并记录原因。肤色保护用于自动观感下的非暗夜场景；显式覆盖对比度或饱和度、或关闭自动观感时不启用。SDR 与 HDR 共用同一底稿和人物判断。设为 0 关闭两处肤色保护，但继续使用 RAW 自动白平衡，不恢复 V4/V5 的整体去暖色。

`phone-clear` 与新版 `phone-natural` 默认采用 RAW 自动白平衡；`natural` 默认沿用相机白平衡。`--white-balance camera` 或 `custom` 会尊重明确指定的 RAW 白平衡，跳过相机参考肤色回退，后续也不会再做整体光源校正。Clear V8 的固定 RAW 测光证据独立于所选颜色白平衡；Auto 下仅在可信场景中将有限 TemperatureBias 写回显影引擎重新显影，Camera/Custom 不受该偏置覆盖。默认 RAW 肤色保护会增加一次参考显影的时间和临时磁盘开销。manifest 的 `raw_development.white_balance` 记录请求值、实际模式与肤色保护决定；自动估计的具体色温不由当前命令行工具返回，因此不会虚构数值。

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

- `photo_sdr.jpg`：默认 Display P3；`--style natural` 使用 sRGB。两者均为质量 95、4:4:4 的 SDR JPEG，带匹配的 ICC。
- `photo_ultrahdr.jpg`：SDR base + 彩色 gain map。
- `photo_render.json`：完整参数、工具版本、曝光统计和验证结果。

添加 `--keep-intermediates` 会保留 `photo_scene.tif` 和 `photo_hdr.rgba16f`。默认不覆盖文件；需要替换时明确使用 `--overwrite`。使用 `--strip-metadata` 可去掉拍摄元数据，但会保留正确显示所需的 ICC 信息。

## 画质检查

建议在 HDR 屏幕上分别用 Safari、Chrome 和 macOS 照片查看 Ultra HDR，同时单独检查 SDR JPEG。HDR 成片应检查中间调和肤色是否自然，以及阳光、灯光、反射与明亮云层是否获得合适的额外亮度。

真实 RAW 回归样片放在未跟踪的 `samples/` 目录。自动测试可通过以下命令运行：

```bash
pytest
```

八张真实样片的覆盖范围和验收状态记录在 [`docs/visual-acceptance.md`](docs/visual-acceptance.md)。没有相应样片或 HDR 屏幕复核时，该项会明确保留为“待验收”。

## 工作流程

1. RawTherapee 使用 Unclipped 配置和 gamma 1.0 Rec.2020 ICC 显影为 32-bit float TIFF。两种手机风格在此阶段自动校正白平衡，再从匹配的相机白平衡参考检查肤色损失。显影时预留 2 EV 高光余量，后续在线性曝光计算中精确补回，避免 ICC 输出阶段先把高光压到 1.0。
2. Python 先估算基础曝光，再从缩小预览测量中央亮度、亮部/暗部跨度和 OKLab 色度，自动决定观感曝光补偿、对比度和饱和度；每个决定及其测量依据都会写入 manifest。随后为 SDR 加入以 18% 灰为锚点的黑位和中段对比，并从同一底稿生成 sRGB 或 Display P3 SDR 和线性 Rec.2020 HDR rendition。手机风格的中间调、亮部、局部对比和选择性鲜艳度步骤也在这里执行。
3. 两种手机风格根据 RAW 场景分布调整 SDR 底图；HDR 中间调增益独立计算，在暗景中还参考 RAW 高光层次，让亮度主要留给真正的光源。实际峰值与增益图上限随场景变化；`--peak-nits` 是输出的物理亮度上限，而非每张照片的目标峰值。`natural` 沿用原来的影调曲线。
4. libultrahdr 从两份 rendition 计算彩色 gain map 并封装 Ultra HDR JPEG。
5. 每张成品都经过 probe、SDR decode 和线性 HDR decode；`inspect` 还会报告解码后是否真的存在超过 SDR 白的像素。

RawTherapee 和 libultrahdr 作为外部程序调用。本项目采用 MIT License。
项目携带的线性 Rec.2020 ICC 来自 Elle Stone，详情见 `THIRD_PARTY_NOTICES.md`。
