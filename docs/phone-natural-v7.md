# phone-natural V7：自然人像与浅肤色保留

`--style phone-natural` 更新为 2026-09-29 确认的人像样片方案，替换该选项原来的 V1 处理。CLI 名称仍为 `phone-natural`。默认选项仍为 `phone-clear` V6；`natural` 保留 V1。

```bash
img2uhdr render photo.CR2 --style phone-natural --output outputs/phone-natural
```

## 已确认的处理

这版使用 RAW 优先白平衡，保留自然的明暗与 HDR，不额外提高样片亮度。基础显影、降噪、锐化、蓝色区域保护、相机参考肤色保护和全局影调沿用 RAW 优先流程；参数为中间调 `0.38 EV`、亮部压缩 `0.42`、局部对比 `0`、选择性鲜艳度 `0.23`、Display P3。

局部对比默认关闭，因此不运行宽范围显示细节增强、局部直方图、去雾式材质反差和天空局部影调。逐层对照显示，这些增强会把腿部原有的边缘阴影放大。仍保留全局明暗、原始 RAW 细节、肤色保护和独立的人像影调；不依赖轮廓补偿遮盖暗边。显式指定正的 `--local-contrast` 可以重新启用这些步骤，届时不再等同于已确认样片。

对浅肤色的额外保护只阻止后续去色。RAW 优先阶段没有再次改变光源增益，因此已有的前后肤色置信度被重复相乘；V7 在颜色细化阶段将权重 `s` 调整为 `1 - (1 - sqrt(s))³`，平滑加强脚踝、脚背等浅色区域的保护。按原颜色处理结果归一亮度，原肤色支持范围以外逐像素保持原处理结果。这不会给脚踝绘制单独的颜色，也不会把白墙一起调暖。

渲染用的人物判断基于最终完成 RAW 肤色保护的底稿，与已确认样片保持一致；它与早期白平衡保护的参考分开计算，并在主图和内部参考之间复用。SDR 与独立的 HDR 参考使用同样的颜色保护；单独修改 `--sdr-exposure-ev` 不应改变 HDR 目标。`render.style.algorithm_version` 为 `7`，新增保护记录在 `render.tone_mapping.phone_skin.pale_color_protection`。

## 控制和边界

- 默认 RAW 白平衡为 `auto`；显式 `camera`、`custom` 仍受尊重，并跳过自动白平衡的相机参考回退。
- `--skin-protection-strength 0` 关闭肤色保护，包括新的浅肤色去色约束。关闭自动观感、显式覆盖对比度/饱和度、暗夜场景也不启用自动肤色保护。
- `--raw-denoise-strength`、`--raw-detail-strength`、`--surface-denoise-strength` 和 SDR/HDR、人像影调控制均可用于新版 `phone-natural`。
- 肤色保护依赖连续颜色规则和人物检测，不是精确的皮肤分割；相似颜色的衣物仍可能被保护。检测不可用时继续记录降级原因。
- 单张人像得到确认不代表所有光源和夜景都完成了视觉验收。查看 HDR 效果仍应使用支持 HDR 的显示环境。

## 验证

自动测试包含按风格选择 RAW 白平衡、匹配的降噪与相机参考、浅肤色色度保留、亮度保留、背景不受新增保护影响、过渡连续、显式控制、SDR/HDR 独立性和默认局部增强关闭。真实 RAW 导出与已确认样片的比较记录在 [V7 验证记录](phone-natural-v7-validation.json)。照片与实验输出仅保留在 Git 忽略目录。

本次共 157 项测试通过。使用正式 CLI 从 `0N6A9416.CR2` 重新显影并导出 4492 × 6732 Ultra HDR：线性底稿、SDR 像素和最终 Ultra HDR 文件均与已确认样片完全一致，成品 SHA-256 相同；完整 SDR/HDR 解码通过。该结果验证这张样片的处理已完整转入预设，不代表其他场景也已逐张完成视觉验收。

[旧 phone-natural 校准](phone-natural-calibration.md)保留为历史依据，其参数不再代表当前选项。
