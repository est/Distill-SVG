# 蒸矢 / distill-svg：截图 → SVG 的 SOTA 调研（2023–2026）

> 范围：GUI 截图/UI 截图的语义重建、矢量化、屏幕解析、OCR、字体、图标/emoji、照片隐私、评估与本地离线应用架构。
> 目的：为 `distill-svg` 的 flat/wireframe、照片嵌入、可选中文本、本地离线 App 与后续原生 macOS/Windows 版本提供可执行建议。
> 证据口径：优先论文、官方 repo、官方文档与模型卡；链接在 2026-11-03 检索。**这不是法律意见**，发布前必须以实际 `LICENSE`、模型卡和字体 EULA 为准。
>
> 注：照片默认 `embed` 是项目决策；`placeholder/blur/scrub` 是隐私降级模式。

## 0. 执行摘要（TL;DR）

1. **继续走“语义重绘”，不要转向端到端截图转 SVG。** StarVector/OmniSVG 在图标、logo、图表、字体、emoji 上很强，但官方明确说 StarVector 不适用于自然图像/插画；它们没有证明能精确重建整张 UI 截图的文字基线、面板圆角和 1px 边框。SliDer 是最接近“语义 derender”的工作，但目标是幻灯片，代码截至写作仍“coming soon”。
2. **端到端 VLM 只做“辅助识别”，不做几何引擎。** 可用于图标语义描述、低置信文本复核、给图标生成 alt text；所有坐标、字号、基线仍由 OCR + CV + 字体度量决定。
3. **UI 元素检测的实用阶梯是：辅助功能树 > 经典 CV > 小检测器 > VLM grounding。** 截图本身没有 A11y 树；如果能拿到 macOS AX / Windows UIA，精度和成本都远好于视觉模型。纯截图场景，OmniParser v2 的 `icon_detect_v3`（MIT）+ Florence-2（MIT）是当前最实用的离线组合；UI-TARS 7B 精度更高但太重、太慢，不适合作为默认。
4. **OCR 默认双后端：macOS 用 Apple Vision，其他平台用 RapidOCR（ONNX）。** PaddleOCR 作为高精度可选后端；Surya 2 的权重是 modified OpenRAIL（营收/融资 <$5M 才免费），不要默认采用；dots.ocr/GOT/PaddleOCR-VL 是文档解析模型，不是 UI 行级 OCR 的首选。
5. **字体识别不要迷信 DeepFont。** DeepFont 是 2015 年 Adobe 数据集上的 2,383 类分类器，未找到官方可部署权重；FontCLIP 偏语义检索；VLM 字体识别在基准上表现很差。现有“候选字体渲染 + 墨迹掩码 IoU + 字宽/字重投票”更可控，应继续做，并补 per-run（中英混排）和人工覆盖。
6. **文本默认可选中。** 输出 `<text>` + 显式基线 + font stack；只有用户显式要求“跨平台完全一致”时才 `--text=path`。嵌入字体只允许 OFL/Apache（Noto/Source Han/Inter/Roboto），不要嵌入 PingFang、SF Pro、微软雅黑；转曲也不是规避字体许可的万能钥匙。
7. **图标吸附是“加分项”，不是“真相”。** Lucide（ISC）、Tabler（MIT）、Material Symbols（Apache-2.0）可作为本地索引；匹配必须带置信度，低置信度回退到 vtracer 描摹。SF Symbols 只适合 Apple 平台内使用，不能把其资产打进跨平台 SVG 导出。
8. **照片/头像按项目要求默认 `--photos=embed`，但必须提供一键隐私模式。** 嵌入 crop 会把原始像素重新引入 SVG，EXIF 可去、人脸/二维码/证件信息不会自动消失；提供 `placeholder / blur / scrub`，并在导出前做 PII/人脸/二维码检查和明确提示。
9. **评估不要只看 SSIM。** 用 CER/WER、行/面板 IoU、基线/字号误差、颜色 ΔE00、LPIPS/DinoScore、SVG 体积与可编辑性、隐私泄漏检查的组合；自建合成 UI benchmark 是必须的，因为**没有公开的 UI 截图 → SVG 基准**。
10. **本地 App 先 Python 壳，后原生壳。** v1 用 PySide6（LGPL，动态链接）或 Tauri 2 + Python sidecar，复用现有 CV 管线；把 **Scene JSON + CLI** 固化为契约。v2 再做 SwiftUI + Vision 的 macOS 壳、WinUI 3 + Windows OCR 的 Windows 壳；Linux 保留 CLI。不要一开始重写全部 CV。
11. **避免的坑：** potrace（GPL-2.0）、SVGcode（GPL）、Nuitka（AGPL）、Surya 权重（OpenRAIL 营收限制）、OmniSVG 数据集的 CC BY-NC-SA、SF Symbols 跨平台打包、把“去像素隐写”说成“匿名化”。
12. **一句话：** 用“OCR + 几何 + 字体 + 图标库 + 可选嵌入照片”的可编辑 scene graph 做产品；把 VLM/端到端 SVG 当研究雷达，不当默认管线。

## 1. 对本项目的具体建议（Adopt / Avoid / Maybe）

| 决策 | 建议 | 理由 |
|---|---|---|
| 核心架构 | **Adopt**：保留 scene JSON 作为唯一中间表示；flat/wireframe 只是渲染模式 | 同一几何可多风格导出；避免为每种模式重跑 OCR/CV |
| 端到端 SVG | **Avoid**：不要把 StarVector/OmniSVG 作为整图转换器 | 官方定位是图标/logo/图表/字体/emoji；UI 精度与可编辑性不足 |
| UI 检测 | **Adopt**：OCR + 面板连通域 + 残差；**Maybe**：OmniParser v3 只做图标框/描述 | 轻量、可控；VLM grounding 对精确 bbox 不稳 |
| A11y | **Maybe**：增加“带辅助功能树捕获”模式 | 有树时坐标/文本/角色几乎免费；但需要权限，且只对活着的应用有效 |
| OCR（macOS） | **Adopt**：Vision，`zh-Hans` + `en-US`，关闭语言纠正做对比 | 系统内置、离线、中文 UI 实际表现好；无公开 UI 基准，需自测 |
| OCR（跨平台） | **Adopt**：RapidOCR + ONNX Runtime | Apache-2.0、模型小、CPU 快、部署简单 |
| OCR（高精度可选） | **Maybe**：PaddleOCR / PaddleOCR-VL | Apache-2.0、中文强；Paddle 依赖重，VLM 版偏文档 |
| OCR（避免默认） | **Avoid**：Surya 2 权重、dots.ocr/GOT 作为 UI 行级 OCR | 前者许可有营收限制；后两者是文档 VLM，输出生成式、可能改写文本 |
| 字体识别 | **Adopt**：候选字体渲染 + 墨迹 IoU/字宽/字重；**Avoid**：DeepFont 现成权重 | DeepFont 未找到官方可部署权重；模板匹配更可控 |
| 字体嵌入 | **Adopt**：OFL/Apache 字体 subset；**Avoid**：PingFang/SF/微软雅黑 | 许可风险；系统字体不可再分发 |
| 文本输出 | **Adopt**：默认 `<text>`；**Maybe**：`--text=path`；**Avoid**：默认转曲 | 可选中/可搜索/可编辑优先；转曲只用于归档一致性 |
| 图标 | **Adopt**：Lucide/Tabler/Material 本地索引 + 置信度 + 回退描摹 | 体积小、可编辑、许可清晰；错误吸附会改变语义 |
| Emoji | **Maybe**：Twemoji/Noto Emoji 匹配；**Avoid**：Apple emoji 资产 | 许可与跨平台一致性 |
| 照片 | **Adopt**：默认 `--photos=embed`（项目要求）+ `placeholder/blur/scrub` 隐私模式 + 导出前检查 | 满足“恢复照片”需求，同时让用户能一键降级 |
| flat/wireframe | **Adopt**：同一 scene graph 的两种 style token | 线框模式还能降低颜色指纹 |
| 评估 | **Adopt**：合成 UI benchmark + 真机小集；**Avoid**：SSIM 单指标 | 文字/布局/可编辑性/隐私都要测 |
| 本地 App | **Adopt**：v1 PySide6 或 Tauri 2 + Python sidecar；v2 原生壳 | 先复用 Python CV，再逐步原生化；默认不用云 OCR/VLM |

