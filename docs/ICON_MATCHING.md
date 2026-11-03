# 图标库匹配（Icon Library Matching）调研与设计

> 目标：给 `caption-svg` 增加“8–40px UI 小图标吸附到开源图标库”的能力：识别成可编辑 `<path>`，低置信度回退现有 `_vectorize_icon()` 描摹。
> 证据口径：优先官方 repo、文档、LICENSE、模型卡；检索 2026-11-03。**非法律意见**，发布前按实际条款复核。
> 代码现状：`images.py` 用 Lab k-means + `approxPolyDP` 描摹；`IconPath` 只有 `d/fill/points`，`svg.py` 只输出 `<path fill>`。本文只做设计，不改代码。

## 0. TL;DR

1. **先做 Lucide 单库、HOG 召回 + Chamfer/soft-IoU 精排、低分回退描摹**：1 周可落地，许可最干净、体积最小；不要一上来上 CLIP/VLM。
2. **小图 Chamfer 比 IoU/pHash 稳**：本地 500 图标实验，16px 退化查询 top-1 为 Chamfer 0.915、HOG 0.890、NCC 0.870、IoU 0.845、pHash 0.545；24px 以上 HOG 最好（0.970–0.985）。
3. **pHash 不做主线**：线性图标墨迹少，DCT 丢位置信息；只用于去重/粗召回。
4. **匹配从 `prep.original` 裁切**，不要用 `prep.flat` 的分析降采样图：2× 截图里 16px 逻辑图标有 32px 信息。
5. **阈值必须带 margin + 视觉增益检查**：分数高、领先 top-2 足够、替换后像素误差不比描摹差才接受。
6. **品牌 logo/多色/App 自定义图标默认不吸附**；Simple Icons 是 CC0 但商标权另计；SF Symbols 不能跨平台打包或导出。
7. **首版 `--icon-match` 默认 `off`**，`auto` 显式开启；benchmark 达到“接受精度 ≥95%、有害错配 ≤1%”后再改默认。
8. **3,000 图标索引压缩后约 4–7MB（估算）**，可随 App 离线分发。

## 1. 候选图标库与许可

### 1.1 对比表

规模为 2026-11 官方/Index 口径；“变体”不等于不同形状，索引前要按几何去重。

| 库 | 规模 | 风格/网格 | SVG 获取 | 许可 | 再分发注意 | 本项目 |
|---|---|---|---|---|---|---|
| **Lucide** | 2,000+（Iconify 2,015；`lucide-static` 含别名 2,292 SVG） | 24px 线性，2px；Feather 的 fork | `lucide-static/icons/*.svg` | **ISC** | 保留版权/许可；可商用修改 | **首选**通用线性 |
| **Tabler** | 6,257（outline+filled） | 24px，2px | `@tabler/icons` 的 `icons/{outline,filled}` | **MIT** | 保留 notice | **第二批** |
| **Material Symbols** | 官方 2,500+ 符号；Iconify 15,761 条含风格/填充/字重变体 | 24px；outlined/rounded/sharp；可变字重/fill/grade/optical size | fonts.google.com/icons；`google/material-design-icons` | **Apache-2.0** | LICENSE/NOTICE；专利条款；Google 品牌另计 | **Material/Android 首选** |
| Material Icons（旧） | 10,955（含变体） | 24px filled/outlined | 同上 repo | **Apache-2.0** | 同上 | 兼容旧 UI |
| **Phosphor** | ~1,500 × 6 字重 ≈ 9,000 | 256 viewBox；thin/light/regular/bold/fill/duotone | `@phosphor-icons/core/assets` | **MIT** | 保留 notice；duotone 过滤 | 第三批；利于 stroke 匹配 |
| **Feather** | 287 | 24px 线性，2px | `feathericons/feather/icons` | **MIT** | 保留 notice | 兼容；Lucide 已覆盖 |
| **Bootstrap Icons** | 2,000+（2,078） | 16px 网格，实心为主 | `bootstrap-icons/icons/*.svg` | **MIT** | 保留 notice | filled 候选 |
| **Fluent UI System Icons** | ~2,900 regular + ~2,975 filled；Iconify 19,945 含尺寸 | 16/20/24/28/32/48；regular/filled/color/light | repo `assets/*/SVG` | **MIT** | 保留 notice | Windows 风格 |
| **SF Symbols** | 7,000+ | Apple 系统符号；9 weights/3 scales | 仅 SF Symbols.app 导出 | **Apple 系统资产许可** | **仅 Apple 平台 UI**；禁 app icon/logo/商标；不得再分发/跨平台导出 | **不打包/不入索引/不输出** |
| Heroicons / Remix | 1,288 / 3,188 | 24px；outline/solid | 各自 repo | MIT / Apache-2.0 | 保留 notice | 备选 |
| Font Awesome Free | 免费子集约 2,000 | 多风格 | npm/repo | 图标 **CC BY 4.0**；字体 OFL；代码 MIT | **必须 attribution**；品牌商标另计 | 不默认 |
| Simple Icons | 3,467 | 品牌 logo，24px | `simple-icons` | **CC0 1.0** | CC0 不授予商标权；不可自动替换品牌 | **默认排除** |
| Iconify（聚合） | 300,000+，150+ 套 | 混合 | API/`@iconify/json`/离线包 | **每套不同** | 逐套审计；包大、元数据复杂 | 构建期工具，不作单一许可源 |
| App 自定义/品牌 | 不可枚举 | 各 App 自绘 | 无公开授权 | 版权/商标 | 不能随项目分发；只能用户自备或描摹 | **不自动吸附** |

