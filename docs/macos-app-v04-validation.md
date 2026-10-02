# Img2UltraHDR 0.4.0 实施与验证记录

验证日期：2026-10-01。基线为 Git `bf57fe8`（0.3），实现位于
`codex/local-lighting`。本机候选应用安装在
`~/Applications/Img2UltraHDR.app`，版本 **0.4.0 / build 2**（启动修复；此前局部调光验收使用 build 1）。
Python 包版本同步为 0.4.0；最低系统保持 macOS 15。
验收机器为 M1 Pro / 16 GB，实际运行系统为 macOS 26.7.1。

## 已实现的操作

- 基础明暗之后提供通用“局部调光”，最多保存 8 个独立区域。
  点选、提亮／压暗、力度、独立开关、删除、拖动和撤销重做均已接入。
- 新区域从零开始，首次选择方向设为 50% / ±0.25 EV；切换方向保留
  已设置的力度，主动归零后不会再次被方向按钮恢复到 50%。
- 柔和圆形／椭圆支持位置、5–200% 大小、1:4–4:1 长宽比和 ±180°
  旋转；中心限制在照片内，范围允许超出边缘。空格拖动平移。
- Vision 选择点击位置对应的前景实例，包括非人物物体；背景、失败和
  8 秒超时明确回退到柔和范围，等待时也可主动使用柔和范围。
- 按住局部比较保留当前全局参数；“初始效果”继续表示风格默认效果。
  选区覆盖层不进入直方图、取色和导出。
- 选区随原片内容指纹保存，全局曝光、风格和白平衡变化不重新识别。
  16 位灰度选区独立保存在 Application Support，清理运行缓存不会删除它。
  缺失／损坏的有效选区阻止导出，允许关闭、删除、改为柔和范围或重新选择。
  智能模式提供“重新选择范围”按钮；重新选择同一物体也会校验并修复其损坏资产。
- 中英文界面、帮助、README 和本机说明已更新。应用先显示窗口，再启动
  引擎，避免系统访问授权等待期间没有主窗口。

## 数值与协议

手动局部阶段位于既有风格、肤色和自动局部处理之后，JPEG 编码之前。
所有区域按稳定 ID 合成曝光场，限制在 ±0.5 EV，再一次处理像素。
压暗共享 RGB 增益；提亮按 SDR / HDR 各自的亮部余量保护。
Natural 使用其已接受的预 JPEG 数值建立浮点输入；没有有效局部调整时
两种风格都保留原路径。局部参数不参与 RAW 测光、场景分析或显影缓存键。

