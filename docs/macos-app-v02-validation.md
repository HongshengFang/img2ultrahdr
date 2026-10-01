# Img2UltraHDR 0.2 验收记录

日期：2026-09-30。本机为 Apple M1 Pro（8 性能核 + 2 能效核）、16 GB、macOS 26.7.1；使用 Command Line Tools 构建，不依赖完整 Xcode。冻结 Clear V8 R5 / Natural V8、203 nit 参考白和 1000 nit 输出峰值。

本记录区分自动检查、实际窗口呈现计时和人工 HDR 实屏判断。后者不能由截图或 GPU 数值替代。0.1 的历史记录、原应用及工作区证据保留在 `outputs/app-v02-validation/baseline/`，可用 `dist/Img2UltraHDR 0.1 Recovery.app` 运行冻结引擎。

## 实现与数值检查

| 项目 | 结果与证据 |
| --- | --- |
| Python 回归 | 217 项通过；`python-tests-final217.log` / 同名 XML |
| 原生编辑模型 | 13 项通过：撤销合并、旧结果丢弃、取消、导出锁定、记录恢复、无效参数、偏好独立以及导出文件两种显示模式的来源；`native-model-final/checks.json` |
| 原生工具与 GPU 合成检查 | 7 组通过：包版本、稳定帧标识、Retina/竖幅坐标、256 桶统计、输出边界、极端组合有限值，以及旧照片异步回调隔离；`native-tools-delivery/checks.json` |
| 冻结精确路径 | 38/38 SDR JPEG 与 HDR 半浮点结果逐字节一致；`equivalence-delivery/equivalence.json` |
| 预览行块并行 | 六张真实底稿 × 两种风格，零调整与组合调整分别 12/12 SDR/HDR 与串行结果一致；`parallel-shipping-zero/parallel-equivalence.json`、`parallel-shipping-final/parallel-equivalence.json`。Clear 使用 256 行 / 最多 4 线程；Natural 保留 512 行，未采用会改变半浮点舍入的实验分块 |
| 取消 | RAW 显影、完整渲染、旧 JPEG 编码、新浮点预览四阶段均结束任务进程组，无半成品缓存；`cancellation-unique/cancellation.json` |
| GPU 失效回退 | 强制禁用 GPU 后，精确照片仍能显示，明确停用实时统计和取色；`gpu-fallback/` |

证据目录均相对于 `outputs/app-v02-validation/`。部分可再生的半浮点临时文件及本次 RAW/应用/计时测试缓存已清理以控制磁盘占用；保留比较报告、代表帧、实际导出文件和清理清单。最终清理的缓存 JSON 清单另存各测试目录下 `cache-metadata/`，见 `delivery-cache-cleanup.json`。原片、历史参考输出和冻结证据没有作为临时缓存删除。

历史保护清单已重新计算 SHA-256：1361 项原片/历史文件及 2681 项冻结交付文件，共 **4042 项全部保持一致**，无缺失。证据：`historical-preservation.json`。

精确预览直接捕获原渲染器的 pre-JPEG 数值。Clear 在最终 8 位量化前捕获；Natural 保留其既有的内部量化顺序。完整导出继续使用原编码、验证和元数据路径。最后一项优化仅省去内部独立 SDR 分析参考未被使用的 HDR 副本；新的参考等价测试及 38 组冻结对照均通过。

## 实时与精确画质

六个代表场景：`0N6A9034`、`0N6A9169`、`0N6A9406`、`0N6A9416`、`0N6A9453`、`DSCF8111`。覆盖日光、暖光、逆光人物、亮衣、高亮背景、深阴影。对同一准备好的浮点输入运行 Clear / Natural 两套影调；这项比较控制了显影差异，不表示两套风格各重新显影一遍。

9 组配方 × 6 场景 × 2 风格 × SDR/HDR = **216/216 通过**。测试包含精确锚点、曝光 ±0.5 EV、高光 −0.5 EV、阴影 +0.5 EV、饱和度 +10%、HDR 强度 −20%、SDR 亮度 +0.5 EV 及组合调整。有效区域要求两幅图的线性亮度均大于 0.02；每组独立判断中位误差 ≤0.05 EV、95 分位 ≤0.2 EV，不能用总体平均掩盖失败场景。