### 1.2 许可与再分发

- **MIT/ISC**：保留 copyright + permission notice；`THIRD_PARTY_NOTICES` 按库列出；每个图标存 `license`/`source_url`。
- **Apache-2.0**：附 `LICENSE`，有 `NOTICE` 就附；标注修改；专利授权对下游友好。Material 的“名称/品牌”不等于代码许可。
- **CC0**：无需署名，但**商标/形象权另算**；Simple Icons 的 logo 不等于可当产品图标或暗示合作。
- **CC BY 4.0**：必须 attribution；不想做署名页就别默认引入 Font Awesome。
- **SF Symbols**：Apple 把符号视为 system-provided images，许可限“为 Apple 平台 App UI 使用”，禁止 app icon/logo/混淆性相似。把 path 写进跨平台 SVG 风险高；直接排除。参考 [HIG](https://developer.apple.com/design/human-interface-guidelines/sf-symbols)、[Xcode/SDK License](https://www.apple.com/legal/sla/docs/xcode.pdf)。
- **App/品牌图标**：截图里出现不等于有权分发；默认描摹或保留 crop；用户自备品牌库需自行确认授权。图标名也可能是商标。

### 1.3 规模与风格陷阱

1. **变体膨胀**：Material 15,761 含风格/填充/字重；Phosphor 9,072 是 ~1,500×6；Fluent 19,945 含尺寸。不去重 top-k 会被同一形状变体占满。
2. **viewBox 不同**：Lucide/Tabler 24，Bootstrap 16，Phosphor 256，Fluent 16–48。匹配统一 rasterize 96px 再紧裁切；输出保留原 viewBox，用 `<g transform>` 放进 bbox。
3. **stroke/fill 不同**：Lucide/Tabler/Feather 是 `fill="none" stroke="currentColor" stroke-width="2"`；Bootstrap/Material filled 是实心 path。**fill/outline 必须硬过滤**。
4. **内边距不同**：Material 常留 2–4px，Bootstrap 更贴边；匹配用紧裁切轮廓，同时把占用率/内边距作为特征，避免大图标缩太小。
5. **别名/近似重复**：`settings` vs `settings-2`、`search` vs `zoom-in` 可能视觉相近；建 alias 簇，但不要合并语义不同的形状。

## 2. 离线匹配算法

### 2.1 算法对比

| 方法 | 特征/做法 | 优点 | 缺点 | 速度/内存（量级） | 许可/依赖 | 适用 |
|---|---|---|---|---|---|---|
| **二值/软 IoU** | 归一化后比较墨迹 mask 的 IoU/Dice | 简单可解释 | 对 1px 位移、stroke 粗细极敏感 | 500 库 ~0.07ms/query | NumPy/OpenCV | baseline、精排 |
| **NCC/模板匹配** | `matchTemplate`/零均值归一化相关 | 对亮度稳；最简单 | 不抗旋转/尺度；小图易漂 | ~0.05ms/query | OpenCV | baseline、召回 |
| **pHash/dHash** | DCT/差分哈希 + Hamming | 极小极快、抗轻微压缩 | 线性图标位置信息丢失严重 | <0.01ms/query | 开源实现多 | 去重/粗召回，**不做 top-1** |
| **Chamfer/距离变换** | 墨迹/边缘距离变换，对称平均距离 | 容忍 stroke 位移/粗细；16px 最稳 | 对噪声/断线敏感；需调 τ | 500 库 ~5ms/query（Python 循环） | OpenCV | **16px 精排首选** |
| **HOG** | 32×32 梯度方向直方图 | 抗 stroke 粗细/光照；24px 以上稳 | 丢全局布局；对旋转略敏感 | ~0.03ms/query（500 库） | OpenCV | **24–48px 召回/精排首选** |
| **Shape Context** | 轮廓采样 + log-polar 直方图 + 匹配 | 旋转/尺度不变；经典 | 需干净轮廓；8–12px 不可靠；慢 | 10–50ms/query（估算） | 自实现/学术代码 | 研究/可选，非首版 |
| Hu 矩/Zernike | 全局矩/正交矩 | 极快、旋转不变 | 太粗，难区分相似 UI 图标 | <0.1ms | OpenCV | 不推荐 |
| **小型 CNN embedding** | 渲染图标训练 triplet/ArcFace，128–256 维 | 可跨风格；强于手工特征 | 需训练/GPU；过拟合 | 5–20ms/query；模型 2–10MB（估算） | 自研权重 | 长期最优，二期后 |
| **CLIP/SigLIP/DINOv2** | 预训练视觉 embedding + 余弦 | 零样本语义强；可文本检索 | 对 16px 线性细形状弱；模型大 | 30–200ms/query；90MB–1.2GB | CLIP 代码 MIT；SigLIP/DINOv2 Apache-2.0；权重逐页核对 | 只做 top-k 语义重排 |
| **OmniParser/Florence-2** | 检测图标框 + 生成功能描述 | 补检测、给语义标签 | 不做精确字形 ID；CPU 慢、模型大 | 0.5–1GB；CPU 秒级/图（估算） | `icon_detect_v3`+`icon_caption` MIT | 可选增强，非默认 |

### 2.2 本地小实验：Lucide 500 图标

**设置**（临时目录运行，图片不进 git）：`lucide-static@1.66.0` 确定性抽取 500 个图标；resvg-js 以 96px 渲染黑图标透明底。查询加 ±2° 旋转、±3% 缩放、±0.6px 平移、0.45σ 模糊，再降采样到 16/24/32/48 逻辑 px，紧裁切归一到 32×32；库端干净渲染归一 32×32；每尺寸 200 查询。`exact@1` 按 id；`visual@1` 按与真值 IoU ≥0.9（避免别名把正确视觉匹配算错）。

| 方法 | 16px（exact/visual） | 24px | 32px | 48px |
|---|---|---|---|---|
| IoU | 0.845 / 0.875 | 0.860 / 0.880 | 0.940 / 0.955 | 0.970 / 0.995 |
| NCC | 0.870 / 0.880 | 0.870 / 0.890 | 0.965 / 0.975 | 0.985 / 1.000 |
| pHash | 0.545 / 0.555 | 0.695 / 0.700 | 0.680 / 0.690 | 0.730 / 0.740 |
| **Chamfer** | **0.915 / 0.940** | 0.920 / 0.940 | 0.960 / 0.975 | 0.975 / 1.000 |
| **HOG** | 0.890 / 0.910 | **0.970 / 0.995** | **0.985 / 0.995** | 0.980 / 1.000 |

干净同尺寸 oracle：16px 0.875/0.885，24px 0.965/1.000，32px 0.975/1.000。24px 以上同库匹配几乎不是问题；**难点在 16px 以下和跨库风格差异**。

**stroke 失配**（库 stroke=2.0，24px，50 样本，仅 exact@1）：查询 1.5 时 IoU/NCC/HOG/Chamfer 均 0.98；查询 2.5 时 IoU 0.76、NCC 0.76、**HOG 1.00**、Chamfer 0.82。实际截图图标常比库图粗 10–25%，所以 **HOG 做主召回、Chamfer 做精排**。

**速度**（Python 3.14 + OpenCV 4.13，500 库）：建库 HOG 9ms、距离变换 3ms；单查询 IoU 0.07ms、HOG 0.03ms、Chamfer 4.95ms（逐对循环）。3,000 库外推 HOG ~0.2ms、IoU ~0.4ms、Chamfer ~30ms/query（估算，可向量化/多线程）。

**边界**：以上是**同库同渲染器**上界，只测退化噪声，没测跨库风格；真实截图（尤其 1× 8–12px）top-1 会明显更低。500 图标/200 查询、无遮挡/文字/品牌，结论是工程排序，不是 SOTA 声明。

### 2.3 检测/语义模型

- **OmniParser v2**：`icon_detect_v3`（YOLOv9-E 微调）MIT，`icon_caption`（Florence-2）MIT；旧 `icon_detect`（YOLOv8）AGPL，不要用。仓库 `LICENSE` 与 README 徽章有 CC-BY-4.0/MIT 不一致，按文件核对。适合补图标框 + 功能描述，**不适合精确字形匹配**。[repo](https://github.com/microsoft/OmniParser)、[HF](https://huggingface.co/microsoft/OmniParser-v2.0)。
- **Florence-2-base-ft**：0.23B，MIT；caption/region caption/OCR。可给裁切图标生成 “a gear/settings icon” 描述，做语义重排或未知图标打标；CPU 秒级/图（估算）。[HF](https://huggingface.co/microsoft/Florence-2-base-ft)。
- **CLIP/SigLIP/DINOv2**：适合语义召回/重排，不适合单独做 16px 字形判定。CLIP ViT-B/32 ~150M（~600MB fp32/~300MB fp16），DINOv2-base ~86M（~350MB fp32），SigLIP-base 同量级；CPU 单图几十到几百 ms（估算）。库端 embedding 离线算，查询端只跑一次。
- **没有公开的“UI 图标库匹配”基准**：SVG-Bench 测生成，ScreenSpot 测 grounding，都不测“16px 截图图标 → 库 id”；必须自建（§5）。

## 3. 8–40px 小图标管线

### 3.1 总览

```
residual CC
  -> 合并断裂部件 / 去文字、照片、面板边
  -> 从 prep.original 裁切（不是 prep.flat）
  -> 估计局部背景、ink mask、stroke、fill/outline、颜色数
  -> 硬过滤（尺寸/长宽比/占用率/文字重叠/多色/品牌）
  -> Stage A: HOG + 占用率先验 -> top-50
  -> Stage B: 预渲染多尺寸 mask 的 soft-IoU + Chamfer + HOG -> top-10
  -> 分数 + margin + 视觉增益 + 语义重排
  -> 接受：输出库 path；拒绝：回退 _vectorize_icon()
```

### 3.2 候选生成与硬过滤

现有 CC 会断裂（省略号、Wi-Fi 弧线、圆环里的加号）。匹配前：3×3 close；或合并 bbox 间距 ≤ `max(2, 0.12*min(w,h))`、组合长宽比 ≤3 的部件。继续用 OCR 框、photo box、panel 边带屏蔽。裁切从 `prep.original` 取（analysis 坐标乘 `prep.scale`），再按逻辑尺寸归一到 32×32。局部背景优先用所在 panel 的 `fill`，否则取 crop 四边中位数；ink mask 用颜色距离：`ink = clip(dist(pixel,bg)/max(dist,eps),0,1)`，深色模式自动反向。

| 过滤 | 规则 | 目的 |
|---|---|---|
| bbox | 逻辑 8–48px（6–64px 降置信度） | 太小无信息，太大不是图标 |
| 长宽比 | 0.55–1.8（箭头/chevron 放宽到 0.35） | 排除文字行、分割线、整块图 |
| 占用率 | outline 0.04–0.75；filled 0.25–0.95 | 区分线框/实心/实心方块 |
| stroke | `2*median(DT)`；1–3px，超过 `0.3*min(w,h)` 拒绝 | stroke 过滤 + 输出缩放 |
| 颜色数 | 主色 ≤4；多色/高饱和多 hue 标 brand | 品牌/插画保护 |
| 文字/照片重叠 | 与 OCR 框 >30% 或 photo box 重叠拒绝 | 防把字/照片当图标 |
| 风格 | 由占用率/孔洞数判 outline vs filled，硬过滤 | 避免线框配实心 |
| 重复形状 | 同屏相同 mask 聚类，匹配一次后复用 | 降误配、提速度 |

### 3.3 两阶段匹配与打分

**Stage A（召回，便宜）**：HOG 余弦 + 占用率/长宽比/风格先验，取 top-50；3,000 库一次矩阵乘约 0.2ms（估算），不 rasterize。

**Stage B（精排）**：top-50 取预渲染 16/24/32/48 mask（选最近桶，必要时 resize），算：

```
shape = 0.40*soft_iou + 0.30*chamfer_score + 0.20*hog_cosine + 0.10*prior
prior = 1 - 0.5*min(1, |aspect_q-aspect_c|/0.5)
          - 0.5*min(1, |fill_q-fill_c|/0.3)
```

`soft_iou = sum(min(a,b))/sum(max(a,b))`；`chamfer_score = exp(-mean_distance/τ)`，τ 取 1.5–2.5px（32×32 归一化坐标）。权重是工程起点，用 §5 验证集校准；只对 top-10 做 Chamfer，避免全库距离变换。

### 3.4 尺寸/渲染归一化

- **构建期**用 resvg 以 4× 目标尺寸渲染（16px 渲染 64px），再 `INTER_AREA` 降到桶尺寸；运行期不依赖 SVG rasterizer。
- 库端 96px 渲染 → 紧裁切 → 32×32；保存 16/24/32/48 多桶，查询按逻辑尺寸选桶。
- 输出保留原 `viewBox`/`d`，用 `translate(tx,ty) scale(s)` 放进 bbox；`s=min(w/vbw,h/vbh)`，居中。stroke 图标 `stroke-width` 按 `s` 缩放，避免 2px 库图标放进 8px 位置变 0.67px。
- filled 用 `fill-rule="evenodd"`，不加 stroke；渐变/多色回退。
- 查询若检测到 ±5° 以上旋转，先做 -6/0/+6° 小搜索再取最高分，否则拒绝。
- 颜色：匹配只看 shape；最终 fill/stroke 用 ink 像素中位数着色，保留原色。

### 3.5 阈值、回退与歧义

| 模式 | 接受条件 | 回退 |
|---|---|---|
| `off` | 不匹配 | 全部 `_vectorize_icon()` |
| `auto` | `score ≥0.84` 且 `margin ≥0.06`，视觉增益 ≥-0.02，非品牌/多色 | 低分/无增益 → 描摹 |
| `strict` | `score ≥0.92` 且 `margin ≥0.12`，语义证据或重复实例一致，视觉增益 ≥0 | 低分 → 描摹 |

- 8–12px 在 `auto` 阈值上加 0.04；32–40px 可减 0.02。`margin` 是去掉 alias 簇后 top-1 与 top-2 的差。
- **视觉增益**：预渲染候选与查询 crop 算 MAE；若 `MAE_candidate > MAE_trace + 0.02`，分数高也回退，防止“形状像但像素更差”。
- **歧义**：① top-1/top-2 mask IoU ≥0.92 视为视觉同义，按语义/风格择优；② 邻居 OCR 文本在图标左/右/下 1.5× 宽度内，匹配名称/标签/同义词（search/find、settings/gear、delete/trash、close/x、menu、home、user、cart、heart、star、bell、download/upload/share/filter/calendar/clock/mail/phone/camera/play）；③ 同屏重复形状多数投票；④ 同工具栏/标签栏优先同库同风格，选中态 filled 由邻图标推断。
- **品牌保护**：多色、复杂渐变、Simple Icons 强命中、含文字/图形的 logo 一律不自动吸附；输出描摹或 crop，scene 标 `brand_candidate`。

### 3.6 输出与 scene JSON

扩展 `IconPath`（默认值保持向后兼容）：

```python
@dataclass
class IconPath:
    d: str
    fill: str
    points: int = 0
    source: str = "trace"          # trace | library
    library: str = ""
    name: str = ""
    score: float = 0.0
    margin: float = 0.0
    view_box: tuple[float, float, float, float] | None = None
    bbox: tuple[float, float, float, float] | None = None
    stroke: str | None = None
    stroke_width: float | None = None
    alternatives: list[dict] | None = None
```

`svg.py` 对 `source="library"` 输出 `<g transform="translate(tx,ty) scale(s)"><path d=... fill="none" stroke=... stroke-width=... stroke-linecap="round" stroke-linejoin="round"/></g>`；描摹路径仍用现有绝对坐标，不改渲染语义。scene JSON 增 `source/library/name/score/alternatives`，编辑器可“恢复描摹/换候选”。

## 4. 离线打包与索引（1–3k 图标）

### 4.1 索引结构

```
caption_svg/data/icons/
  icons.json      # id/lib/name/aliases/viewBox/d/style/stroke_width/tags/license/source_url
  icons.npz       # mask_16/24/32/48、hog、phash、aspect、fill_ratio
  THIRD_PARTY_NOTICES.md
```

不要把 mask base64 塞进 JSON：JSON 放元数据，二进制数组放 NPZ（`np.savez_compressed`/zstd），按 id 索引。

### 4.2 构建流程

1. **锁版本**：固定 tag/npm 版本，下载 tarball，记录 SHA256。
2. **解析**：只接受 path-only 图标；含 `<mask>`/`<filter>`/`<image>`/渐变/多色 fill 的跳过或标 `complex`，避免运行期渲染差异。
3. **归一化**：读 `viewBox`，resvg 渲染 96/128px，紧裁切，归一到 32×32；记录占用率、长宽比、stroke、fill/outline。
4. **特征**：多桶 mask、HOG（324 维）、pHash（64 bit）；可选 128 维 embedding。
5. **去重/alias**：mask IoU >0.95 合并；保留 canonical，其余进 `aliases`；语义不同的近似形状不要合并。
6. **许可审计**：每个图标写 `license`/`source_url`，生成 `THIRD_PARTY_NOTICES.md`；SF Symbols/Simple Icons/App 品牌默认不进索引。
7. **压缩校验**：HOG float16、mask packbits/PNG、JSON minify；重载抽样渲染，确认 path/transform 正确。

### 4.3 体积预算（3,000 图标，估算）

| 内容 | 原始 | 压缩后 |
|---|---|---|
| path `d` + 元数据（~700B/个） | ~2.1MB | 0.8–1.5MB |
| mask 16/24/32/48（uint8） | ~12.5MB | 1.5–2.5MB（packbits） |
| HOG 324 × float16 | ~1.9MB | ~1.2MB |
| pHash/标量 | <0.1MB | <0.1MB |
| **合计** | ~16MB | **4–7MB** |

只保留 32px mask + HOG 可压到 ~3MB；加 128 维 embedding 再加 ~0.75MB。桌面 App 10MB 预算可接受；移动端只带 Lucide（~2,000 图标，~2–3MB）或按需下载。

### 4.4 运行时

- `np.load(..., mmap_mode="r")` 打开 NPZ，只读、线程安全；只把当前 screen 用到的库/数组页读入。
- 构建期做 SHA256，运行期校验版本；不联网、不自动下载；索引随安装包/签名离线包分发。
- 构建期 rasterize，运行期只做 NumPy 距离/矩阵运算，**不需要打包 resvg/cairo**，减少许可与体积问题；任意尺寸优先 resize 预计算 mask，不重渲染 SVG。

## 5. 评估：合成图标 benchmark

### 5.1 合成集

不要只拿“库图 vs 库图”自测，要模拟截图管线：

| 维度 | 取值 |
|---|---|
| 逻辑尺寸 / DPR | 8/12/16/24/32/40px；1×/2×/3×（高分辨率渲染后降采样） |
| 主题 / 背景 | 浅色/深色/高对比/半透明；白/灰/品牌色/模糊照片 |
| 压缩 / 模糊 | PNG/JPEG 60/80/95；0/0.5/1.0px |
| 变换 | ±3° 旋转、±5% 缩放、±1px 平移 |
| stroke / 遮挡 | 库值 ±20%；0/10/20%（文字、角标、选中态） |
| 上下文 / 邻居 | 按钮/tab/列表行/工具栏/对话框；左/右/下 1.5× 图标宽度的标签 |
| 未知类 | 留出 10% 图标不进索引，标 `unknown`；另加品牌/自定义 |
| 来源库 | Lucide/Tabler/Material/Phosphor/Bootstrap/Fluent |

生成物放 `out/iconbench/`（gitignore），**不把测试图片提交 git**；只提交生成脚本、标注 JSON 和指标。

### 5.2 指标

- **top-1/top-3 exact**：id 命中；**visual**：与真值 mask IoU ≥0.9 或 SSIM ≥0.98（避免 alias 误判）。
- **覆盖率/拒识率**：低于阈值回退描摹的比例；**接受精度**：所有被接受样本中正确/视觉等价的比例。
- **有害错配率**：按语义组加权（§5.3），比 top-1 更重要的产品指标。
- **像素 diff**：bbox 内 MAE/SSIM/LPIPS/ΔE00；分别算吸附后与描摹后，吸附必须不差于描摹。
- **矢量/性能**：path 数、节点数、SVG 体积、4× 重渲染稳定性；p50/p95 延迟、峰值内存、索引体积、冷启动。
- **人工**：1–5 分“功能是否一致”，以及“是否愿意在编辑器中保留”。

### 5.3 false-positive 成本

给图标定义语义组（search/settings/delete/close/menu/home/user/cart/heart/star/bell/download/upload/share/filter/calendar/clock/mail/phone/camera/play/arrow…）：

| 情况 | 成本 |
|---|---|
| 同组/视觉变体（settings vs settings-2） | 0–1 |
| 相邻功能（search vs filter） | 2 |
| 不同功能（search vs delete） | 5 |
| 品牌/身份错配 | 10 |

报告 **weighted harmful wrong-snap rate**；`auto` 目标 ≤1%，`strict` ≤0.2%。未知/自定义图标被强行吸附单列，目标 ≤2%；这比“没识别出来”更伤信任。

### 5.4 验收门槛

- 16–32px 已知图标：top-1 visual ≥90%，top-3 visual ≥97%。
- `auto` 接受精度 ≥95%，`strict` ≥99%；拒识率 ≤25%（宁可回退，不要乱配）。
- 有害错配 ≤1%；未知类误吸附 ≤2%。
- 吸附后像素 MAE 不高于描摹 +0.02；SSIM 不低于描摹 -0.005。
- 3,000 库 p95 ≤2ms/图标（CPU 级联）；索引 ≤8MB。
- 回归：文本 CER/行框 IoU/面板 IoU 不下降；图标路径节点数下降，文件体积不增加。

## 6. 对本项目的建议

### 6.1 Now / Next / Later

| 阶段 | 内容 | 工作量（估算） | 验收 |
|---|---|---|---|
| **Now（1 周）** | Lucide 单库索引；HOG 召回 + Chamfer/soft-IoU 精排；`--icon-match=off/auto/strict`；scene 字段；200–500 张合成 benchmark；默认 `off`，`auto` 可显式开 | 5–7 人日 | 索引 ≤3MB；16–32px visual top-1 ≥88%；未知误吸附 ≤3% |
| **Next（2–3 周）** | 加 Tabler/Material Symbols/Phosphor；filled/outline 硬过滤；语义重排（邻居 OCR/同义词/重复实例）；视觉增益；默认切 `auto` | 8–12 人日 | `auto` 接受精度 ≥95%；有害错配 ≤1%；p95 ≤2ms |
| **Later（1–2 月）** | 小 CNN embedding（跨风格）；CLIP/DINOv2 只重排；可选 OmniParser/Florence-2 补框/描述；用户自备品牌库；A11y 角色 | 15–30 人日 | 跨库 top-1 提升；仍离线/许可审计 |

### 6.2 模块 API

新增 `caption_svg/iconmatch.py`，不把匹配塞进 `images.py`：

```python
@dataclass(frozen=True)
class IconHit:
    library: str; name: str; score: float; margin: float
    d: str; view_box: tuple[float, float, float, float]
    style: str                      # "outline" | "filled"
    stroke_width: float | None
    fill_rule: str = "evenodd"
    alternatives: tuple[tuple[str, str, float], ...] = ()
    license: str = ""

class IconIndex:
    @classmethod
    def load(cls, path=None, libs=("lucide",)) -> "IconIndex": ...
    def match(self, crop_bgr, *, bbox, bg_rgb=None,
              mode="auto", text_hint=None, scale=1.0) -> IconHit | None: ...
```

`images.py` 在 `_vectorize_icon()` 前调用 `icon_index.match(...)`；命中构造 `IconPath(source="library", ...)`，否则走原描摹；`off` 时不加载索引，行为与现在一致。

### 6.3 CLI

```bash
--icon-match {off,auto,strict}   # 首版默认 off；达标后默认 auto
--icon-libs lucide,tabler,material-symbols,phosphor
--icon-min-score 0.84
--icon-min-margin 0.06
--icon-index PATH                # 自定义/精简索引
--icon-debug                     # 输出 top-k、分数、拒绝原因
```

`off` 零回归；`auto` 普通用户；`strict` 法务/品牌敏感或只允许高置信吸附。调试写 `out/*.icons.json`（bbox、top-k、分数、是否回退），不提交测试图。

### 6.4 收益与风险

**收益**：24px 图标从描摹 100–450 点降到库图 10–40 节点；可编辑、可换 stroke/color，4× 更干净；图标组路径数降一个数量级，整图体积通常降 3–10%（估算，图标占比小）。

**风险**：① 错误吸附改变语义（search→delete 比描摹丑更糟），所以阈值保守、默认 off、必须可回退；② 1× 8–12px 信息不足，不强行匹配，要求 2×/3× 或直接描摹；③ 品牌/多色默认不吸附，Simple Icons CC0 不等于免费品牌库；④ MIT/ISC/Apache 仍要 notice，SF Symbols 不进索引，自定义图标不打包；⑤ 库图 stroke/cap/join 与截图不同，用 stroke 缩放 + 视觉增益，必要时回退；⑥ 索引版本/viewBox/path 语法要校验，升级库后跑回归，不静默换形状。

### 6.5 最小落地顺序

1. `tools/build_icon_index.py`：Lucide → `icons.json` + `icons.npz` + 许可元数据。
2. `caption_svg/iconmatch.py`：HOG 召回 + 多尺寸 mask 精排（先不做语义）。
3. 接 `images.py`/`svg.py`/`cli.py`：`--icon-match=off` 默认，`auto` 可开；scene 增字段。
4. 合成 benchmark（先 200 张），标定阈值；把“有害错配/未知误吸附”作为门禁。
5. 达标后加 Tabler/Material、语义重排，再把默认切 `auto`。

## 7. 参考

**图标库/许可**：Lucide https://github.com/lucide-icons/lucide（ISC）· Tabler https://github.com/tabler/tabler-icons（MIT）· Material Symbols https://developers.google.com/fonts/docs/material_symbols、https://github.com/google/material-design-icons（Apache-2.0）· Phosphor https://github.com/phosphor-icons/core（MIT）· Feather https://github.com/feathericons/feather（MIT）· Bootstrap Icons https://github.com/twbs/icons（MIT）· Fluent https://github.com/microsoft/fluentui-system-icons（MIT）· SF Symbols https://developer.apple.com/sf-symbols/、HIG、Xcode/SDK License（Apple 平台限定，不再分发）· Heroicons https://github.com/tailwindlabs/heroicons（MIT）· Simple Icons https://github.com/simple-icons/simple-icons（CC0，商标另计）· Font Awesome https://fontawesome.com/license/free（图标 CC BY 4.0）· Iconify https://iconify.design/（300,000+，逐套许可）

**算法/模型**：Shape Context，Belongie et al., IEEE PAMI 2002，https://doi.org/10.1109/34.993558 · HOG，Dalal & Triggs, CVPR 2005，https://doi.org/10.1109/CVPR.2005.177 · pHash，Zauner 2010，https://www.phash.org/ · OpenCV template matching https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html · CLIP https://github.com/openai/CLIP、https://arxiv.org/abs/2103.00020（代码 MIT；权重按发布页）· DINOv2 https://github.com/facebookresearch/dinov2（Apache-2.0）· SigLIP https://huggingface.co/google/siglip-base-patch16-224（Apache-2.0）· OmniParser https://github.com/microsoft/OmniParser、https://huggingface.co/microsoft/OmniParser-v2.0（`icon_detect_v3`/`icon_caption` MIT；旧 YOLOv8 AGPL；repo LICENSE 与 README 有 CC-BY/MIT 不一致，按文件核对）· Florence-2 https://huggingface.co/microsoft/Florence-2-base-ft、https://arxiv.org/abs/2311.06242（MIT）· resvg/resvg-js（MPL-2.0，构建期）· OpenCV（Apache-2.0）· NumPy（BSD-3-Clause）

> 未验证/估算：跨库 top-1、CLIP/Florence-2 的 CPU 延迟、索引压缩体积、实际截图精度提升均为工程估算；§2.2 只覆盖同库退化，不构成 SOTA 结论。落地前用 §5 自建 benchmark 复测。
