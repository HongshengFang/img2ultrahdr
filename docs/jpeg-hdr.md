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
| `--protect` | 无 | SAM2 点/框保护区域 JSON |
| `--fp32` | 关闭 | FP32 推理对照 |

默认采用 FP16 autocast，倒数、拟合、分位数等运算保留 FP32。IntrinsicHDR 各阶段顺序加载、运行、释放，再运行 SAM2；全分辨率增益处理使用 CPU。解码验证成功后才写入 Ultra HDR 文件，旁边的 `*_diagnostics/` 包含 EV 数组、增益图、保护上限图、SDR/伪彩色增益对照与 JSON 运行记录。

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

框格式为 `[x1,y1,x2,y2]`。点标签 1 表示包含、0 表示排除。选取 SAM2 得分最高的 mask，轻微膨胀后限制增益，并在 guided 放大后重新应用上限。正向主体增益会填补小孔、羽化边缘，使用原图亮度引导放大，并减弱深暗部的增益；`min_ev` 因边缘羽化和暗部保护而不是严格逐像素下限，且跟随 `--strength`。重叠区域的保护上限优先于正向增益。JPEG 量化会产生小误差，保护上限不是编码后的绝对逐像素保证。没有提供 JSON 时仍有暗部/高光亮度门控，但没有 SAM2 对象保护。自动 face parsing 留待后续加入。

```powershell
.\.venv-jpeg\Scripts\img2uhdr.exe jpeg-hdr photo.jpg --output outputs/photo_ultrahdr.jpg --protect regions.json
```

## 数据与编码约定

输入限定为 RGB、sRGB JPEG，最长边不超过 8192。无 ICC 时按 sRGB 处理，参考库补入 sRGB ICC。非 sRGB ICC、CMYK、灰度或已有 Ultra HDR gain map 的输入会被拒绝。

AI 输入用标准逆 sRGB 线性化，未运行官方旧 TensorFlow SingleHDR 的去量化/相机响应估计。按中间调亮度比的中位数消除模型曝光歧义，再施加亮度门控和保护。最终只使用亮度比，预测的 RGB 不替换原图纹理或颜色。fast guided filter 以原图 log-luminance 引导恢复原尺寸 EV 场。

增益图满足 `gain_byte ≈ gain_EV / max_EV × 255`，gamma=1、offset=0、min boost=1、max boost=`2**max_EV`。libultrahdr C API 的 compressed-base + compressed-gainmap 模式直接封装原 JPEG 图像数据。新的整个文件当然包含额外元数据，但 SDR 像素及 JPEG 压缩扫描数据须逐一一致，EXIF 和已有 sRGB ICC 须保留。验证也实际解码线性 HDR，检查有限数值并记录重建误差。

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
