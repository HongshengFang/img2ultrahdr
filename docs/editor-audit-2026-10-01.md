# 编辑器检查与修复 · 2026-10-01

本轮覆盖 SwiftUI 控件、编辑状态与取消、Python worker 协议、预览缓存、曝光与影调、Metal 实时预览，以及 CR2 / RAF 原尺寸导出。修复已安装到 `~/Applications/Img2UltraHDR.app`，版本 0.4.0、build 3。安装后恢复了 `_DSF6848.RAF` 和更新前的全部编辑参数，收到 1024 × 1536 精确预览；引擎源码与工作区一致，预览后的严格签名校验通过。

## 修复

- **拖动时数值停在零。** 原生 NSHostingView 复现：模型的曝光、高光、阴影已变为 0.7 / −0.6 / 0.8，五个基础调整的输入框仍显示初始零值。`AdjustmentControl` 仅保存模型引用，SwiftUI 无法从子视图输入判断数值已改变。现在传入数值快照作为视图依赖，并保留读取实时模型的双向 Binding，兼顾刷新和手动输入。实际 AppKit 输入框验证了拖动、松手、撤销、重做、单项重置、局部调整及键盘输入。
- **深度压低高光造成亮度顺序反转。** 旧 −2 EV 曲线在 20,000 点的 0.001～2 亮度渐变中出现 4,481 次下降。现在保留 −1 EV 及以上的原处理，更低值叠加以灰位为锚点的单调肩部。Python、Swift 参数计算和 Metal 同步修改。默认零值与中等高光调整保持原结果；保存记录中低于 −1 EV 的高光调整会采用修复后的曲线。
- **损坏预览缓存被当作完成结果。** 统一缓存读取，检查 JSON、图像尺寸、显示帧、局部 mask 和全尺寸 HDR 缓冲长度；缺失或截断时重新计算，避免返回无法显示的完成记录。完整缓存仍可在 RAW 底稿已被淘汰时直接复用。
- **异常请求使后台退出或带上旧请求身份。** 每条消息独立初始化解析状态，拒绝非对象请求，区分参数错误和停止旧任务失败。首条坏 JSON、数组、null 及后续坏消息均不会终止 controller。
- **选择任务在后续操作中遗留。** 切图、全局编辑、重置和新的处理请求会清除不再适用的选区等待与超时；过期结果不能重新插入已取消区域。保留等待选区完成前的导出保护。
- **边界输入。** 单行、单列场景测光不再产生空中心区域；没有有效亮度样本的 packet 使用有限的零分位数。

清理了未使用的导入、重复停止任务分支和重复全局控件构造；缓存完整性判断集中到一个函数。没有清除原片、成片、用户编辑或历史实验资料。工作区原有的未提交修改继续保留。

## 单位与默认亮度

曝光保留 ±3 EV；高光、阴影、白色色阶、黑色色阶统一显示 −100～+100 的无单位调节量。这些范围调节仍使用原先保存的权重参数；改显示单位不会重新解释已有数值。0 表示保留自动效果，并非关闭自动曝光或 HDR 风格。

同一份保留的 RAW 浮点底稿用于比较 Clear / Natural 及手动强度，排除重新显影时的白平衡和识别差异。下面的 nit 是图像内容相对 203 nit 参考白的换算，不是屏幕实测亮度。

| 底稿 | 风格 / HDR 强度 | 亮度中位数 | 亮度 P90 |
| --- | --- | ---: | ---: |
| `_DSF6848.RAF` | Clear / 100% | 122 nit | 368 nit |
| `_DSF6848.RAF` | Clear / 80% | 102 nit | 309 nit |
| `_DSF6848.RAF` | Natural / 100% | 100 nit | 290 nit |
| `_DSF7018.RAF` | Clear / 100% | 181 nit | 520 nit |
| `_DSF7018.RAF` | Clear / 80% | 150 nit | 438 nit |
| `_DSF7018.RAF` | Natural / 100% | 137 nit | 429 nit |

两张底稿的 16 组处理全部有限、非负且位于 SDR/HDR 输出范围内，没有像素碰到 HDR 通道上限的 99.9%。这些结果支持 Clear 的 HDR 提亮会带来偏亮观感；它们不能证明 RAW 传感器是否发生剪切，也不能替代 HDR 屏幕的人眼判断。两种风格在相同底稿上的默认零值 SDR JPEG 和 HDR 半精度缓冲，与更新前安装包逐字节一致。本轮保留默认风格强度；只有 HDR 偏亮时可先试 80%，两种显示都偏亮时可试曝光 −0.3 EV。

## 验证与证据

| 验证 | 结果 | 本地证据 |
| --- | --- | --- |
| Python 全套回归 | 269 项通过 | `outputs/repo-audit/pytest-final.log` |
| 原生编辑状态 | 31 项通过 | `outputs/repo-audit/native-final/checks.json` |
| 实际数字输入框 | 9 项通过 | `outputs/repo-audit/global-controls-final/checks.json` |
| 直方图、边界提示及 GPU 极端参数 | 7 项通过 | `outputs/repo-audit/gpu-tools/checks.json` |
| GPU / 精确预览 | 32 / 32 通过；亮度误差中位数最多 0.0063 EV，P95 最多 0.122 EV | `outputs/repo-audit/gpu-final/comparison.json` |
| 默认效果与亮度对照 | 16 组通过；4 组默认 SDR/HDR 字节相等 | `outputs/repo-audit/brightness/audit.json` |
| 新鲜 CR2 / RAF 全流程导出 | 4492 × 6732 / 4156 × 6232；Ultra HDR probe、SDR/HDR 解码、元数据检查通过；原片保持原样 | `outputs/repo-audit/raw-exports/exports.json` |
| 新包启动 | 约 5.13 秒就绪 | `outputs/repo-audit/candidate-startup/state.json` |
| 实际安装、编辑恢复与签名 | 通过 | `outputs/repo-audit/installed-validation.json` |

自动化验证覆盖了上述路径；没有把长期内存稳定性或所有照片的主观 HDR 观感宣称为已验收。历史样片范围和此前人工检查仍见各版本验证记录。

## 重复运行

```bash
.venv/bin/python -m pytest
swiftc -O -D EDITOR_MODEL_CHECK macos/Sources/Img2UltraHDR/*.swift \
  scripts/check_macos_editor.swift -o /tmp/check-editor
HDRIMG_RESOURCES="$PWD/macos/Sources/Img2UltraHDR/Resources" \
  /tmp/check-editor outputs/new-editor-check
swiftc -O -D EDITOR_MODEL_CHECK macos/Sources/Img2UltraHDR/*.swift \
  scripts/check_global_controls.swift -o /tmp/check-global-controls
HDRIMG_RESOURCES="$PWD/macos/Sources/Img2UltraHDR/Resources" \
  /tmp/check-global-controls outputs/new-global-control-check
```