## 2. 工具/模型对比表

> 许可列是“写作时检索到的”：`MIT/Apache/OFL/ISC/CC0` 通常商业友好；`GPL/AGPL/OpenRAIL/CC BY-NC-SA` 需谨慎。模型权重与代码可能不同许可，表中尽量分开写。

### 2.1 矢量化 / SVG 生成 / 截图转代码

| 名称 | 类型/能力 | 离线/部署 | 许可（代码/权重） | 本项目结论 |
|---|---|---|---|---|
| [vtracer](https://github.com/visioncortex/vtracer) | 彩色分层位图描摹，Rust/C++/Python/Node | 完全离线，轻 | MIT | **采用**：图标/logo 局部描摹、无字体依赖的 fallback |
| [potrace](https://potrace.sourceforge.net/) | 单色位图描摹，经典 | 离线，轻 | GPL-2.0 | **避免**闭源分发；可用 vtracer 替代 |
| [diffvg](https://github.com/BachiLi/diffvg) | 可微矢量光栅化/优化 | GPU 研究 | Apache-2.0 | 研究参考，不做产品默认 |
| [LIVE](https://github.com/Picsart-AI-Research/LIVE-Layerwise-Image-Vectorization) | 分层图像矢量化（CVPR 2022） | 需 PyTorch/GPU | Apache-2.0 | 适合插画/照片分层，不适合 UI 文本 |
| [StarVector](https://github.com/joanrod/star-vector) | VLM 直接生成 SVG 代码；1B/8B；SVG-Bench | 需 GPU/较大显存 | Apache-2.0（代码/权重） | **不做整图**；可研究图标/图表；官方说不适用于自然图像 |
| [OmniSVG](https://github.com/OmniSVG/OmniSVG) | 端到端多模态 SVG 生成（NeurIPS 2025） | 需 GPU | Apache-2.0；MMSVG 数据集 CC BY-NC-SA-4.0 | 研究雷达；不作为 UI 几何引擎 |
| [SliDer](https://github.com/adamhazimeh/SliDer) | VLM 幻灯片 derender → 可编辑 SVG（AAAI 2026） | 代码“coming soon” | Apache-2.0 | **最接近的架构参考**；等代码，先借鉴“元素检测 + 属性 + SVG”流程 |
| [SVGcode](https://svgco.de/) | PWA 位图转 SVG | 浏览器/离线 PWA | GPL | 不直接复用；可参考交互 |
| [Vectorizer.AI](https://vectorizer.ai/) | 商业位图矢量化 | 云 | 专有 | 不做默认；可做效果对照 |
| [screenshot-to-code](https://github.com/abi/screenshot-to-code) | LLM 截图 → HTML/Tailwind/React | 云模型/需 API | MIT（代码） | 语义强、几何弱、隐私不可控；不做几何引擎 |

### 2.2 UI 元素检测 / 屏幕解析

| 名称 | 类型/能力 | 离线可行性 | 许可 | 本项目结论 |
|---|---|---|---|---|
| [UIED](https://github.com/MulongXie/UIED) | 经典 CV：文本/图像/容器检测 | 很轻，CPU | Apache-2.0 | **可作为 baseline**；对现代圆角/阴影/深色模式偏弱 |
| [OmniParser v2](https://github.com/microsoft/OmniParser) | YOLO 图标检测 + Florence-2 图标描述 | 可离线；CPU 慢、GPU 好 | 代码 CC-BY-4.0（LICENSE；README 徽章写 MIT，存在不一致）；`icon_detect_v3`+`icon_caption` 权重 MIT；旧 YOLOv8 权重 AGPL-3.0 | **推荐 v3 组合**：只做图标框/描述，不做文字几何 |
| [ScreenAI](https://arxiv.org/abs/2402.04615) | Google UI/信息图 VLM | 未找到官方开放权重（截至检索） | 论文/数据集；代码非官方 | 研究参考；不要假设可部署 |
| [UI-TARS 1.5/2](https://github.com/bytedance/UI-TARS) | 端到端 GUI agent/grounding，7B/72B | 7B 4-bit 可跑，但重 | Apache-2.0 | 不做默认；可选“高精度 grounding”实验 |
| [Florence-2](https://huggingface.co/microsoft/Florence-2-base-ft) | 0.23B/0.77B 通用视觉：检测/grounding/caption/OCR | CPU 可跑，GPU 更稳 | MIT | **采用**：图标 caption、区域描述、辅助分类 |
| [GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) | 开放词表检测 | 可离线；模型较大 | Apache-2.0 | 可做图标/照片区域召回；不如专用 UI 检测器 |
| macOS AX / Windows UIA | 系统辅助功能树：角色、文本、bbox、层级 | 系统 API，零模型 | 系统专有 API | **有则优先**；需权限，仅活应用；OS-Atlas 等 4B/8B 仅研究 |

### 2.3 OCR（中英混排 UI）

| 名称 | 平台/粒度 | 中文/混排 | 离线 | 许可 | 本项目结论 |
|---|---|---|---|---|---|
| [Apple Vision](https://developer.apple.com/documentation/vision/recognizing-text-in-images) | macOS/iOS；行级，可按 range 取框 | 好（`zh-Hans`） | 系统内置 | Apple 系统 API | **macOS 默认**；无公开 UI 基准，自测 |
| [Windows OCR](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine) | Windows；行 + 词框 | 取决于语言包 | 系统内置 | Windows 系统 API | **Windows 默认**；需装 `zh-Hans` OCR 语言包 |
| [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | 全平台；行/词（可选） | 很强；109 语言 | 可离线 | Apache-2.0 | **高精度可选**；依赖较重 |
| [RapidOCR](https://github.com/RapidAI/RapidOCR) | 全平台；ONNX/OpenVINO/MNN | 好；默认中英 | 可离线 | Apache-2.0 | **跨平台默认**；部署最轻 |
| [Surya 2](https://github.com/datalab-to/surya) | 全平台；行/布局/阅读顺序 | 强（文档） | 可离线 | 代码 Apache-2.0；权重 modified OpenRAIL（<$5M 免费） | **不默认**；商业需评估许可/营收 |
| [dots.ocr](https://github.com/studio-dots-ai/dots.ocr) | 1.7B 文档 VLM；布局/文本/表格 | 100+ 语言 | 需 GPU/较重 | MIT | 文档解析实验；不做 UI 行级默认 |
| [GOT-OCR2.0](https://github.com/Ucas-HaoranWei/GOT-OCR2.0) | 580M 通用 OCR VLM | 中英 | 可离线 | Apache-2.0 | 研究/复杂文档；生成式输出需校验 |
| [PaddleOCR-VL](https://huggingface.co/PaddlePaddle/PaddleOCR-VL) | 0.9B 文档 VLM | 109 语言 | 可离线 | Apache-2.0 | 文档解析 SOTA 方向；不是 UI 框级首选 |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) / [EasyOCR](https://github.com/JaidedAI/EasyOCR) | 全平台；词/行 | 一般/可用 | 可离线 | Apache-2.0 | 仅 legacy/低资源 fallback；EasyOCR 比 RapidOCR 重 |

> 关键：**OCR benchmark 的分数不能直接外推到 UI 小字。** OCRBench/CC-OCR/OmniDocBench 主要测文档/自然场景；UI 截图有 2x/3x DPR、细字重、彩色背景、图标误识别、行内混排等独有问题。

### 2.4 字体 / 图标 / Emoji

| 名称 | 类型 | 许可 | 本项目结论 |
|---|---|---|---|
| [DeepFont](https://arxiv.org/abs/1507.03196) | 2015 字体分类（2,383 类，top-5 >80%） | 论文；未找到官方可部署权重 | 研究参考；不采用 |
| [FontCLIP](https://github.com/yukistavailable/FontCLIP) | 字体-语义 CLIP，检索/属性 | MIT | 可用于“字体风格搜索”，不用于精确识别 |
| [Font recognition VLM 基准](https://arxiv.org/abs/2503.23768) | 评测 VLM 字体识别 | 论文 | 结论：VLM 字体识别弱，不要依赖 |
| [Lens](https://github.com/mixfont/lens) | 开源字体识别模型（宣称 1000+ 字体） | 仓库存在；权重/代码可验证性不足 | 保持观察；未验证前不集成 |
| [Lucide](https://github.com/lucide-icons/lucide) | 1,000+ 线性图标 | ISC | **首选**通用线性图标索引 |
| [Material Symbols](https://developers.google.com/fonts/docs/material_symbols) | Google 图标（可变字体/SVG） | Apache-2.0 | **采用**；适合 Android/Material 风格 |
| [SF Symbols](https://developer.apple.com/design/human-interface-guidelines/sf-symbols) | Apple 系统符号 | Apple 系统资产许可；仅 Apple 平台 | **仅 macOS/iOS App 内**；不跨平台打包/导出 |
| [Font Awesome Free](https://fontawesome.com/license/free) | 图标/字体 | 图标 CC BY 4.0；字体 OFL 1.1；代码 MIT | 可用但需 attribution；注意品牌商标 |
| [Simple Icons](https://github.com/simple-icons/simple-icons) | 品牌图标 | CC0（商标另计） | 品牌 logo 可参考；不要自动吸附误判 |
| [Tabler Icons](https://github.com/tabler/tabler-icons) | 线性图标 | MIT | 可用；与 Lucide 风格接近 |
| [Phosphor](https://github.com/phosphor-icons/phosphor-icons) / [Heroicons](https://github.com/tailwindlabs/heroicons) | 多字重/线性图标 | MIT | 备选 |
| [Twemoji](https://github.com/twitter/twemoji) | Emoji 图形 | 图形 CC BY 4.0；代码 MIT | 可嵌入但需 attribution |
| [OpenMoji](https://openmoji.org/) | Emoji 图形 | CC BY-SA 4.0 | 有 share-alike 传染性；谨慎用于专有输出 |
| [Noto Emoji](https://github.com/googlefonts/noto-emoji) | Emoji 图形 | OFL-1.1 | **较安全**的 emoji 嵌入选择 |

## 3. 截图/UI → SVG 的三条技术路线

### 3.1 范式对比

| 范式 | 代表 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| 位图描摹 | vtracer、potrace、Vectorizer.AI | 工程简单、颜色/形状还原好 | 文字变路径、不可编辑；噪点/水印保留；照片路径巨大 | 图标/logo/局部装饰 |
| 语义重绘 | 本原型、SliDer（幻灯片） | 小、可编辑、文本可选中、隐私可控 | 依赖 OCR/字体/图标分类 | **UI 截图主路线** |
| 端到端生成 | StarVector、OmniSVG、LLM screenshot-to-code | 语义强、能“理解” | 坐标/字号/基线不精确；中文小字易错；不可复现；隐私风险 | 图标/图表/灵感，不做精确 UI |

### 3.2 端到端 SOTA 的真实边界

- **StarVector（CVPR 2025）**：把矢量化当代码生成，SVG-Bench 上用 DinoScore/LPIPS 等指标领先；但官方模型卡明确写“**不适用于自然图像或插画**”，强项是图标、logo、技术图、图表、字体、emoji。它的输出可能包含 `<text>`，但没有证据表明能稳定还原 UI 的行级字号/基线。
- **OmniSVG（NeurIPS 2025）**：统一多模态 SVG 生成，覆盖从简单图标到复杂动漫角色；训练数据 MMSVG 是 **CC BY-NC-SA-4.0**（非商业）。同样不是 UI 截图重建基准。
- **SliDer（AAAI 2026）**：目前最接近“语义 derender”的工作——检测/抽取图像与文本元素属性，再组织成 SVG；目标是幻灯片。代码截至写作“coming soon”，不能作为依赖，但它的“元素级属性 + SVG 组装”思路值得借鉴。
- **LIVE/diffvg**：面向插画/照片的分层或可微矢量化；对文字、1px 边框、UI 文本层不友好。
- **评估缺口**：SVG-Bench 覆盖 fonts/icons/emoji/diagrams 等 10 个数据集；SVGenius 覆盖 SVG 理解/编辑/生成 2,377 个查询；但**没有“UI 截图 → 可编辑 SVG”的公开基准**。这是本项目需要自建 benchmark 的根本原因。

### 3.3 工业实践

- Adobe Image Trace、Vectorizer.AI、SVGcode 主要是**位图描摹**：对 logo/插画有效，对 UI 文字不可编辑。
- screenshot-to-code / Design2Code 代表“截图 → HTML”：语义强、几何/字体弱、多数依赖云。设计工具社区的“截图转 Figma/SVG”也多是“分割 + 描摹 + 手工修”。
- **结论**：工业界没有“全自动、像素级、可编辑 UI SVG”的成熟方案；混合管线仍是正确方向。

## 4. UI 元素检测 / 屏幕解析：什么能在笔记本上离线跑

### 4.1 实用阶梯

| 方案 | 坐标精度 | 成本 | 离线 | 适用 |
|---|---|---|---|---|
| 辅助功能树（AX/UIA/DOM） | **最高**（原生 bbox/角色/文本） | 极低 | 是 | 能捕获活应用时；需权限 |
| 经典 CV（UIED/连通域/边缘） | 中高 | 低 | 是 | 面板/容器/按钮 baseline |
| 小检测器（YOLO/Florence-2） | 中高 | 中 | 是 | 图标/可交互区域 |
| VLM grounding（UI-TARS/OS-Atlas） | 中（高分辨率小目标弱） | 高 | 是（7B+） | 实验/兜底，不做默认 |

### 4.2 OmniParser v2：最实用的离线图标检测组合

- 结构：`icon_detect`（YOLO 系列，检测可交互区域）+ `icon_caption`（Florence-2，生成图标功能描述）。
- **许可要看清**：仓库 `LICENSE` 是 **CC-BY-4.0**（需要 attribution），README 徽章写 MIT，存在不一致；官方模型卡说明：
  - `icon_detect`（YOLOv8）权重 **AGPL-3.0**；
  - `icon_caption`（Florence-2）**MIT**；
  - 新增 `icon_detect_v3`（YOLOv9-E 微调）**MIT**，与 `icon_caption` 组合可得到全 MIT 管线。
- 实践建议：只用 `icon_detect_v3` + `icon_caption`，不要用旧 `icon_detect`；把输出当“图标候选框 + 语义描述”，不要把文字框交给它。
- 论文报告：在 ScreenSpot 上，OmniParser 的检测+局部语义显著提升 GPT-4V 的 grounding；但这是“agent 可用性”，不是“bbox 像素精度”。

### 4.3 其他方案

- **UIED**：经典 CV，轻、快、Apache-2.0；适合做 baseline 和“容器/文本/图像”粗分。对圆角、阴影、深色模式、复杂背景偏弱。
- **ScreenAI**：Google 的 UI/信息图 VLM，论文与 Screen Annotation/ScreenQA 数据集有价值；截至检索**未找到官方开放权重**，不要把它当可部署方案。
- **UI-TARS 1.5/2**：7B 级 GUI grounding，ScreenSpot-Pro 上 1.5-7B 报告 49.6（官方博客/榜单口径）；精度强于小模型，但显存/延迟不适合“每张截图都跑”。可作为“疑难图标/高分辨率”实验后端。
- **Florence-2**：0.23B/0.77B，MIT，支持 caption/OD/grounding/OCR；适合做图标 caption、区域分类、低置信框复核。
- **GroundingDINO**：开放词表检测，Apache-2.0；可做“找头像/找二维码/找图片区域”的召回，但对 UI 小图标不如专用检测器。
- **OS-Atlas**：4B/8B GUI grounding，Apache-2.0；研究/实验，不作为默认。
- **A11y 树**：macOS `AXUIElement`、Windows UI Automation 能给出角色、标题、值、bbox；如果产品能“截图时同时读取当前前台应用”，这是最高性价比的增强。注意权限弹窗、隐私（树里可能有未显示文本）和跨应用兼容性。

### 4.4 建议

1. 主管线仍用 **OCR 行框 + 颜色连通域 + 残差**，不要用 VLM 出几何。
2. 增加一个可选 `--icon-detect=omniparser`：只输出图标候选框和 caption，合并到 `IconBlock`，不覆盖 OCR 文本。
3. 增加 `--a11y` 捕获模式（macOS AX/Windows UIA）：有树时用树校正文本/角色/bbox；无树时回退视觉。
4. 对高分辨率桌面截图，先按 DPR 降采样到逻辑像素，再跑检测；小目标不要直接喂 4K 全图。

## 5. OCR：中英混排 UI 的工程选型

### 5.1 粒度与能力

| 引擎 | 框粒度 | 置信度 | 语言纠正 | 备注 |
|---|---|---|---|---|
| Apple Vision | 行级 `VNRecognizedTextObservation`；可按 range 取子框 | 有 | 可开关 | UI 建议 `usesLanguageCorrection=false` 做对照；中文标点/全角需归一化 |
| Windows OCR | `OcrLine` + `OcrWord` 词框 | 有 | 有限 | 需装 OCR 语言包；词框对混排有用 |
| PaddleOCR | 行级 det+rec；可选词框 | 有 | 可配 | 中文强；Paddle 依赖重，可导出 ONNX |
| RapidOCR | 行级（基于 PP-OCR 模型） | 有 | 可配 | ONNX/OpenVINO/MNN；CPU 快；跨平台首选 |
| Surya 2 | 行/布局/阅读顺序 | 有 | 模型生成 | 文档强；权重许可需评估 |
| dots.ocr / GOT / PaddleOCR-VL | 文档级/区域级 | 生成式 | 无 | 输出可能是 Markdown/LaTeX；不适合精确行框 |

### 5.2 推荐组合

- **macOS**：Vision 默认；`recognitionLanguages = ["zh-Hans", "en-US"]`；`recognitionLevel = .accurate`；对低置信行用 RapidOCR 交叉验证（可选）。
- **Windows**：Windows OCR 默认；检查 `OcrEngine.AvailableRecognizerLanguages`，提示用户安装 `zh-Hans` 语言包；失败时回退 RapidOCR。
- **Linux CLI**：RapidOCR（ONNX Runtime）默认；PaddleOCR 作为 `--ocr=paddle` 可选。
- **不默认**：Surya（权重许可/营收限制）、dots.ocr/GOT/PaddleOCR-VL（文档 VLM、生成式、较重）、云 OCR。
- **可选高精度**：PaddleOCR PP-OCRv5/v6；官方技术报告称 <100M 参数模型可对标十亿级 VLM，但这是文档/多语言场景，UI 仍需自测。

### 5.3 UI 特有的坑

1. **OCR 框有 padding**：Vision 行框常比墨迹高 15–40%；字号必须用墨迹框/字宽反推，不能直接用框高。
2. **中英混排行框被拉高**：英文 ascender/descender 与 CJK em box 不同；要 per-run 拟合，不能整行一个字号。
3. **语言纠正会“改写”UI 文本**：漏字母、数字串/按钮标签错误应保留并给人工校正入口，不要自动猜。
4. **图标误识别/低分辨率**：短 ASCII/符号行可能是图标；用置信度 + 字体渲染失败 + 颜色多样性过滤；先降采样到逻辑像素再 OCR。
5. **Emoji 不归 OCR / VLM 不做最终文本**：走颜色连通域 + emoji 匹配；VLM 生成式 OCR 可能补全/改写，必须字符级 diff + 人工确认。

### 5.4 OCR 评估

- 组件级：OCRBench（1,000 QA）、OCRBench v2（10,000 QA，31 场景）、CC-OCR（中英/多语言/文档）、OmniDocBench（PDF 文档）。
- UI 级：ScreenQA（86K QA over RICO）可测“屏幕阅读理解”，但不测行框/字号；ScreenSpot/ScreenSpot-Pro 测 grounding，不测 OCR。
- **建议**：自建 200–500 张 UI 截图，人工标注行文本 + 墨迹框 + 字体，统计 CER/WER、行框 IoU、中心误差、置信度校准；不要用文档 benchmark 代替。

## 6. 字体识别、字号/基线、跨平台还原

### 6.1 字体识别 SOTA 与工程现实

| 方法 | 能力 | 问题 | 建议 |
|---|---|---|---|
| DeepFont（2015） | 2,383 字体分类，top-5 >80%（AdobeVFR） | 老、未找到官方权重、偏印刷/自然图像、不覆盖 UI 系统字体 | 不采用；作为 baseline 引用 |
| FontCLIP（2024） | 字体-语义检索、属性 | 不是精确字体 ID；跨语言有限 | 用于“相似字体推荐” |
| VLM 字体识别（2025 基准） | 直接问 VLM“这是什么字体” | 基准显示 VLM 容易被文本语义干扰、准确率低 | 不依赖 |
| Lens（开源宣称） | 图像 → 最相似开源字体 | 权重/代码可验证性不足（社区检索） | 观察，未验证不集成 |
| **候选渲染匹配（本原型）** | 用本地字体库渲染候选，比较墨迹掩码/字宽/字重 | 受 OCR 文本错误、抗锯齿、字号估计影响 | **继续做**；加 per-run、置信度、人工覆盖 |

**工程建议**：
- 维护一个“候选字体库”：Noto Sans SC、Source Han Sans、Inter、Roboto、系统字体（PingFang SC、Microsoft YaHei、Segoe UI）仅用于**渲染比较**，不嵌入。
- 特征：墨迹 mask IoU、字宽/advance、x-height/cap-height、笔画粗细、字重密度、标点形状。
- 评分：`score = w1*IoU + w2*width_err + w3*weight_err - w4*density_penalty`；整图按像素数加权投票，但允许每行覆盖。
- 对 CJK：汉字 advance 基本都是 1em，**不能用字宽区分字体**；必须看字形 mask/笔画。
- 对英文：可用 `a/g/y` 等区分性字形、x-height、字重；但 UI 小字抗锯齿会糊，需 2x/3x 渲染再降采样比较。
- 置信度低时不要“猜字体”，回退到通用 font stack，并在编辑器中提示。

### 6.2 字号与基线

- **字号**：用墨迹宽度/高度拟合，不用 OCR 框高。对 CJK，字宽 ≈ 1em 很稳；对英文，先 strip 空格，再用候选字体 `getbbox(text)` 在 100px 下线性缩放。
- **基线**：`baseline = ink_bottom - (bbox_bottom - ascent)`；`ascent` 来自字体 `getmetrics()`；不要用 `dominant-baseline` 赌渲染器。
- **多 run**：按脚本/字体/字重切 run；每个 run 独立字号/基线/颜色；同一行共享 baseline（除非上标/下标）。
- **亚像素**：在 2x/3x 渲染后比较，再降采样；直接 1x 比较会被 hinting/抗锯齿误导。
- **行高/字距**：OCR 已给行位置，不要重新排版；英文 tracking 可用 `letter-spacing` 拟合；中文一般 0。
- **兜底**：宽度偏差 >2% 才考虑 `textLength`/`lengthAdjust="spacing"`；不要拉伸 CJK。

### 6.3 输出模式与许可

| 模式 | 可选中/可搜索 | 跨平台一致 | 体积 | 许可 | 建议 |
|---|---|---|---|---|---|
| `<text>` + font stack | ✅ | 低（依赖查看器字体） | 最小 | 无额外 | **默认** |
| `<text>` + 嵌入 subset WOFF2 | ✅ | 中高 | 中（几十 KB） | 字体必须允许嵌入/再分发 | 仅 OFL/Apache；`--embed-font` |
| 文本转 `<path>` | ❌ | 最高 | 大 | 仍受字体许可约束 | `--text=path`，归档/印刷用 |
| 位图文字 | ❌ | 最高 | 大 | 隐私/体积差 | 不推荐 |

- OFL FAQ 明确：OFL 字体可嵌入文档/子集，文档本身不受 OFL 影响；但**字体文件再分发**需带版权/许可，修改后需遵守 Reserved Font Name。见 [OFL FAQ](https://openfontlicense.org/ofl-faq/)。
- **不要嵌入**：PingFang SC（Apple 系统字体）、SF Pro（Apple 字体许可，仅限 Apple 平台）、Microsoft YaHei（Windows 系统字体，需商业授权）。转曲也不等于合法：很多 EULA 同样限制 outlines 的再分发；不要把它当规避手段。
- **可用**：Noto Sans SC/CJK（OFL）、Source Han Sans（OFL）、Inter（OFL）、Roboto（Apache-2.0）。保留 `LICENSE`/版权声明；subset 用 `fonttools`。
- SVG 细节：显式 `x`/`y`（baseline）、`xml:space="preserve"`、`font-family` 栈、`font-size` px；避免依赖 `dominant-baseline`（跨浏览器 baseline table 选择不一致，见 [MDN](https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/dominant-baseline)）。
- 混合语言 font stack 示例：`-apple-system, "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif`；但“确定性导出”应嵌入 Noto 或转 path。

### 6.4 建议

1. 继续“候选渲染 + 墨迹匹配”，把它做成可解释的 `font_candidates[]`（family/weight/score），不要只给一个答案。
2. 增加 per-run 拟合，解决中英混排/标题正文混用。
3. 默认 `<text>`；`--embed-font` 只允许 OFL/Apache 白名单；`--text=path` 显式开启。
4. 在 UI 中显示“字体不确定”标记，允许用户改 family/weight/size/baseline；比自动猜更有用。

## 7. 图标/emoji 识别、吸附与照片隐私

### 7.1 图标库选择

| 库 | 风格 | 许可 | 适用 |
|---|---|---|---|
| Lucide | 线性、24px 网格 | ISC | 通用 UI 首选 |
| Tabler | 线性 | MIT | 通用/后台 |
| Material Symbols | 多风格可变字体 | Apache-2.0 | Android/Material |
| SF Symbols | Apple 系统符号 | Apple 系统资产许可 | 仅 Apple 平台 App；不跨平台导出 |
| Font Awesome Free | 实心/线性 | 图标 CC BY 4.0；字体 OFL 1.1；代码 MIT | 需 attribution；品牌图标注意商标 |
| Simple Icons | 品牌 logo | CC0（商标另计） | 品牌识别；不要自动替换 |
| Phosphor / Heroicons | 多字重/线性 | MIT | 备选 |

### 7.2 识别/吸附管线

1. **检测**：残差/颜色连通域 + 可选 OmniParser `icon_detect_v3`；过滤文字框内区域。
2. **归一化/特征**：裁切去背景 → 24×24/48×48；silhouette IoU、distance-transform、stroke width、corner radius、fill/outline、颜色多样性；可选 CLIP/DINOv2 语义召回。
3. **匹配/回退**：本地库 top-k；低分不吸附，回退 vtracer；保留原颜色，stroke width 按目标尺寸缩放；编辑器显示置信度和“还原描摹”。

**风险**：品牌 logo 不自动吸附（商标/版权）；fill/outline 作为硬约束；1x 小图信息不足，阈值要保守。

### 7.3 Emoji

- Emoji 不会被 OCR 识别；用颜色连通域 + 形状/颜色特征，或直接用 emoji 图像库做 pHash/CLIP 最近邻。
- 可嵌入库：**Twemoji**（图形 CC BY 4.0，需 attribution）、**Noto Emoji**（OFL-1.1，较安全）、OpenMoji（CC BY-SA 4.0，share-alike 需谨慎）。
- **不要嵌入 Apple emoji 资产**（系统字体/图像，不可再分发）。
- 识别不确定时：保留描摹路径，或输出占位 + 让用户选 emoji；不要猜一个语义相近但错误的 emoji。

### 7.4 照片/头像：嵌入 crop 的隐私设计

用户要求“照片/头像恢复为嵌入 crop”，这会重新引入栅格。建议把它设计成显式、可审计的模式：

| 模式 | 行为 | 隐私 | 适用 |
|---|---|---|---|
| `placeholder` | 纯色块/简单 glyph | 最高 | 分享、公开、隐私模式 |
| `blur` | 裁切 + 高斯/像素化 + 嵌入 | 中 | 保留构图，隐藏身份 |
| `redact` | 人脸/二维码/证件区域打码后嵌入 | 中高 | 有敏感人物/码 |
| `embed` | 裁切原图（可降采样/重编码）嵌入 | 低 | 用户明确要还原 |
| `trace` | 矢量化照片 | 低（仍可辨识） | 插画/扁平图，不适合真人照片 |

**管线建议**：
1. 从**原图**裁切（不是从已量化/flatten 的图），按逻辑像素/目标显示尺寸降采样；去 EXIF/XMP/IPTC/ICC，重编码 PNG/WebP/JPEG；不写原文件名/路径。
2. 圆角/圆形用 `<clipPath>`；`<image>` base64 内嵌，不用外部 URL。
3. 嵌入前跑人脸/二维码/条形码/PII OCR；命中提示 `redact/blur/placeholder`。输出前扫描 `<image>` 数量、data URI 大小、EXIF、外部引用、原文件名、零宽字符，生成“隐私报告”。
4. **“去像素隐写” ≠ “匿名化”**；嵌入照片会把原始像素带回来，文本/姓名/订单号仍可见；照片版权/肖像权由用户负责。

**建议默认**：按项目要求默认 `embed`，但把 `placeholder/blur/scrub` 放在同一显眼位置；默认开启人脸/二维码检查，导出前给“含原始照片像素”的明确提示。这样满足“恢复照片”需求，也保留隐私降级路径。

## 8. 评估：指标、基准与自建方案

### 8.1 为什么不能只看 SSIM

- SSIM/PSNR 对“文字错一个字但形状相似”不敏感；对“面板位置偏 2px”也不敏感。
- StarVector 论文批评像素指标（MSE）不能捕捉矢量图形的语义/可编辑性，提出 DinoScore（DINOv2 特征距离）并配合 LPIPS/SSIM。UI 重建的核心是“文本对、布局对、可编辑、隐私”，照片占位会拉低 SSIM 但不代表失败。

### 8.2 指标矩阵

| 维度 | 指标 | 说明 |
|---|---|---|
| 文本 | CER（中文）、WER（英文）、归一化编辑距离、精确匹配 | 按行/按字符；保留 OCR 原始错误 vs 人工校正两套 |
| 文本几何 | 行框 IoU、中心误差、基线误差、字号相对误差、颜色 ΔE00 | 用人工标注/合成 ground truth |
| 布局 | 面板/容器 IoU、GIoU、边界 F1、元素检测 mAP@0.5 | 区分 text/icon/panel/photo |
| 视觉 | SSIM、MS-SSIM、LPIPS、DinoScore、CLIP | 在“非照片 mask”上单独算；照片模式另算 |
| 矢量质量 | SVG 体积、节点数、路径点数、text 节点占比、4x 重渲染稳定性、XML 有效性 | 可编辑性代理指标 |
| 隐私 | `<image>` 数、外部引用、EXIF/XMP、原文件名、零宽字符、PII 命中 | 每次导出自动跑 |
| 性能 | 端到端延迟、峰值内存、模型体积、冷启动 | 分 OCR/检测/渲染 |
| 人工 |  pairwise 偏好、可编辑性 Likert、文本可读性 | Design2Code 式人工评估 |

### 8.3 可借鉴的公开基准（以及它们不测什么）

| 基准 | 测什么 | 不测什么 |
|---|---|---|
| [Design2Code](https://arxiv.org/abs/2403.03163) | 484 个网页截图 → HTML；CLIP + block match + text/position/color | 不测 SVG、不测字体/基线 |
| [WebSight](https://huggingface.co/datasets/HuggingFaceM4/WebSight) | 823k 合成英文网页截图/HTML | 不测中文、不测 SVG、不测 UI 小字 |
| [ScreenQA](https://arxiv.org/abs/2209.08199) | 86K 移动屏问答 | 不测重建几何 |
| [ScreenSpot](https://arxiv.org/abs/2401.10935) / [ScreenSpot-Pro](https://arxiv.org/abs/2504.07981) | GUI grounding（指令 → 元素） | 不测 OCR/重建/可编辑性 |
| [SVG-Bench](https://starvector.github.io/) | 图标/字体/emoji/图表等 image-to-SVG；DinoScore/LPIPS/SSIM | 不测 UI 截图、不测文本可选中 |
| [SVGenius](https://arxiv.org/abs/2506.03139) | SVG 理解/编辑/生成，2,377 查询 | 不测截图重建 |
| [OCRBench v2](https://arxiv.org/abs/2501.00321) / [CC-OCR](https://arxiv.org/abs/2412.02210) | 文档/自然场景 OCR | 不测 UI 行框/字体 |
| [OmniDocBench](https://arxiv.org/abs/2412.07626) | PDF 文档解析 | 不测 UI |

**结论**：没有一个公开基准同时覆盖“UI 截图 + 中文 + 文本可选中 + 字体/基线 + 隐私”。必须自建。

### 8.4 自建 benchmark 设计

1. **合成集（主）**：固定 HTML/CSS/Flutter 渲染 300–500 张；记录 DOM/Widget bbox、文本、字体 family/size/weight/color、圆角、层级；覆盖中英混排/纯中文/数字、浅深色、2x/3x DPR、JPEG/PNG、抖动/最近邻放大、头像/照片/emoji/线性与实心图标、长文本/折行/省略号。
2. **真机集（辅）**：100–200 张真实 App 截图，人工标注行文本、墨迹框、字体候选、面板；覆盖 iOS/Android/macOS/Windows/Web。
3. **协议/Baseline/消融**：固定渲染器（resvg/Chromium）、字体包、DPR；训练/调参/测试分离；报告 median/IQR + 分品类。Baseline：当前原型、vtracer 全图、OCR+矩形、StarVector（仅图标子集）、SliDer（若可用）；消融去抖/调色板/墨迹字号/per-run 字体/图标吸附/照片模式。
4. **隐私测试**：`placeholder/blur/scrub` 模式不得出现 `<image>`/外部引用/EXIF/原文件名；默认 `embed` 必须带检查与警告。当前原型 README 基线（1.4px/3.8px 中心误差、0.964/0.952 文本相似度）只作 regression baseline，不是 SOTA 声明。

## 9. 本地离线应用架构（macOS / Windows / Linux）

### 9.1 方案对比

| 方案 | 优点 | 缺点 | 许可/分发 | 适用 |
|---|---|---|---|---|
| **SwiftUI + Vision + Core ML** | 最小、最快、系统 OCR/人脸/二维码、能效好 | macOS only；CV 需重写/桥接 | Apple 框架；Developer ID + notarization | 原生 macOS 终态 |
| **WinUI 3 + Windows OCR + ONNX Runtime** | 原生 Windows、MSIX、系统 OCR | 语言包依赖；OCR 质量参差 | Windows App SDK；Authenticode/MSIX | 原生 Windows 终态 |
| **Tauri 2 + Python sidecar** | 小壳、跨平台、复用 Python；Web UI 灵活 | 双运行时；sidecar 体积；WebView 差异 | MIT/Apache；签名/公证 | v1 跨平台 App |
| **Qt 6 / PySide6** | 单 Python 栈、跨平台、原生控件、Linux 友好 | 打包大；LGPL 合规需动态链接 | LGPLv3/GPL/商业 | **v1 推荐**（最快复用 CV） |
| **Avalonia/.NET** | 跨平台、C#、MIT | CV/OCR 生态弱，仍需 Python/ONNX | MIT | 备选；Electron/Flutter 仅作 UI 备选 |

### 9.2 推荐路线

- **v1（本地 App）**：Python core + **PySide6** 桌面壳（macOS 优先，Windows/Linux 同栈），保留 `distill-svg` CLI；现有算法/依赖全在 Python，PySide6 LGPL 可动态链接，开发最快。若 UI 设计/安装包体积是硬指标，改用 **Tauri 2 + Python sidecar**（Rust 壳 + Web UI，stdin/stdout JSON 或本地 HTTP）。
- **契约**：**Scene JSON schema** 版本化（`schema_version`），CLI 支持 `--scene in.json --out out.svg`；App 只依赖契约，不依赖 Python 内部函数。
- **v2（原生壳）**：macOS 用 SwiftUI + Vision + Core ML；Windows 用 WinUI 3 + Windows OCR + ONNX Runtime；Linux 保留 CLI（RapidOCR ONNX）。复杂几何仍可调用 Python sidecar，再按热点（OCR 后处理、图标匹配）逐步原生化。
- **不要**一开始把 CV 全重写为 Swift/C#；先用“原生 UI + Python 核心”交付。

### 9.3 打包、签名、许可

- **macOS**：Developer ID 签名 + Hardened Runtime + 公证（notarytool）+ staple；直接分发优先于 Mac App Store。App Sandbox 与 Python sidecar/子进程可能冲突，需实测；Mac App Store 对嵌入解释器/可执行文件有额外限制，不要假设能过审。
- **Windows**：WinUI 3/MSIX 或传统 installer；Authenticode 签名 + 时间戳；SmartScreen 需声誉积累；OCR 语言包按需提示安装。**Linux**：CLI 用 `pipx`/`uv`/独立二进制；GUI 可选 AppImage/Flatpak。**更新**：签名 manifest，关闭自动下载模型。
- **许可雷区**：GPL/AGPL（potrace、SVGcode、Nuitka、Poppler、Ghostscript、PyMuPDF）闭源分发需法务评估；PyInstaller 是 GPL-2.0-or-later **with Bootloader Exception**；Qt/PySide6 是 LGPLv3（动态链接、附许可、可替换库），静态链接考虑商业许可。
- **权重/数据/字体/图标**：Surya 权重 modified OpenRAIL（营收/融资 <$5M 免费）；OmniParser 旧 YOLOv8 权重 AGPL；OmniSVG 数据集 CC BY-NC-SA；SF Symbols 仅 Apple 平台；PingFang/SF/微软雅黑不可再分发；Font Awesome 图标 CC BY 4.0；OpenMoji CC BY-SA；Twemoji CC BY 4.0；Noto Emoji OFL。RICO/WebSight/Design2Code 用于评测/训练前先看 license。

### 9.4 隐私架构

- 默认**零网络**：不遥测、不崩溃上传、不自动下载模型；模型随包/离线包分发，SHA256 校验。
- 沙箱/最小权限：只读用户选择的文件；截图捕获用 ScreenCaptureKit（macOS）/Windows Graphics Capture，明确权限提示。
- 临时文件：App 容器内、处理完删除；导出前扫描 PII/EXIF/外部引用；不写原文件名/路径。照片默认 embed（项目要求），但 `placeholder/blur/scrub` 一键可用，嵌入前做人脸/二维码检查 + 预览确认。
- 透明：输出“隐私报告”（是否含 `<image>`、是否有 EXIF、是否命中 PII 正则）；明确“不是匿名化”。

## 10. 三阶段路线图（对齐 flat/wireframe、照片、可选中文本、原生 App）

### Phase 1（0–3 个月）：本地可用 MVP

**目标**：macOS 本地 App + Linux CLI；flat/wireframe/彩色三模式；文本可选中；照片可选嵌入；可重复 benchmark。

- 冻结 **Scene JSON v1**（panels/text runs/icons/images/style）；`--style flat|wireframe|color` 同一 scene graph 渲染；wireframe 只描边/黑字/隐藏照片。
- `--photos=embed|placeholder|blur|scrub`：默认 embed（项目要求）；从原图裁切、去 EXIF、重编码、base64；人脸/二维码检测 + 警告；隐私模式一键降级。
- 文本默认 `<text>` + per-run 字体/字号/基线；`--embed-font` 只允许 OFL/Apache 白名单；`--text=path` 可选。
- OCR：Vision（macOS）+ RapidOCR（跨平台）双后端；图标：Lucide/Tabler/Material + 置信度 + vtracer 回退；SF Symbols 不跨平台。
- 评估：合成 benchmark v0（100–200 张）+ 指标脚本（CER/行框 IoU/面板 IoU/LPIPS/体积/隐私）；打包 PySide6（或 Tauri 2 + sidecar）+ macOS 公证 + Linux CLI。
- **验收**：默认导出无 `<image>`/外部引用；wireframe 可读；文本可复制；照片 embed 有明确开关；benchmark 有 baseline 数字。

### Phase 2（3–6 个月）：原生 macOS/Windows + 质量

**目标**：原生壳、系统 OCR、隐私增强、编辑器。

- macOS SwiftUI + Vision + Core ML；Windows WinUI 3 + Windows OCR + ONNX Runtime；共享 Scene JSON/CLI。
- 可选 `--a11y`（macOS AX/Windows UIA）；可选 OmniParser `icon_detect_v3` + Florence-2 caption（MIT 组合，只做图标候选）。
- 字体 `font_candidates[]` + per-run 置信度 + 人工覆盖；照片人脸/二维码/证件 redact + PII 扫描 + 隐私报告。
- 编辑器：改文字、拖框、换字体、切换模式、还原描摹/吸附；评估扩到 300–500 张 + 真机集 + 回归门禁。
- **验收**：安装包可签名分发；OCR 语言包缺失有提示；照片模式默认安全；benchmark 覆盖中英混排/深色/2x/JPEG。

### Phase 3（6–12 个月）：规模化与前沿试点

**目标**：跨平台稳定、图标/emoji 识别、可选本地 VLM、生态。

- 图标/emoji：silhouette + embedding 混合；低置信度回退；品牌 logo 不自动吸附；字体小型分类器（可选）与模板匹配融合。
- 可选本地 VLM：只用于图标 caption/低置信文本复核/alt text；默认关闭；输出必须人工确认。
- 批量/目录/watch、CLI 管道、插件 API、Linux 包；基准扩到真实 App/高分辨率/RTL/多语言；隐私审计（SBOM、许可清单、输出扫描、渗透测试）。
- **验收**：默认管线不依赖大模型；VLM 是可选增强；benchmark 有统计显著提升；分发合规。

## 11. 前沿 vs 实用（一页看清）

| 方向 | 前沿（2023–2026） | 现在能落地 | 本项目选择 |
|---|---|---|---|
| 截图 → SVG | StarVector、OmniSVG、SliDer、LIVE/diffvg | 语义 scene graph + vtracer 局部描摹 | **语义重绘**；端到端只做研究 |
| UI 解析 | ScreenAI、UI-TARS、OS-Atlas、OmniParser | UIED/连通域 + OmniParser v3 + A11y | OCR+CV 主，OmniParser 辅 |
| OCR | dots.ocr、GOT、PaddleOCR-VL、Surya 2 | Vision / Windows OCR / RapidOCR / PaddleOCR | 双后端 + 可选 Paddle |
| 字体 | FontCLIP、VLM 字体识别、Lens | 候选渲染 + 墨迹匹配 | **模板匹配**；不做 DeepFont |
| 图标 | 图标 embedding/生成 | Lucide/Tabler/Material 索引 + 描摹 | 吸附 + 置信度 + 回退 |
| Emoji | 生成式/多模态识别 | Twemoji/Noto Emoji 匹配 | 可选；不嵌入 Apple |
| 照片 | 生成式修复/分层 | 裁切嵌入/模糊/打码 | 默认 embed，隐私模式可降级 |
| 评估 | DinoScore、LPIPS、Design2Code | 自建合成集 + 多指标 | **自建 benchmark** |
| App | 原生 + Core ML/ONNX | PySide6/Tauri + Python sidecar | 先本地壳，后原生 |

## 12. 风险、未验证说法与工程坑

1. **StarVector 不是 UI 模型**：官方明确“不适用于自然图像/插画”；SliDer 代码未发布；ScreenAI 未找到官方开放权重；Lens 权重可验证性不足。不要把 demo 当整图 UI 能力。
2. **许可不一致要按文件**：OmniParser 仓库 `LICENSE` 是 CC-BY-4.0（README 徽章写 MIT），旧 `icon_detect` 权重 AGPL、新 `icon_detect_v3` MIT；Surya 代码 Apache 但权重 modified OpenRAIL（<$5M 免费）；OmniSVG 数据集 CC BY-NC-SA。
3. **VLM OCR 会“改写”文本**：dots.ocr/GOT/PaddleOCR-VL 是生成式，可能补全/格式化；UI 文本必须字符级校验/人工确认。
4. **OCR benchmark 不可直接外推**：OCRBench/CC-OCR/OmniDocBench 主要测文档；UI 小字/混排/彩色背景是另一个分布。**SSIM 也会骗人**：照片占位、字体差异、文字错字都可能 SSIM 很高；必须组合文本/布局/感知/可编辑性指标。
6. **字体许可不是“转曲就没事”**：PingFang/SF/微软雅黑的 EULA 可能同时限制嵌入和 outline 再分发；只用 OFL/Apache 最稳。
7. **SF Symbols / OpenMoji**：SF Symbols 不跨平台打包；OpenMoji CC BY-SA 有 share-alike，优先 Twemoji（CC BY）或 Noto Emoji（OFL）。
8. **“去像素隐写” ≠ “匿名化”**：文本、logo、布局、颜色、嵌入照片仍是强指纹；Python sidecar 与 Mac App Store 也可能冲突，先走 Developer ID 直接分发。本文“当前”指 2026-11-03 检索，落地前重新核对 LICENSE/模型卡。

## 13. 参考链接（按主题）
**矢量化 / SVG / 截图转代码**
StarVector：https://arxiv.org/abs/2312.11556 · https://github.com/joanrod/star-vector · https://starvector.github.io/
OmniSVG：https://arxiv.org/abs/2504.06263 · https://github.com/OmniSVG/OmniSVG
SliDer：https://arxiv.org/abs/2511.13478 · https://github.com/adamhazimeh/SliDer
LIVE：https://arxiv.org/abs/2206.04655 · https://github.com/Picsart-AI-Research/LIVE-Layerwise-Image-Vectorization
diffvg：https://github.com/BachiLi/diffvg · vtracer：https://github.com/visioncortex/vtracer · potrace：https://potrace.sourceforge.net/
SVGcode：https://svgco.de/ · Vectorizer.AI：https://vectorizer.ai/ · Adobe Image Trace：https://helpx.adobe.com/illustrator/using/image-trace.html
screenshot-to-code：https://github.com/abi/screenshot-to-code · OpenUI：https://github.com/wandb/openui

**UI 检测 / 屏幕解析**
OmniParser：https://github.com/microsoft/OmniParser · https://arxiv.org/abs/2408.00203 · https://huggingface.co/microsoft/OmniParser-v2.0
ScreenAI：https://arxiv.org/abs/2402.04615 · https://research.google/blog/screenai-a-visual-language-model-for-ui-and-visually-situated-language-understanding/
UIED：https://github.com/MulongXie/UIED · https://dl.acm.org/doi/10.1145/3368089.3417940
UI-TARS：https://github.com/bytedance/UI-TARS · https://arxiv.org/abs/2501.12326 · https://seed-tars.com/1.5/
Florence-2：https://arxiv.org/abs/2311.06242 · https://huggingface.co/microsoft/Florence-2-base-ft
GroundingDINO：https://github.com/IDEA-Research/GroundingDINO · OS-Atlas：https://github.com/OS-Copilot/OS-Atlas
ScreenSpot：https://arxiv.org/abs/2401.10935 · ScreenSpot-Pro：https://arxiv.org/abs/2504.07981
macOS AX：https://developer.apple.com/documentation/applicationservices/axuielement_h · Windows UIA：https://learn.microsoft.com/en-us/windows/win32/winauto/entry-uiauto-win32

**OCR**
Apple Vision：https://developer.apple.com/documentation/vision/recognizing-text-in-images · https://developer.apple.com/documentation/vision/vnrecognizetextrequest
Windows OCR：https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine
PaddleOCR：https://github.com/PaddlePaddle/PaddleOCR · https://arxiv.org/abs/2507.05595
PaddleOCR-VL：https://arxiv.org/abs/2510.14528 · https://huggingface.co/PaddlePaddle/PaddleOCR-VL
RapidOCR：https://github.com/RapidAI/RapidOCR · Surya：https://github.com/datalab-to/surya · https://www.datalab.to/blog/surya-2
dots.ocr：https://github.com/studio-dots-ai/dots.ocr · https://huggingface.co/dots-studio/dots.ocr
GOT-OCR2.0：https://github.com/Ucas-HaoranWei/GOT-OCR2.0 · https://arxiv.org/abs/2409.01704
Tesseract：https://github.com/tesseract-ocr/tesseract · EasyOCR：https://github.com/JaidedAI/EasyOCR

**字体 / 图标 / Emoji**
DeepFont：https://arxiv.org/abs/1507.03196 · FontCLIP：https://arxiv.org/abs/2403.06453 · https://github.com/yukistavailable/FontCLIP
VLM 字体基准：https://arxiv.org/abs/2503.23768 · Lens：https://github.com/mixfont/lens
Lucide：https://github.com/lucide-icons/lucide · Material Symbols：https://developers.google.com/fonts/docs/material_symbols
SF Symbols：https://developer.apple.com/design/human-interface-guidelines/sf-symbols · FA 许可：https://fontawesome.com/license/free
Simple Icons：https://github.com/simple-icons/simple-icons · Tabler：https://github.com/tabler/tabler-icons
Phosphor：https://github.com/phosphor-icons/phosphor-icons · Heroicons：https://github.com/tailwindlabs/heroicons
Twemoji：https://github.com/twitter/twemoji · OpenMoji：https://openmoji.org/ · Noto Emoji：https://github.com/googlefonts/noto-emoji
OFL FAQ：https://openfontlicense.org/ofl-faq/ · Apple Fonts：https://developer.apple.com/fonts/
Noto CJK：https://github.com/notofonts/noto-cjk · Source Han Sans：https://github.com/adobe-fonts/source-han-sans
Inter：https://github.com/rsms/inter · Roboto：https://github.com/googlefonts/roboto

**评估**
Design2Code：https://arxiv.org/abs/2403.03163 · https://salt-nlp.github.io/Design2Code/
WebSight：https://huggingface.co/datasets/HuggingFaceM4/WebSight · https://arxiv.org/abs/2403.09029
ScreenQA：https://arxiv.org/abs/2209.08199 · SVGenius：https://arxiv.org/abs/2506.03139 · https://github.com/ZJU-REAL/SVGenius
SGP-Bench：https://github.com/sgp-bench/sgp-bench · OCRBench v2：https://arxiv.org/abs/2501.00321
CC-OCR：https://arxiv.org/abs/2412.02210 · OmniDocBench：https://arxiv.org/abs/2412.07626
SSIM：https://ieeexplore.ieee.org/document/1284395 · LPIPS：https://arxiv.org/abs/1801.03924 · DINOv2：https://arxiv.org/abs/2304.07193

**应用架构 / 打包 / 许可**
Tauri sidecar：https://v2.tauri.app/develop/sidecar/ · Tauri：https://github.com/tauri-apps/tauri · Electron：https://github.com/electron/electron
Qt LGPL：https://www.qt.io/development/open-source-lgpl-obligations · PySide6：https://doc.qt.io/qtforpython-6/
PyInstaller：https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt · Nuitka：https://github.com/Nuitka/Nuitka/blob/develop/LICENSE.txt
Avalonia：https://avaloniaui.net/ · Flutter：https://flutter.dev/
Apple 公证：https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
Windows App SDK：https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/ · WinUI 3：https://learn.microsoft.com/en-us/windows/apps/winui/winui3/
ScreenCaptureKit：https://developer.apple.com/documentation/screencapturekit · Windows Graphics Capture：https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/screen-capture
ONNX Runtime：https://onnxruntime.ai/ · Core ML：https://developer.apple.com/documentation/coreml
fontTools：https://github.com/fonttools/fonttools · HarfBuzz：https://github.com/harfbuzz/harfbuzz · FreeType：https://freetype.org/
resvg（测试渲染）：https://github.com/linebender/resvg
