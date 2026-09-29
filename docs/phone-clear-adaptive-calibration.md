# `phone-clear` 自适应校准记录

2026-09-28。默认风格仍名为 `phone-clear`；本次算法版本为 `2`，写入每张成片的 `render.style.algorithm_version`。旧版 12 组数值保存在 [`phone-clear-v1-baseline.json`](phone-clear-v1-baseline.json)，原始照片和大幅对照图继续留在 Git 忽略的目录。

## 实现原则

- SDR 底图按显影前的线性 RAW 亮度分布连续调整：极暗场景保留暗部，低亮度室内场景降低过强的自动提亮，高亮中性物体占比高的逆光场景提高主体。现有 `--sdr-exposure-ev` 叠加在场景判断之后，仍只改变 SDR 与增益图，不改变 HDR 目标图。
- HDR 根据最终 SDR 参考亮度独立提高中间调；暗景的高光增量参考未压缩的 RAW 亮度，避免将洞口、灯棚附近的一整片 SDR 亮面都推到峰值。显式传入正的 `--exposure-ev` 时，仅在 RAW 最亮的一小部分增加额外 HDR 余量。最终 `maxContentBoost` 从 SDR／HDR 实际每通道差值估算，不再固定为 `peak_nits / 203`。
- 对蓝色和暖色做平滑的色相校准；暗景降低过强的色度，室内暖色处理有独立权重。中性像素保持不变。`natural` 和 `phone-natural` 沿用先前算法。

## 12 组 Pixel 9 样本

用 `scripts/audit_phone_clear.py` 将 Pixel DNG 经 RawTherapee 转成临时缩小预览；Pixel JPEG 的 SDR 底图和 HDR 增益图逐像素配对。RAW 与 JPEG 仅用于分布比较，几何和多帧处理差异使它们不能逐像素归因。误差定义为各样本 `|log2(repo / phone)|` 的中位数。

| 指标 | 旧版 | 新版 | 误差降低 |
| --- | ---: | ---: | ---: |
| SDR 中位亮度 | 0.513 | 0.320 | 37.6% |
| SDR 亮度 0.1–0.4 范围内的 HDR 中间调增益 | 0.555 | 0.168 | 69.7% |
| HDR 第 99.5 百分位亮度 | 0.221 | 0.143 | 35.2% |

暗景 01／05／06 的 HDR 第 95 百分位在局部高光调整后不再整片逼近峰值；白裙逆光 09 的 HDR 超过 SDR 白的面积约为 **49%**，手机参考约为 **56%**。室内人像 07 的 SDR 底图已大幅变暗，但仍比手机参考亮；单帧 DNG 与手机合成 JPEG 的差异仍是明显限制。色彩类别统计是按色相划分的诊断，不等于可靠的皮肤或天空语义识别。

在 repo 根目录运行：

```bash
.venv/bin/python scripts/audit_phone_clear.py jpg_hdr_sample_effect \
  --output outputs/phone_clear_audit \
  --scene-cache outputs/phone_reanalysis/scenes \
  --baseline docs/phone-clear-v1-baseline.json
```

结果写入 `outputs/phone_clear_audit/audit.json`，并生成每组六张的 SDR／HDR 并排图。HDR PNG 是统一曲线生成的 SDR 预览，用于观察相对亮度分布；最终 HDR 观感仍应看 Ultra HDR JPEG 原件。

## Canon RAW 回归

- `0N6A9479.CR2`：固定 +1.0 EV、5500 K、tint 1.0、对比度 1.35、饱和度 1.18。新版解码 HDR 峰值约 **3.42 倍 SDR 白**；在固定腿部矩形内，第 95 百分位由旧版约 **0.95** 升至 **1.35**，超过 SDR 白的像素比例由约 **2.8%** 升至 **44%**。
- `0N6A9480.CR2`：固定 +0.95 EV、SDR −0.45 EV，其余白平衡与观感参数同上。新版解码 HDR 峰值约 **3.41 倍 SDR 白**；腿部第 95 百分位由约 **0.84** 升至 **1.23**。旧版和新版的 SDR 及 Ultra HDR 成片分别保存在 `outputs/0N6A9480_phone_clear_balanced_hdr/` 与 `outputs/phone_clear_optimized_9480/`。

两张 Canon 的 SDR 并排图和腿部数值记录在 `outputs/phone_clear_audit/`。肤色、衣服与墙面在缩小预览中仍有纹理；是否符合个人偏好的最终 HDR 亮感，应在同一 HDR 显示器与亮度设置下查看原件。

将同一张 Canon 线性场景重新用 `natural`、`phone-natural` 渲染，并与此前保存的两种风格 SDR 成片解码后逐像素比较：两者均无像素差异。JPEG 文件字节因旧成片含额外拍摄元数据而不同。