- 最差场景中位误差：**0.01452 EV**；最差场景 95 分位：**0.18166 EV**。
- 精确锚点直接返回精确像素，开始拖动不会先套一次通用滤镜。
- CR2 / RAF 自定义白平衡分别真实重显影，对比 5600 K 起点的 ±1000 K、色调 ±10：**16/16 SDR/HDR 亮度检查通过**，最差中位数 0.02134 EV、95 分位 0.04391 EV。
- 白平衡仍存在可见颜色校正。最终实现的 SDR Lab D65 ΔE76 中位数约 0.71–3.44、95 分位约 1.89–6.13；此项是描述性指标，没有把亮度通过等同于颜色逐像素相同。

证据：`gpu-final/comparison.json`、`gpu-wb-calibrated/comparison.json`、`color-review/white-balance-color-shipping.json`。`color-review/combined-*.png` 的六个预览原尺寸区域已查看，人物发丝、肤色、亮衣、背景树叶及暗部未见明显新增轮廓；可以看出小幅影调差异。它们是 SDR 诊断图，不能证明 HDR 实屏亮感。

## 性能与显示

计时使用参数变化时钟和 `MTLDrawable.presentedTime`，包括 SwiftUI 到实际 drawable 呈现的路径；不是仅报告 GPU 提交耗时。测试由原生计时器以 30 Hz 改变实际模型参数，开启直方图及移动取色，包含全部实时滑块、自定义白平衡和 HDR/SDR 切换。没有测量物理鼠标硬件延迟。

交付应用隔离测试（35 秒，内建 HDR 显示器、窗口可见）取得 1040 个不同参数帧，约 **29.71 fps**，输入至呈现 95 分位 **51.27 ms**、帧间隔 95 分位 **41.67 ms**，均达到目标。证据：`presentation-scene-isolated/presentation.json`。原生组件窗口也达到目标（`presentation-paced/window-check.json`）。此后增加低频内存记录、旧照片回调隔离和窗口恢复重绘修正。最终包重测时系统报告窗口不可见，未返回有效 presentedTime（`presentation-delivery/presentation.json`）；不把该轮记为通过，也不把不可见窗口的 −1 数值解释为性能。51.27 ms 是此前可见窗口的实测证据，不是最终包重新取得的测量。

已缓存底稿的最终精确预览通过实际 JSONL 控制器测量，六场景各三次：请求到结果中位数 **4.272 秒**、95 分位 **5.338 秒**；其中引擎渲染/文件发布中位数 3.756 秒、95 分位 4.756 秒。包含原片指纹和子进程启动，不含原生纹理上传，不重新 RAW 显影。**3 秒中位数和 5 秒 95 分位目标均未达到，性能验收不能标为全部通过。** 证据：`benchmark-worker-delivery/benchmark.json`。

该轮停止了本次其他重测试，仍运行用户正常桌面环境，没有关闭其他应用或系统服务。准备好的浮点输入沿用既有六场景，测试报告同时记录原准备版本与当前引擎版本。此前引擎内计时为 3.494 / 4.328 秒（`benchmark-finish/benchmark.json`），此前控制器隔离计时为 3.615 / 4.782 秒（`benchmark-worker-isolated/benchmark.json`）；计时范围和负载不同，不能挑选其中最低数字称为最终通过。已经实施分块并行、数值加速、分析缓存和移除无用 HDR 副本；后续仍需降低精确处理和子进程开销。

HDR 实际 drawable 的半浮点采样包含大于 1 的有限值；原生层使用线性 Rec.2020、203 nit 光学刻度、1000 nit 峰值与 EDR 元数据。内建屏幕报告可用余量约 5–6 倍。该证据确认没有在显示前统一裁成 SDR，但不替代人眼判断颜色、亮度或系统显示适配是否符合预期。

## 窗口与本机交付

已安装 `~/Applications/Img2UltraHDR.app`；版本化副本为 `dist/Img2UltraHDR 0.2.app`。两者及 `dist/Img2UltraHDR 0.1 Recovery.app` 均通过本地签名验证。构建入口 `scripts/build_macos_app.sh`，中英文说明为 `docs/macos-app.md`、`docs/macos-app-en.md`。

