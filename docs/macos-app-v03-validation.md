# Img2UltraHDR 0.3.0 验收记录

日期：2026-09-30。Apple M1 Pro / 16 GB，macOS 26.7.1。本机安装为
`~/Applications/Img2UltraHDR.app`，版本 0.3.0、build 1；Python 项目版本同步为 0.3.0。
Clear V8 R5 / Natural V8、203 nit 参考白和 1000 nit 输出上限保持不变。

## 改动

- Edit 菜单只保留撤销、重做、剪切、拷贝、粘贴。剪贴板命令继续走原生响应链，数值输入与日志选择不受影响。SwiftUI 移除文字编辑／格式分组，AppKit 在菜单更新时过滤系统额外入口；没有修改 macOS 全局设置。
- 白平衡选择按 250 ms 合并，只提交最后一次选择。最多保留当前照片 8 组精确预览的文件引用，模式返回时先同步选择匹配的已完成帧，再让后台确认。切换照片清空这层缓存；活跃帧通过现有租约避免淘汰。
- 相同 RAW 显影复用不可变浮点 TIFF，键包含原片内容、引擎／工具、白平衡、色调、温度偏移和降噪／细节配置内容。新条目只在完整显影与 ICC 附着完成后原子发布。硬链接避免重复复制整张 TIFF，CLI 默认仍使用原有显影入口。
- 渲染缓存先于 RAW 准备查询，已完成帧不会因为全尺寸底稿被淘汰而被迫重新显影。
- 日志中确认旧任务清理的 `killpg` 产生 `PermissionError`，并从任务与控制循环传播，导致后台退出。现在只对同一进程组、同一用户的存活成员回退到单进程信号，发信号前再次验证组归属。清理错误被隔离，控制进程继续接收请求；确认旧组结束前不会启动新的重任务。真实拒绝终止时显示可重试的取消错误。
- Swift 丢弃已替换后台进程的迟到消息，防止旧进程的数据／写入错误干扰新实例。
- 增加白色色阶／黑色色阶，支持实时 Metal、精确预览、全尺寸导出、撤销和旧记录读取。界面范围 −100～100，存储为 ±2 EV；旧记录缺失字段时按零读取，配方 schema 仍为 1。
- 双击数值参数名称重置单项，保留其他调整，可撤销。中文／英文文案与帮助同步更新。

## 色阶定义

曝光、阴影及现有高光处理后，在 scene-linear Rec.2020 数据中依次执行：

```text
black_weight = max(1 - Y / 0.045, 0)^2
RGB *= 2^(black_ev * black_weight)
重新计算 Y
white_weight = (Y / (Y + 0.9))^2
RGB *= 2^(white_ev * white_weight)
```

RGB 通道共用亮度增益；零值跳过；纯黑保持纯黑。这两条曲线在 ±2 EV 内单调，
不对 SDR 白以上的数据提前截断。Python、Swift 的统计变换和 Metal 使用同一公式。
这是本应用的定义，不宣称复刻 Adobe 的私有算法。新浮点包使用 `scene-tone-2`；
原生端仍可读取旧 `scene-tone-1` 的零调整测试资料。

## 验证结果

完整证据保存在未入库的 `outputs/app-v03-validation/`，不覆盖 0.2 验证资料。

| 检查 | 结果 |
| --- | --- |
| Python 回归 | 224 项通过，含进程组拒绝／取消错误隔离、旧记录、曲线、显影键和底稿淘汰后的缓存命中 |
| 原生模型／菜单 | 18 项通过，含白平衡请求合并、同步选择缓存帧、8 项上限、单项重置及撤销 |
| 默认冻结对照 | 19 张既有浮点输入 × Clear/Natural：38 项 SDR JPEG 和 HDR encoder input 均逐字节一致 |
| 新控件 GPU 对照 | 六类既有 RAW 浮点底稿 × 两种风格 × 基础／白正负／黑正负／组合 × SDR/HDR：144 项全部通过 |
| 新控件正常微调误差 | 所有帧有效区域的亮度 EV 误差：最差中位数 0.00608，最差 95 分位 0.02153；均有限 |
| 白平衡真实显影 | CR2 和 RAF 均完成自动／相机／自定义及返回；显影过程中每张连续切换 18 次，控制进程正常退出 |
| 最终取消压力验证 | 最新代码再次对两张照片各切换 18 次；取消确认 26.6 / 33.8 ms，控制进程退出码 0 |
| 非零色阶全尺寸导出 | CR2 与 RAF 各一张；白 +25、黑 −25，使用保留的高精度 RAW 底稿；Ultra HDR 检测和 SDR/HDR 解码均通过 |
| 正式安装应用 | 本地签名验证成功，Launch Services 启动；实际菜单为 `撤销 / 重做 / 剪切 / 拷贝 / 粘贴` |

GPU 对照使用相对最近精确结果的 ±0.5 EV（界面 ±25）微调；全范围曲线的
单调、有限值、零值、黑位和色度保持另由合成数据测试验证。没有重新运行
37 张 RAW 的完整默认导出；本次以冻结同输入对照及新增功能专项验证为主，
37 张全量记录仍见 [0.2 验收记录](macos-app-v02-validation.md)。

## 速度与限制

正式安装的应用在独立的 36 秒测试中呈现 1069 帧，约 **29.69 fps**；
参数更新至呈现的 95 分位 **50.96 ms**，帧间隔 95 分位 **41.67 ms**。
直方图与取色启用，覆盖新增色阶；窗口可见、GPU 无错误，直方图计数与
1574400 个像素一致。测量使用 30 Hz 的原生参数更新和 `MTLDrawable.presentedTime`，
不代表硬件鼠标事件端到端延迟，也没有替代 HDR 实屏人工检查。

白平衡功能测试中，CR2 三个缓存模式的后台返回耗时为 1.07–1.27 秒；RAF 的
相机／自定义缓存命中为 1.60 / 0.35 秒。原生模型在后台响应前先选中匹配的
已保留精确帧。验证期间构建更换了引擎标识，RAF 第二次自动模式发生正常的
版本失效与重建，不能当作缓存命中性能。冷显影与功能验证和其他渲染存在
并发，因此不据此声称获得严格的首次白平衡加速比例。

**首次进入尚未准备的白平衡模式仍需要等待精确显影，可能需要几十秒。**
本版解决取消导致后台退出、减少重复显影，并改善已缓存模式返回；没有
增加未经校准的跨模式色适应来宣称首次切换实时。自定义底稿就绪后的
色温／色调实时近似沿用 0.2 行为。屏幕 HDR 亮度与颜色仍需要人工确认。

## 重复验证

```bash
.venv/bin/python -m pytest
scripts/build_macos_app.sh
swiftc -O -D EDITOR_MODEL_CHECK macos/Sources/Img2UltraHDR/*.swift \
  scripts/check_macos_editor.swift -o /tmp/check-editor
HDRIMG_RESOURCES="$HOME/Applications/Img2UltraHDR.app/Contents/Resources/Img2UltraHDR_Img2UltraHDR.bundle" \
  /tmp/check-editor outputs/new-native-checks
.venv/bin/python scripts/check_white_balance_switching.py \
  outputs/new-wb-checks pics/0N6A9406.CR2 pics/DSCF8111.RAF
```

`--rapid-only` 可仅检查连续切换和取消。数值对照使用
`validate_interactive_previews.py --endpoints-only --prepared-cache <保留底稿缓存>`，
随后运行 `check_interactive_preview.swift` 和 `compare_interactive_previews.py`。
各次验证使用新目录；性能测试不要与其他重处理并发。