智能辅助使用 Apple 的 [`VNGenerateForegroundInstanceMaskRequest`](https://developer.apple.com/documentation/vision/vngenerateforegroundinstancemaskrequest)，
该接口生成前景对象实例范围，系统可用性从 macOS 14 开始；本应用继续要求 macOS 15。

配方版本为 2，旧记录迁移为空局部列表。JSONL 协议仍为 2，新增
`select_region`、`local_adjustments_v1` 和 `float_preview_v2`，兼容原浮点包。
v2 提供不含手动局部效果的 SDR/HDR 精确基准帧及选区纹理。
Metal 从基准帧计算全局实时近似，再应用当前局部参数，避免重复叠加。
不同尺寸的智能选区纹理也已覆盖 CPU / Metal 对照。

## 自动验证结果

结果保存在项目的 `outputs/app-v04-validation/`，其大文件不进入 Git。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 完整 Python 回归 | 253 项通过 | `pytest-final-r4.log` |
| 原生模型与鼠标事件 | 23 项通过；包含 8 区域恢复、撤销、旋转、平移及异常资产 | `native-final-r11/checks.json` |
| 冻结底稿的无局部回归 | 19 张底稿 × 2 风格，38/38 SDR JPEG 和 HDR 编码输入逐字节相同 | `equivalence/equivalence.json` |
| CPU / Metal 对照 | 168/168 帧通过：144 帧局部变化，24 帧全局与局部组合 | `local-gpu-final/comparison.json` |
| 不同尺寸选区纹理 | 6/6 帧通过 | `mixed-mask-sizes/comparison.log` |
| 真实 Vision 选择与保存 | 37 张现有 RAW 的参考图，37/37 标签命中点采用智能选区 | `selection-37-final/selection.json` |
| 实际 JSONL 完整链路 | 11 项通过，包含 8 区域恢复、选择不覆盖配方、连续取消、损坏阻止发布及恢复 | `worker-local/checks.json` |
| 原尺寸导出与解码 | 12 张 CR2 / RAF × 2 风格，24/24 Ultra HDR 验证通过；保留 48 张 HDR / SDR JPEG | `full-exports/exports.json` |
| 无人物照片 | 岩壁、暗景建筑、天空山谷，2 风格，共 6 次编码／解码；非人物物体选择及背景回退通过 | `scenery/scenery.json` |

局部单独变化的最坏 95 分位误差为 **0.000725 EV**，最坏最大误差为
**0.001409 EV**，低于目标 0.01 / 0.05 EV。近黑采用绝对误差，最坏值为
`6.104e-5`。全局与局部组合仍使用既有实时近似标准，不能把它的误差
宣称为精确局部阶段的误差。未作用区域的逐像素一致性在编码前检查，
JPEG / gain map 解码误差另行检查。

基础测试还覆盖纯黑、接近白、饱和色、高 HDR 亮部、零力度、正负抵消、
区域重排、重叠预算、有限数值、SDR 亮度不改变 HDR、旧配方、资产校验和
缓存缺失。原生检查使用真实 AppKit 鼠标事件验证左上角坐标、移动、
边界调整、完整负角度旋转及空间平移；画布矩形隔离缩放和平移坐标。

## 样片来源和画面检查

37 张选择测试使用已接受的 V8 R5 SDR 参考，先实际运行 Vision，再选取
标签图中的命中点。37/37 表示这些命中点通过，**不表示任意点击都可识别**。
这批参考的冷分析中位数为 0.43 秒，最长 6.13 秒；不含首次 RAW 显影。
真实 worker 的选区测试使用当前全局下、不含手动局部调整的精确基准帧。
失败和超时另外通过受控故障测试，用户界面也有独立的 8 秒期限。

原尺寸样片为 `0N6A9034`、`0N6A9169`、`0N6A9406`、`0N6A9416`、
`0N6A9453`、`0N6A9476`、`0N6A9479`、`0N6A9486`、`0N6A9507`、
`DSCF8111`、`DSCF8114`、`DSCF8129`。前 6 张复用已接受的完整 RAW
底稿，后 6 张重新显影；各次来源在 JSON 中逐项记录。
原片内容校验全部通过，旧缓存、原片和此前验收成片未作清理。

现有 37 张 CR2 / RAF 主要是人物照片。补充的三张无人物场景来自已有
Pixel DNG 派生的 1536 像素底稿，用于实际 Vision、调光和 Ultra HDR 编码
验证；**不构成应用新增 DNG 输入支持，也不构成无人物 RAW 原尺寸验收**。

SDR 整图、原尺寸头发、肤色、白色饰物和织物对照保存在
`visual-review/`。当前已查看的代表画面未见明显新增光晕、灰边或黑边。
普通截图和 SDR 对照无法确认 HDR 实屏亮度及完整主观画质。
三张无人物照片的两种风格对照、24 组人物 HDR / SDR 成片均已保留。

## 性能与运行限制

性能使用 30 Hz 原生参数更新，开启完整图直方图和移动取色，8 个区域
同时启用（2 个智能选区、6 个柔和范围）。依次改变力度、位置和旋转，
并切换 HDR / SDR。呈现通过 `MTLDrawable.presentedTime` 记录，
不代表硬件鼠标事件端到端延迟。

调整面板已减少全局控件、列表行和选区方式控件的重复布局；折叠的
“范围”按需创建数值控件。覆盖层在力度变化时不重复绘制静态轮廓。
呈现回调立即记录时间，避免主队列稍后访问已被回收的 drawable。

此前长测出现窗口变为不可见／呈现时间为零，相关整段 FPS 不用于验收。
`window-awake/visibility_history` 记录了窗口约在启动后 46.44 秒变为不可见；
其 120 秒测量不能直接宣称通过持续可见窗口验收。
这次窗口可见的约 38.44 秒编辑区间呈现 1111 帧，约 28.90 fps，
95 分位延迟 42.27 ms。120 秒渲染期间进程峰值约 209.8 MiB，
GPU 资源在 HDR / SDR 纹理建立后稳定；这不替代长期切图内存检查。
安装版最终独立的 **35 秒**测试全程可见，呈现 **1038 帧 / 29.66 fps**，
更新至呈现的 95 分位为 **43.47 ms**。8 区域同时启用，直方图计数
与 1572864 像素完全一致，GPU 无错误。采用临时目录内的生成浮点测试帧，
不需要访问“文稿”；这是已安装应用的真实界面和 Metal 呈现测试，
不表示原片导入授权已完成。证据：`installed-local-window-temp/presentation.json`。
还修复了无恢复窗口时的首次启动：使用 SwiftUI 的编辑窗口入口明确开窗，
并在窗口出现后启动引擎；正常启动与诊断启动共用该入口。

同一安装版继续完成 **120 秒**独立测量：全程可见，呈现 **3224 帧 /
26.87 fps**，95 分位延迟 **52.91 ms**，直方图计数正确且无 GPU 错误。
进程峰值内存四次采样均为 **186.125 MiB**；GPU 分配在首次纹理建立后
由 178.34 MiB 降至 172.41 MiB，未出现持续增长。
证据：`installed-local-window-long/presentation.json` 和
`gpu-memory-progress.json`。这覆盖持续编辑和 HDR / SDR 切换，
不代表已经完成大量原片连续切换的长期测试。

补齐“重新选择范围”、隐藏过长的英文选区方式标签及跨控件空格平移后，
最终构建另测 35 秒：**1039 帧 / 29.69 fps**，95 分位 **40.69 ms**，
全程可见且 GPU 无错误。证据：`installed-local-window-final/presentation.json`。

完整回归曾因可用磁盘小于既有 4 GB RAW 准备门槛而出现两项失败。
仅清理本轮可重新生成的浮点缓冲、分析缓存后，最终 253 项通过。
计量 JSON、日志、持久选区和成片保留；重复脚本会重建删去的临时缓冲。

本机构建和代码签名验证通过。重新签名使 macOS 的旧“文稿”访问授权
不再匹配；系统日志明确出现 `kTCCServiceSystemPolicyDocumentsFolder`
授权提示。安装版读取项目 / RAW 的运行检查需要所有者在系统提示中
允许访问。该系统权限只能由所有者授予。
最后一次正常启动的诊断确认 0.4.0 主窗口已显示；引擎就绪记录尚未生成，
因此仍按等待系统授权记录。中英文局部面板截图在 `native-final-r11/`。

## 尚未关闭的发布验收

2026-10-01 启动卡住诊断：安装版的 Python 进程栈停在解释器初始化读取运行配置的 `open`，尚未进入 worker；同一环境从开发终端运行 doctor，8 项检查全部通过，约 3.62 秒。源码增加启动 / 工具检查状态区分、10 秒权限提醒及 60 秒超时；停止启动进程时，SIGTERM 无效后仅对原进程升级 SIGKILL。原生检查更新为 26 项全部通过，证据在 `outputs/startup-fix-native/checks.json`；双语资源校验通过。此轮先保留原安装版，随后在重试仍卡住后实施下述运行环境修复。


启动修复（build 2）已安装至原来的 `~/Applications/Img2UltraHDR.app`。构建时把引擎源码、Python 可执行文件及 Python 包复制进应用，移除 editable 路径，保留本机 Python 标准库与外部编解码工具依赖。禁止在签名包内写入 Python 字节码。通过 Launch Services 启动候选包约 5.34 秒就绪，实际安装包约 6.62 秒就绪；候选包从上一张 `_DSF7018.RAF` 的临时副本生成 1024 × 1536 精确预览。启动和预览后的严格签名校验通过，27 项原生检查通过；证据在 `outputs/packaged-startup-validation/` 与 `outputs/packaged-startup-native-final/`。此前记录的安装版启动等待项已解决；HDR 实屏及其他尚未完成的验收仍按下文记录。

这是已安装的本机 0.4.0 候选版本。正式宣称全部发布条件通过之前，
仍需完成 HDR 实屏人工查看、无人物 CR2 / RAF 原尺寸代表样片及长期切图
内存观察；安装版文稿授权后的完整运行检查也应留有结果。
本次没有推送、创建远程发行包或公开发布。

## 重复执行

```bash
.venv/bin/python -m pytest
scripts/build_macos_app.sh
swiftc -O -D EDITOR_MODEL_CHECK macos/Sources/Img2UltraHDR/*.swift \
  scripts/check_macos_editor.swift -o /tmp/check-local-editor
HDRIMG_RESOURCES="$PWD/macos/Sources/Img2UltraHDR/Resources" \
  /tmp/check-local-editor outputs/new-local-native
.venv/bin/python scripts/validate_local_previews.py --help
.venv/bin/python scripts/validate_local_gpu.py --help
.venv/bin/python scripts/validate_local_selection.py --help
.venv/bin/python scripts/validate_local_worker.py --help
.venv/bin/python scripts/validate_local_exports.py --help
.venv/bin/python scripts/validate_local_scenery.py --help
```

GPU 对照使用 `scripts/check_local_preview.swift` 和
`scripts/compare_local_previews.py`。性能检查可用
`scripts/check_preview_window.swift --local`，或安装版的
`--preview-benchmark <cases.json> --benchmark-local --benchmark-seconds 35
--diagnostics <directory>`。可见窗口测试应单独运行，并检查可见性历史、
直方图像素计数、GPU 错误和实际呈现数据；不能只使用提交给 GPU 的帧数。
