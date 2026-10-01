# JPEG → Ultra HDR research workflow

普通 JPEG 与 RAW 适合放在同一个项目：它们有共同的 Ultra HDR 文件格式，但起点不同。RAW 路径从相机线性数据显影并生成 SDR/HDR；JPEG 路径接受已完成影调和颜色处理的 SDR 图像，只预测额外的亮度增益。这里为 JPEG 增加独立命令与可选依赖。

**研究用途**：IntrinsicHDR 以及由其推理计算改编的 `src/hdrimg/jpeg_hdr/ai.py` 受学术用途许可限制。其条款不会被根目录的 MIT 许可替代。见 [third-party notices](../THIRD_PARTY_NOTICES.md) 和 [原许可](licenses/IntrinsicHDR.txt)。所有推理在本地运行，没有 API 费用。

## Windows 安装

已验证 Python 3.12、Windows、RTX 3070 Ti 8GB。先安装 Python 3.12、Git 和 NVIDIA 驱动，在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/jpeg_hdr/setup_windows.ps1
.\.venv-jpeg\Scripts\img2uhdr.exe jpeg-hdr photo.jpg --output outputs/photo_ultrahdr.jpg
```

脚本创建独立的 `.venv-jpeg` 环境，安装 CUDA 12.4 的 PyTorch 2.5.1 wheel，并把模型、第三方源码及 MSYS2 libultrahdr 1.5.1 放入 `.jpeg-hdr/`。这些目录均由 Git 忽略；不会上传权重、二进制或照片。无需安装完整 CUDA Toolkit。SAM2 不构建可选 CUDA 扩展，跳过洞/碎片后处理。

默认在当前目录寻找 `.jpeg-hdr`。从其他目录运行时，指定绝对路径：

```powershell
$env:IMG2UHDR_JPEG_HOME = 'D:\img2ultrahdr\.jpeg-hdr'
D:\img2ultrahdr\.venv-jpeg\Scripts\img2uhdr.exe jpeg-hdr photo.jpg --output result.jpg
```

`IMG2UHDR_LIBUHDR` 可指定兼容 C API 的 libultrahdr 动态库。库加载支持系统库查找，但本次只验证了 Windows CUDA JPEG 流程，未验证 macOS/MPS/Linux 的 AI 执行。原来的 `doctor` 继续检查 RAW/macOS 工具；JPEG 命令自行检查可选依赖。

可选依赖集合也在 `pyproject.toml` 的 `jpeg-hdr` extra 中，但 SAM2 源码、研究 checkpoint 和编解码库仍需单独准备。Windows 脚本给出经过测试的版本组合，不使用现有 macOS RAW 的 `requirements.lock`。

## 用法

```powershell
.\.venv-jpeg\Scripts\img2uhdr.exe jpeg-hdr photo.jpg --output outputs/photo_ultrahdr.jpg --ai-size 768 --max-ev 2.5 --strength 1.0
```

`--output` 在此命令中是 JPEG **文件名**。已存在的输出需要显式 `--overwrite`，输出不能与输入相同。

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `--ai-size` | 768 | AI 最长边；支持 384–1536，显存紧张时用 512 |
| `--max-ev` | 2.5 | 增益上限，实际增益不强制拉满 |
| `--strength` | 1 | 增益强度 0–3；0 为无增益对照 |
| `--look` | conservative | `phone` 在 HDR 中提升中间调和浅色材质；SDR base 不变 |
| `--peak-nits` | 1000 | `phone` 的设计峰值，支持 (203,4000]；按 203 nit SDR 参考白计算 headroom |
| `--protect` | 无 | SAM2 点/框保护区域 JSON |
| `--fp32` | 关闭 | FP32 推理对照 |

默认采用 FP16 autocast，倒数、拟合、分位数等运算保留 FP32。IntrinsicHDR 各阶段顺序加载、运行、释放，再运行 SAM2；全分辨率增益处理使用 CPU。解码验证成功后才写入 Ultra HDR 文件，旁边的 `*_diagnostics/` 包含 EV 数组、增益图、保护上限图、SDR/伪彩色增益对照与 JSON 运行记录。

### 更明显的手机 HDR 观感

```powershell
.\.venv-jpeg\Scripts\img2uhdr.exe jpeg-hdr photo.jpg --output outputs/photo_phone_ultrahdr.jpg --look phone --peak-nits 1000 --protect regions.json
```

单纯提升 0.7 EV，不一定让较暗的白丝袜超过 SDR 白色的亮度。`phone` 借用现有 RAW Phone Clear/Natural 共用的 `phone_hdr_luminance` 平滑材质曲线：一般中间调约 1.9 倍，随亮度连续提高浅色表面的增益，深暗部仍保留深度。JPEG 已丢失 RAW 测光信息，所以不复用 RAW 场景判定、白平衡、SDR 重塑或 V8 的完整配方；此模式不是 RAW Phone Clear 的等价结果。

在原图分辨率按亮度计算这条曲线，与 guided AI 增益及主体下限取最大值，避免叠加曝光。再应用区域上限与 RGB 峰值约束；单通道增益保留原 RGB 比例。没有保护区域时，花朵、亮墙等浅色物体也会得到提升，因此需要为想保留亮度的花束指定 SAM2 区域。主体较强下限可以用于丝袜，但脸部应单独设置较低上限。区域名称不触发自动语义识别。

设计峰值是创作参数，不是对屏幕发光亮度的测量。`HDRCapacityMax=peak_nits/203` 与 `MaxContentBoost=2**max_ev` 分开记录：前者描述完整 HDR 所需的显示余量，后者描述增益图编码范围。两者不必相等。显示设备会按 Google 规范利用可用余量适配；不能把 `--peak-nits 1000` 理解为任何屏幕上都达到 1000 nit。JPEG 量化也会使最终值轻微偏离编码前的上限。

## 保护区域

SAM2 提供交互式物体分割，不自动识别“脸/皮肤/天空”。需用点或框说明保护对象。坐标使用原 JPEG 的**存储像素坐标**，没有 EXIF 旋转；`name` 只是人读的标签，不是语义文本提示。可选 `min_ev` 为指定主体提供名义增益下限：例如白丝袜需要亮起来，而花继续只用很低的 `max_ev`。

```json
{
  "regions": [
    {"name": "face", "box": [1200, 600, 1600, 1100], "max_ev": 0.35},
    {"name": "person", "points": [[1800, 1900]], "labels": [1], "min_ev": 0.3, "max_ev": 0.5}
  ]
}
```

框格式为 `[x1,y1,x2,y2]`。点标签 1 表示包含、0 表示排除。选取 SAM2 得分最高的 mask，轻微膨胀后限制增益；原尺寸上限通过原图亮度引导的单向传播构造，再于所有正向增益之后应用。保护内部仍受原上限约束，邻近背景中的上限连续降低，避免把放大的二值掩膜轮廓写进 HDR。正向主体增益会填补小孔、羽化边缘，使用原图亮度引导放大，并减弱深暗部的增益；`min_ev` 因边缘羽化、暗部和邻近对象保护而不是严格逐像素下限，且跟随 `--strength`。重叠区域的保护上限优先于正向增益。JPEG 量化会产生小误差，保护上限不是编码后的绝对逐像素保证。没有提供 JSON 时仍有暗部/高光亮度门控，但没有 SAM2 对象保护。自动 face parsing 留待后续加入。

```powershell
.\.venv-jpeg\Scripts\img2uhdr.exe jpeg-hdr photo.jpg --output outputs/photo_ultrahdr.jpg --protect regions.json
```

## 数据与编码约定

输入限定为 RGB、sRGB JPEG，最长边不超过 8192。无 ICC 时按 sRGB 处理，参考库补入 sRGB ICC。非 sRGB ICC、CMYK、灰度或已有 Ultra HDR gain map 的输入会被拒绝。

AI 输入用标准逆 sRGB 线性化，未运行官方旧 TensorFlow SingleHDR 的去量化/相机响应估计。按中间调亮度比的中位数消除模型曝光歧义，再施加亮度门控和保护。最终只使用亮度比，预测的 RGB 不替换原图纹理或颜色。fast guided filter 以原图 log-luminance 引导恢复原尺寸 EV 场。

增益图满足 `gain_byte ≈ gain_EV / max_EV × 255`，gamma=1、offset=0、min boost=1、max boost=`2**max_EV`。libultrahdr C API 的 compressed-base + compressed-gainmap 模式直接封装原 JPEG 图像数据。新的整个文件当然包含额外元数据，但 SDR 像素及 JPEG 压缩扫描数据须逐一一致，EXIF 和已有 sRGB ICC 须保留。验证也实际解码线性 HDR，检查有限数值并记录重建误差。

libultrahdr 1.5.1 在所有分母相同时会写出使用保留位 `0x08` 的 ISO 元数据布局。它自己的解码器接受该布局，但 Chromium/Skia 的 ISO 21496-1 读取器无法解析，图片会回退为 SDR。封装后将这一旧布局展开成独立的分子/分母对，并同步更新 MPF 增益图长度；原 JPEG 扫描数据和增益图像素保持不变。新版库已使用独立分数，无需改写。整数 `max-ev=1` 已加入回归测试；不能仅凭参考库自身的解码成功判断浏览器兼容性。

输出同时包含 [Google Ultra HDR Image Format v1.1](https://developer.android.com/media/platform/hdr-image-format) 规定的 XMP 数据：主图的 `hdrgm:Version` 与 GContainer `Primary/GainMap` 目录，以及增益图的 log2 增益范围、gamma、两个 offset、HDR capacity 和 `BaseRenditionIsHDR=False`。1.5.1 的默认构建关闭 `UHDR_WRITE_XMP`，所以参考库生成并规范化 ISO 封装后，由 `xmp.py` 按文档和官方 `generateXmpForPrimaryImage/generateXmpForSecondaryImage` 的结构补充 XMP；XMP 已开启的参考库构建直接验证其现有包。MPF 两个图像长度同步更新，GContainer 中的增益图长度必须一致。检查必需字段、数值范围及 XMP 与实际解码参数的一致性。该路径使用 Google 规范的字段，不依赖私有 HDR 参数。

本次适配不是官方论文评测复现：混合精度、全像素尺度拟合、标准 sRGB 线性化、曝光锚定与 gain 后处理均有工程取舍。JPEG 中已经丢失的真实高光细节不能保证恢复。

## 验证记录（2026-09-30）

仓库命令在 RTX 3070 Ti 上对官方 `sunset_forest.jpg` 实测：1888×1280 输入/增益图，768×512 AI，SAM2 前景树干保护，约 5.3 秒处理，PyTorch 峰值 allocated 1237MiB / reserved 1388MiB。输出与集成前原型的全分辨率增益数值相同，SDR 像素、压缩扫描数据、EXIF 均通过。实际最大增益约 1.11EV。PyTorch 指标不包含桌面与驱动全部显存占用，耗时不包括进程启动和首次下载。

集成前还用同一照片重采样到 4000×3000 做了尺寸测试，约 7.7 秒、reserved 1494MiB，原尺寸 base 和 gain map 均验证通过。该样本有重采样和比例变化，只证明 12MP 尺寸可运行，不是原生 12MP 画质评测。尚未做用户真实照片或 HDR 屏幕观感验收。

测试命令：

```powershell
.\.venv-jpeg\Scripts\python.exe -m pip install pytest
.\.venv-jpeg\Scripts\python.exe -m pytest tests/test_jpeg_hdr.py tests/test_jpeg_hdr_cli.py tests/test_cli.py
```

涵盖：CLI 分流与依赖缺失提示，0/1.5/2.5EV 编解码、progressive JPEG、ICC、旋转 EXIF、重复 Ultra HDR 拒绝、guided filter 边缘和常量保持，以及可控预测下的整条流程和保护上限。模型/库缺失时，相关测试会跳过，不会伪装成 AI 实测通过。

本次上述针对性测试为 **11 passed**。在同一 Windows 环境运行完整非 integration 套件，集成分支为 188 passed / 25 failed / 5 skipped；干净主分支 `88d56f6` 为 184 passed / 相同 25 failed / 5 skipped。失败涉及既有 macOS Display P3 系统配置、POSIX 进程行为及 Windows 文件映射清理，未新增失败。本次未完成原生 macOS RAW/界面的实机回归。

固定来源：IntrinsicHDR `8f21f95c4369b8c7c39c6869dd2c484370744d4d`，SAM2 `2b90b9f5ceec907a1c18123530e92e794ad901a4`，官方 2.1 tiny checkpoint。下载 URL 与实际 SHA256 记录于本地 `downloads-manifest.json`。照片、权重和运行输出不进入仓库。

## 人物与花朵的对照测试

用户提供的 1024×1536 PNG 转为 quality=100、4:4:4 sRGB JPEG 后测试。纯 IntrinsicHDR + 亮度门控没有满足“白丝袜亮起来、花不被推白”的目标：实际解码的丝袜和脸部中位数增益接近 0EV。为此增加显式主体 `min_ev` 控制，与花朵 `max_ev` 保护共同作用，并保留原 SDR base。

一组研究参数为：人物 `min_ev=0.32/max_ev=0.9`、丝袜 `0.7/0.85`、脸部 `0.3/0.4`、花朵 `max_ev=0.03`、整体 `max-ev=1.0`。SAM2 点/框由样图指定，非自动语义识别。实际参考库解码的区域中位数增益：丝袜约 1.60 倍，脸部约 1.25 倍，花朵约 1.01 倍。花朵 mask 内 99% 分位约 0.031EV。主体下限与保护优先级新增测试后，针对性套件为 **12 passed**。

原图、坐标配置和输出仅在本地保留。该记录说明编码和区域增益达到了数值目标；HDR 屏幕上的主观观感仍待用户确认，不能视为已完成视觉验收。

后续浏览器检查定位到 1.5.1 的旧 ISO 元数据布局：Chrome 154 在 HDR 已启用的显示设备上渲染旧文件时，与 SDR 的截图像素完全相同。仅展开元数据并修正 MPF 长度（单通道增加 24 字节）后，Chrome 的原生 `<img>` 渲染出现人物提亮。参考库解码前后的 HDR 数值相同，SDR 压缩扫描和像素一致。检查使用独立 Chrome 窗口及默认浏览器功能；自动化工具默认强制 sRGB 的启动参数被移除，以允许读取真实显示设备能力。截图差异证明浏览器渲染行为改变，不用于测量屏幕实际发光亮度。修复后的针对性测试为 **15 passed**；主观效果仍待用户查看新文件确认。

补齐 Google v1.1 XMP 后，针对性套件仍为 **15 passed**，每个编解码样例同时验证 ISO+XMP 和隐藏 ISO 命名空间后的独立 XMP 解码。人物样图的两条解码路径得到相同参考 HDR 数值；Chrome 154 原生 `<img>` 的两种显示截图也逐像素一致。保留原 SDR 像素、扫描数据、ICC 和 EXIF。字段检查与独立解码属于本项目的验证，不表示 Google 或 ISO 提供了认证。

### 手机效果与用户参考图对照

读取用户另外提供的 Ultra HDR 原文件，参考图的丝袜中位数约 2.72 倍、脸部 2.14 倍、花朵 5.18 倍；上一版保护方案分别约 1.60、1.25、1.01 倍。前一版丝袜的 HDR 亮度中位数只有 SDR 白色的约 0.50 倍，解释了视觉变化较弱。

`phone` 模式与同图 SAM2 区域实测：人物 `min_ev=1.1/max_ev=2.1`，丝袜 `1.85/2.1`，脸部 `1.0/1.25`，花束与左侧补充花束区域 `max_ev=.03`；整体 `max-ev=2.5`、设计峰值 1000 nit。完整参考解码下，丝袜中位数约 3.50 倍、脸部 2.19 倍、主花束 1.02 倍；丝袜约 65% 的 mask 像素超过 SDR 白色。只在完整 HDR 余量条件下报告这些倍数，实际显示会随屏幕适配。样图为 1024×1536，AI 512×768 FP16，约 8 秒（不含进程启动），PyTorch 峰值 allocated 1237 MiB / reserved 1388 MiB。

新增测试验证曲线单调、深暗部、RGB 峰值、区域保护优先、strength=0、编码增益范围与显示余量分离及 XMP/ISO 独立解码；针对性套件 **18 passed**。这次本地 Chrome 154 检查中浏览器报告 HDR 能力为 false，图片加载成功，但未将本次 SDR 截图当作 HDR 实屏验证。最新成片仍需用户在可显示手机 HDR 照片的设备上确认。照片、参考原文件和区域坐标继续仅保存在本地。

### 花朵 HDR 轮廓修正

用户实屏反馈指出花束边缘出现粗糙锯齿。检查发现，guided AI 增益之后重新应用的原尺寸保护上限用了最近邻放大的低分辨率二值掩膜，直接将花束的约 0.03 EV 与邻近衣物的约 2 EV 切开。SDR base 一直不变，所以该缺陷只在 HDR 中明显；单独检查花束中位增益无法发现这个问题。

`edges.protection_envelope` 先双线性放大上限，再使用原图亮度建立单向的连续上限包络。相邻上限差限制为 `0.025 EV + 0.25 × abs(delta log2(source_Y))`，亮度引导下限为 0.01，以免深暗噪声放大边界预算。只降低上限，不提升保护内部；相似亮度表面连续过渡，真实强边缘允许更快变化。最终增益与该上限取最小值，不再恢复二值硬边。处理发生在 CPU，保留原图像素、花瓣纹理和低分辨率 FP16 AI 推理；每轴累计距离复用，临时工作按行分块，另需两张原尺寸 float64 距离场。

用户样图的固定花束轮廓上，筛选原图亮度差小于 0.25 EV 的 3393 对相邻像素，实际 JPEG 增益跳变的 99% 分位由 **2.265 EV 降至 0.088 EV**（最大值由 2.294 降至 0.137 EV）。参考 HDR 的对数亮度局部检查中，阶梯状灰边明显减弱；该 SDR 诊断图不是 HDR 预览，也不替代用户实屏验收。丝袜、脸部中位增益保持约 3.50、2.19 倍，主花束约 1.02 倍；SDR 像素、扫描数据、ICC/EXIF 与 ISO/XMP 解码一致性继续通过。

针对性测试 **21 passed**：新增低分辨率曲线轮廓、保护内部、真实源图强边缘、重叠保护及 quality=98 增益 JPEG 的编码后梯度检查。连续保护会降低花朵附近部分背景增益，以消除人为轮廓；不会锐化或模糊原照片，也不保证修复错误分割或 SDR 本来就模糊的细节。边缘修正版保持独立文件名，原版本留作对照。