六种语言/外观组合有窗口布局记录，含 900 × 620 最小尺寸。直方图和读数固定在右侧上方，参数区滚动；照片背景保持中性深灰。原生菜单、设置、状态和错误由应用翻译；系统文件对话框自己的文字仍遵循 macOS 语言。

应用依赖本机 `.venv` 与外部工具，构建脚本写入绝对路径、复制双语/Metal/Vision 资源，并完成本地签名验证。首次启动需要系统允许读取引擎和用户原片所在目录。诊断模式使用独立编辑记录与缓存，不替换日常编辑会话。

CR2 (`0N6A9406`) 和 RAF (`DSCF8111`) 均通过实际 `.app` 七步操作：导入、曝光/阴影、撤销、重做、取消全尺寸任务、恢复预览、完整 Ultra HDR 导出并验证。证据：`ui-cr2-shipping/state.json`、`ui-raf-shipping/state.json`。RAF 测试恰逢引擎优化更新，调整时观察到一次缓存失效和重新准备；稳定引擎下的 CR2 流程及缓存单元检查没有该情况。

导出显示的 HDR 与 SDR 均读取实际交付的 Ultra HDR JPEG，SDR 不使用编码前的独立 JPEG 冒充文件底图。后续的这项来源修正有原生模型检查；长时间预览测试使用的普通浮点显示通路未改变。

## 完整 RAW 与资源测试

30 张 CR2 + 7 张 RAF 共 **37/37** 完成默认 Clear 全尺寸导出，编码器识别为 Ultra HDR，SDR/HDR 两路均成功解码。精确 SDR 预览与缩小后的全尺寸 SDR 对比，最差照片中位亮度误差 **0.00874 EV**、95 分位 **0.03057 EV**。证据：`raw37/raw-suite.json`、`raw37/summary.json`。这不是 37 张各跑两种风格；两风格的受控数值对照见上文。

本轮全尺寸渲染和编码中位数约 65.7 秒（37.5–271.4 秒），准备、预览和完整处理合计中位数约 131.1 秒。测试期间有其他验证负载，耗时只作记录，不作为隔离性能目标。该长任务在最终预览分块和独立 SDR 参考优化前启动；最终实现由 38 组冻结数值对照及分块对照衔接验证。测试进程树 RSS 峰值约 **3.70 GiB**，峰值位于 RawTherapee 显影。证据：`raw37-memory.json`。

实际应用持续参数更新 **1800 秒**，没有 GPU 错误，最终直方图像素数一致。应用 RSS 峰值 **127.4 MiB**，Metal 分配从预热的 75.1 MiB 上升后稳定在约 **105.0–105.5 MiB**，未见持续累积历史帧。RSS 监视在应用开始后约 30 秒接入，覆盖其后约 1772 秒。证据：`stress30-shipping/presentation.json`、`stress30-shipping/gpu-memory-progress.json`、`stress30-shipping-memory.json`。

该长测与独立 RAW 回归并行，取得 5318 个实际呈现样本，95 分位延迟 164.7 ms、帧间隔 416.7 ms，**不满足帧率目标，不能称为连续 30 分钟稳定 30 fps**。它验证资源趋势；交互性能以单独的 35 秒可见窗口测量为准。最后的照片切换回调隔离和窗口恢复重绘修正通过原生检查，但未重新执行第二轮 30 分钟长测。

## 尚未完成的验收

- 精确更新的最终请求到结果计时仍高于 3/5 秒目标，已明确记录。
- 人工在内建 HDR 屏幕比较实时、精确与实际导出文件，切换显示器及系统亮度；自动化不能替代此项。
- 隐藏后恢复窗口的专用探针被系统报告为不可见，未取得可信的重新呈现时间，记录为 `blocked_by_window_visibility`（`window-restore/restore.json`）；需实际窗口切换复核。

因此本次交付为可运行的 0.2 功能版本，**P5 验收未全部关闭**。保留中间失败和并发负载下的记录，不把它们与隔离测试混用。0.1 恢复副本及历史输入、参考输出保留。
