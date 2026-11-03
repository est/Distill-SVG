# 蒸矢 / distill-svg 原生应用架构（v1 设计）

> 状态：架构建议；v1 macOS SwiftUI 壳已在 `mac/` 实现（未签名测试版），Windows/Linux 仍未实现。基于当前仓库 `distill_svg` 的实际能力（flat/wireframe 两种输出、`embed/placeholder/outline` 三种照片策略、Apple Vision 与 RapidOCR 适配器、scene JSON、`<text>` 可复制文本），以及 2026-11 的官方文档。
>
> 目标：本地/离线、隐私优先；先把管线做稳，再发可分发二进制；macOS 与 Windows 10 原生应用，Linux 只做 CLI，移动端后置。
>
> 说明：当前 `svg.py` 在 `photos=embed` 时输出 data-URI `<image>`；README 已同步为“整图不嵌入，照片/头像裁剪可嵌入”的 v0.2 默认。发布前仍需统一 UI 文案与隐私提示，见第 6 节。

---

## 0. 结论先行

**推荐 v1：一个 Python 共享核心 + 两个薄原生壳，CLI 与 GUI 共用同一套转换代码。**

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 核心语言 | 继续用 Python，不重写 Rust | CV/OCR/字体拟合已经在 Python 里跑通；重写是 6-12 个月的风险，不是 v1 的差异化 |
| macOS 壳 | SwiftUI | 原生文件选择、拖放、Quick Look、Vision、沙盒、公证都最直接；不引入 WebView 和 Node |
| Windows 壳 | WinUI 3（C#/XAML） | MSIX 身份、`Windows.Media.Ocr`、文件选择器、签名/更新链路最顺；Win10 1809+ 可跑 |
| Linux | 只发 CLI | 没有 GUI 需求；减少 Qt/GTK 依赖和打包面积 |
| 进程模型 | 壳启动 `distill-core` 子进程，NDJSON over stdio | 无端口、无网络面、崩溃隔离；比 FFI 稳，比 HTTP 安全 |
| 输出 | flat + wireframe；文本始终 `<text>` | 满足“可选/可复制”；照片裁剪作为显式模式 |
| OCR | macOS Vision；Windows `Windows.Media.Ocr` + RapidOCR 兜底；Linux RapidOCR | 系统 OCR 快且离线；RapidOCR 保证中文和跨平台一致性 |
| 字体/模型 | 默认全量随包，首次运行不下载 | “离线可用”是产品承诺；可选语言包才显式下载 |
| 隐私默认 | 无遥测、无网络权限、默认不联网；崩溃日志仅本地 | 与“隐私优先”一致；更新检查也必须显式同意 |
| 不选 | Electron、Qt、Flutter（桌面 v1）、纯 Rust 重写 | 体积、签名复杂度、重写成本；Flutter 留给移动端评估 |

---

## 1. v1 架构选型对比

下表的启动/体积是**量级估算**，包含 Python 运行时、OpenCV、OCR 模型和字体；实际以构建产物称重为准。Tauri 的壳本身很小，但带 Python sidecar 后体积由 sidecar 决定，不能用“Tauri hello world 几 MB”来宣传。

| 方案 | 冷启动 | 安装包/常驻 | 签名/公证 | OCR 可用性 | ML 运行时 | 可维护性 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Python core + Tauri v2 sidecar | 1.5-3 s（首次 import cv2 更慢） | 壳 2-10 MB + core 150-250 MB | macOS 需签 sidecar；Windows 默认 NSIS/MSI，MSIX 要自己做 | 需另接 Vision/WinOCR 或 RapidOCR | ONNX Runtime（RapidOCR） | Web 技能可复用；但要维护 Rust、JS、Python 三套 | **备选**：团队 Web-first 且要复用编辑器时 |
| Python core + SwiftUI/WinUI 原生壳 | 1-2.5 s | 壳 2-15 MB + core 150-250 MB | 每平台一套，但路径清晰 | 原生 API 直接可用 | ONNX Runtime + 系统 OCR | 两套 UI；核心只有一份 | **推荐 v1** |
| Rust core + 原生壳 | 0.2-0.6 s | 20-60 MB（含模型） | 最简单，单二进制 | 需绑定 Vision/WinOCR/ONNX | ONNX Runtime / Core ML | 长期最好；重写成本最高 | 不是 v1；移动端再评估 |
| SwiftUI + Vision only | <300 ms | 5-15 MB | 简单 | 仅 macOS | 不需要 | 两平台要各写一套 CV | 否，除非只做 macOS |
| WinUI 3 + Windows OCR only | 0.3-1 s | 20-60 MB | MSIX 清晰 | 仅 Windows，且依赖语言包 | 可选 ONNX | 同上 | 否，无法共享核心 |
| Python + Qt/PySide6 | 1-2.5 s | 180-300 MB | 嵌套 Qt 库，macOS 公证繁琐 | RapidOCR | ONNX Runtime | 一套 UI；C++/Python 混合 | 否；LGPL/商业授权和体积不划算 |
| Flutter + Python FFI | 1.5-3 s | 150-250 MB | 各平台插件链 | 插件质量参差 | ONNX FFI | 移动端可复用；桌面原生集成弱 | 移动端再看 |
| Python core + Electron sidecar | 1.5-3 s | 250-400 MB | 嵌套 Chromium，公证/更新都重 | 需 helper | ONNX Runtime | Web 生态成熟 | 否；与“小、离线、低攻击面”冲突 |

### 1.1 关键权衡

- **启动**：Python 的瓶颈不是解释器，而是 `cv2`/`numpy` 首次导入和模型加载。用 **onedir**、预编译字节码、常驻 worker、懒加载 OCR，可以把第二次转换压到 0.6-1.2 s；`--onefile` 每次解压，GUI 不适合。
- **体积**：Tauri/Electron 的“壳大小”不是重点，Python + OpenCV + ONNX + 字体才是 100 MB 级。要减体积，优先裁 OpenCV、裁模型、按语言分包，而不是换 UI 框架。
- **签名**：macOS 要签**每一个 Mach-O**（主程序、Python、`.so`/`.dylib`、Swift OCR helper），公证才过；Windows 要同时签 `.exe` 和 MSIX/安装器。Tauri sidecar 会把这个问题放大一层。
- **OCR**：Vision 在 macOS 上质量最好且零依赖；`Windows.Media.Ocr` 要求 MSIX 包身份，中文语言包不一定预装；RapidOCR 是唯一能保证“三平台同一套中文结果”的离线方案。
- **ML 运行时**：RapidOCR 用 ONNX Runtime CPU 即可；不要为 v1 引入 PyTorch。ONNX Runtime 可用自定义 minimal build 减体积，但会增加构建复杂度。
- **可维护性**：共享核心必须是**库**，不是“只有 CLI 能跑”的脚本。壳只做 UI/文件/OCR/预览，算法和 SVG 序列化留在核心，否则两平台会分叉。

### 1.2 为什么不选另外几条路

- **纯 Rust 重写**：OpenCV 的轮廓/连通域、字体度量、OCR 后处理都要重做；`fonts.py` 里的墨迹框/基线拟合是产品护城河，重写等于重新调参。等移动端或性能成为瓶颈再做。
- **Electron**：自带 Chromium/Node，安装包和内存都大；隐私产品还要审计浏览器攻击面，收益不匹配。
- **Qt**：PySide6 是 LGPL，动态链接和 relink 义务要处理；PyQt 是 GPL/商业双许可。跨平台一致，但原生感、体积、macOS 公证都不占优。
- **Flutter 桌面**：真正的优势在移动端复用；桌面文件选择、OCR、菜单、辅助功能都要插件，v1 不值得。
- **SwiftUI + Vision 单独做**：macOS 会很漂亮，但 Windows 要重写全部 CV/字体逻辑，等于两个产品。

---

## 2. 平台具体建议

### 2.1 macOS：SwiftUI + Vision，先直接分发

> 已实现 v1 壳：`mac/`（SwiftUI + 进程内 Vision OCR + PyInstaller onedir 核心）。
> 构建：`./mac/build_app.sh`；细节与体积/签名说明见 `mac/README.md`。

**推荐结构**

```
DistillSVG.app/
  Contents/MacOS/DistillSVG            # SwiftUI 壳
  Contents/MacOS/distill-core          # Python onedir 启动器
  Contents/Resources/core/             # Python runtime + site-packages + 模型/字体
  Contents/Resources/vision_ocr        # 预编译、已签名的 Swift CLI（可选）
  Contents/Frameworks/                 # 需要时放 Python/OpenCV dylib
```

- **最低系统**：建议 macOS 13+（SwiftUI 成熟，仍覆盖 Intel）；Vision 旧 API 10.15+，macOS 15+ 用新的 `RecognizeTextRequest`，做可用性分支。Intel 版可后置，先发 arm64。
- **SwiftUI 壳**：`NSOpenPanel`/拖放导入、预览（WKWebView 或 Quick Look）、scene JSON 编辑、导出、批量队列。编辑只改 scene JSON，再调核心 `render`，不在 Swift 里重写排版。
- **为什么不用 Tauri**：WKWebView 能用，但你要同时维护 Rust、JS、Python；沙盒下 sidecar 的签名/继承更绕；原生拖放、Quick Look、Services、Shortcuts 反而要写插件。除非“Web 编辑器”本身就是产品核心，否则 SwiftUI 更稳。
- **Vision**：`VNRecognizeTextRequest` 的 `.accurate` 路径，语言顺序 `zh-Hans,en-US`；Apple 明确说明中文只建议和英文配对，且 Vision **不支持中文的语言纠正**（`usesLanguageCorrection`/`customWords` 对中文无效）。macOS 15+ 可用新的 `RecognizeTextRequest` async API，低版本回退旧 API。
- **不要运行时 `swiftc`**：当前 `ocr.py` 在首次运行编译 `tools/vision_ocr.swift`，这在分发版里不可接受（用户没装 Xcode、沙盒无写权限、公证前无法签名）。Phase 0 必须改成**预编译 universal helper**，或直接在 Swift 壳里调 Vision 后把 OCR JSON 传给核心。
- **OCR 边界**：核心只接受 `{text, confidence, x, y, w, h}` 行级 JSON；macOS 壳负责 Vision，CLI 负责调用 helper。这样 Windows 换 WinOCR、Linux 换 RapidOCR 时，核心不变。

**沙盒与公证**

- 直接官网分发：需要 Developer ID Application 证书、Hardened Runtime、secure timestamp，提交 `notarytool`，再 `stapler staple`；`spctl -a -vv` 验证。公证要求所有可执行文件都有有效签名，不能带 `get-task-allow`。
- App Sandbox 不是官网分发的强制项，但隐私产品建议开启。若开启：所有子进程/helper 必须只有 `com.apple.security.app-sandbox` + `com.apple.security.inherit` 两个 entitlement；**子进程只继承静态权限，不继承用户刚在打开面板里选的文件**。因此核心应通过 **stdin 传图片字节**，或让壳把输入复制到 App 容器临时目录再传路径，不要假设子进程能读用户选的任意路径。
- **PyInstaller onefile 不要用于沙盒版**：官方文档明确 onefile 在“签名/公证 + sandbox”场景不可用。用 onedir，并把整个目录签好。
- 若沙盒下 sidecar 反复出问题：v1 先发 **Hardened Runtime + 公证、非沙盒** 的 DMG，把 MAS 版延后；不要把“上架 Mac App Store”当 v1 阻塞项。
- 不申请网络 entitlement。没有 `com.apple.security.network.client` 的沙盒应用在系统层面就无法发起网络连接，这是最强的“无 telemetry”证明之一。

**依赖避免**

- 不依赖 Homebrew、系统 Python、`pip`、`swiftc`。用 python-build-standalone 或 PyInstaller onedir 打包 CPython 3.12；`PYTHONHOME`/`PYTHONPATH` 启动时清空；所有 dylib 用 `@loader_path`/`@rpath`，不引用 `/usr/local`。
- 字体只读随包；PingFang 只作为“系统已安装时的匹配候选”，绝不复制、子集化或嵌入输出。

**更新**：Sparkle 2，HTTPS appcast + EdDSA 签名，公证并 staple 的 DMG；默认不自动检查，首次检查前询问。Sparkle 2 支持沙盒应用。

### 2.2 Windows 10：WinUI 3 + MSIX，Windows OCR + RapidOCR 兜底

**推荐结构**

```
DistillSVG.exe                 # WinUI 3 壳（C#/XAML）
core\distill-core.exe          # Python onedir 启动器
core\python\...                # 运行时 + 依赖
core\models\...                # RapidOCR ONNX
core\fonts\...                 # Noto/Source Han
```

- **WinUI 3 而不是 Tauri**：WinUI 3 原生支持 Win10 1809+（Windows App SDK），MSIX 是默认打包方式，文件选择器、拖放、剪贴板、通知都直接；`Windows.Media.Ocr` 需要包身份，MSIX 正好满足。Tauri 默认产出 NSIS/MSI，要做 MSIX 得额外一层工程，且 Python sidecar 仍要单独签。
- **Windows OCR**：`Windows.Media.Ocr` 只在**有包身份的桌面应用**中受支持（MSIX 安装）。用 `OcrEngine.AvailableRecognizerLanguages` 检测语言包；中文 OCR 语言包在部分 en-US 机器上没装，必须检测并回退，不能假设一定有。
- **不要用 Windows AI `TextRecognizer` 做 v1 默认**：Windows App SDK 的新文本识别 API 目前主要面向带 NPU 的设备（Copilot+ PC），且同样需要包身份；Win10 大范围设备不满足。v1 用 `Windows.Media.Ocr`，RapidOCR 兜底，后续再评估 NPU 路径。
- **RapidOCR**：ONNX Runtime CPU，量化 PP-OCR 模型；模型随包、校验 SHA-256；不联网下载。它是中文一致性和“语言包缺失”场景的保险。
- **MSIX 细节**：single-project MSIX 只支持**一个可执行文件**；我们有 `DistillSVG.exe` + `distill-core.exe`，所以用 **Windows Application Packaging Project**（两项目）或把核心作为资源随包、由壳启动。清单声明 `runFullTrust`，不声明 `internetClient`；文件访问用 FileOpenPicker/FileSavePicker，不申请 `broadFileSystemAccess`。
- **更新**：MSIX + `.appinstaller`，签名后自动更新；注意 `ms-appinstaller:` 协议自 2023-12 起默认禁用，用户需下载并双击 `.appinstaller`，不要做“网页一键安装”的假设。
- **SmartScreen**：`DistillSVG.exe` 和 MSIX 都要 Authenticode 签名；Azure Artifact Signing（原 Trusted Signing，约 $9.99/月，地区受限）或 OV 证书。**EV 从 2024 年起不再即时获得 SmartScreen 信誉**，新发布仍可能提示，持续用同一身份签名积累信誉；上 Microsoft Store 则由 Store 重签，最省事。
- **Windows 10 生命周期**：Win10 已于 2025-10-14 结束支持，消费级 ESU 到 2027-10-12。v1 可以支持 Win10 22H2，但官网要写明“建议 Win11”；测试矩阵保留 Win10 22H2，不作为长期主战场。

**启动优化**：MSIX 用 framework-dependent Windows App SDK 减体积；核心用 onedir、懒加载模型；第二次转换复用常驻 worker；不要在 UI 线程做解码/OCR。

---

## 3. Linux：只做 CLI

**目标**：`distill-svg` 单命令，输入图片，输出 SVG/scene JSON；无 GUI、无服务、无 root。

**打包**

- **PyInstaller `--onedir`**（不是 onefile）：`opencv-python-headless`（不带 Qt/GUI），排除 `tkinter`、`PyQt`、`matplotlib` 等；`--collect-all rapidocr` 收集模型和配置；`--strip`；不要 UPX 压 `.so`（易被 AV 误报、可能破坏加载）。
- **Nuitka `--mode=standalone`** 作为备选：启动更快、代码不易反编译，但 CI 编译时间长、需要 C 编译器；先 standalone 验证，再考虑 onefile。
- **静态 OpenCV 不是必须**：`opencv-python-headless` 的 wheel 已自带共享库；完全静态链接会撞 glibc/FFmpeg/插件问题。更实际的是 onedir + 随包 `.so` + `$ORIGIN` rpath，在 manylinux 容器里构建，在 Ubuntu 22.04/24.04 上验证。
- **分发**：tar.gz（校验和 + minisign/Sigstore）、`.deb`/`.rpm`；AppImage 可选，但 FUSE/沙盒环境兼容性要测。x86_64 与 aarch64 分别构建，PyInstaller 不能交叉编译。
- **OCR**：RapidOCR `rapidocr` v3 + ONNX Runtime CPU；当前 `ocr.py` 用的是旧包 `rapidocr_onnxruntime`，Phase 0 迁移到 v3 并固定模型 manifest。PaddleOCR 作为可选重后端，不随默认包。Tesseract 中文小字弱，只做 fallback。
- **uv**：开发/构建统一 `uv sync --locked`，发布记录 `uv.lock`；`uv export --format cyclonedx1.5` 生成 SBOM。
- **CLI 契约**：退出码 0/2/3/4 区分“成功/输入错误/OCR 不可用/内部错误”；进度写 stderr，JSON 结果写 stdout；`--ocr-json` 是跨平台逃生口。

---

## 4. 如何保留 Python 核心并暴露给壳

### 4.1 核心边界

把 `distill_svg` 从“CLI 脚本集合”整理成**库 + 薄 CLI**：

```
distill_svg/
  api.py        # convert(Request) -> Result；不 import argparse
  pipeline.py   # prepare → ocr → text → panels → images → svg
  ocr.py        # 只做适配器，不决定 UI
  svg.py        # 纯序列化
  privacy.py    # 敏感扫描/元数据策略
distill_core/   # 进程入口：解析 NDJSON，调 api.convert
```

`api.convert` 的输入输出都是可序列化 dataclass/dict；CLI、GUI、测试都走它。这样未来换 Rust 也可以先替换实现，不动壳。

### 4.2 输出模型

- **Scene JSON v1**：`width/height/style/photos/panels/images/icons/texts`；`images` 带 `crop_box`、`embedded`（base64 只放 SVG，不重复进 scene），`texts` 带 `text/x/baseline/size/family/weight/fill`。这就是编辑器修改的唯一中间层。
- **flat / wireframe 共用 scene**：切换样式只换渲染器，不重跑 OCR/CV；线框模式把面板变白底灰边、文本/图标单色，照片转灰度或 outline。
- **文本始终 `<text>`**：保证可复制/可搜索；未来 `--text=path` 只作为归档选项，转 path 后要在 UI 明示“不可复制”。
- **照片裁剪**：`photos=embed` 输出 data-URI `<image>` + `clipPath`；`placeholder`/`outline` 二选一；裁剪重新编码、限制最大边、默认去元数据。
- **自包含**：无外部 `href`、无远程字体/图片、无时间戳/源路径；ID 用稳定序号，保证同一 scene 重复渲染结果一致。

### 4.3 四种暴露方式

| 方式 | 启动/复杂度 | 安全面 | 崩溃隔离 | 适用 |
| --- | --- | --- | --- | --- |
| **CLI + NDJSON over stdio** | 低 | **无网络端口** | 好（子进程） | **推荐 v1** |
| 本地 HTTP sidecar | 低-中 | 端口、token、CSRF、DNS rebinding | 好 | 仅 Tauri/Web 前端且不愿写 stdio 时 |
| FFI（PyO3/C API/ctypes） | 最低延迟 | 同进程 | **差**（Python 崩溃拖垮 UI） | 核心稳定后的性能优化 |
| 进程内嵌入 CPython | 低延迟 | 同进程 | 差；GIL/ABI/打包复杂 | 不推荐 v1 |

**推荐协议（v1）**

```json
{"v":1,"id":"r1","cmd":"convert","input":{"path":"/tmp/in.png"},
 "options":{"style":"flat","photos":"embed","photo_format":"jpeg","photo_quality":88,
            "ocr":"vision","languages":["zh-Hans","en-US"]},
 "ocr":[{"text":"设置","confidence":0.98,"x":40,"y":120,"w":80,"h":32}]}
{"v":1,"id":"r1","event":"progress","stage":"ocr","pct":0.4}
{"v":1,"id":"r1","ok":true,"result":{"svg_path":"/tmp/out.svg","scene_path":"/tmp/out.json",
 "stats":{"panels":12,"texts":38,"embedded_photos":2,"svg_bytes":41230}}}
{"v":1,"id":"r1","ok":false,"error":{"code":"OCR_UNAVAILABLE","message":"..."}}
```

- **一行一个 JSON**，UTF-8，`\n` 结尾；不支持二进制帧。大图用 `input.path`（CLI）或 `input.data_b64`（沙盒 GUI，限制 32 MB）。
- `v` 做协议版本；壳启动时先发 `{"cmd":"hello"}` 做握手，核心回能力列表（OCR/模型/字体/协议版本）。
- 未知字段忽略；错误码稳定；不要把 Python traceback 直接给用户，日志里保留。
- 进度事件可丢弃；结果必须一条；超时由壳控制，超时后杀进程组。

### 4.4 如果一定要本地 HTTP

- 只绑 `127.0.0.1`，**绝不 `0.0.0.0`**；随机端口；每次启动生成 32 字节 token。
- token 不放命令行（`ps` 可见），走 stdin 或 0600 文件；校验 `Host`/`Origin`，关闭 CORS，拒绝 `null` origin。
- 只接受 POST JSON RPC；限制 body 大小、并发、超时；不提供静态文件/目录遍历。
- Unix domain socket（macOS/Linux，0600）或 Windows named pipe（当前用户 ACL）优于 TCP；用完即删。
- 本地其他进程仍可能连接；token 只是缓解，不是强隔离。**能用 stdio 就不用 HTTP。**

### 4.5 文件权限与生命周期

- 临时目录 0700，文件 0600；macOS 用 App 容器，Windows 用 `%LOCALAPPDATA%` + 当前用户 ACL，Linux 用 `$XDG_RUNTIME_DIR`。
- 输出先写同目录临时文件，`fsync` 后原子 rename；默认 0600，用户显式导出再放宽。
- 解析真实路径，拒绝符号链接穿越；不 `shell=True`；参数用数组。
- 壳退出时发 `shutdown`，再杀进程组；Windows 用 Job Object，macOS/Linux 用 process group；避免 PyInstaller bootloader 孤儿进程。
- 核心无状态；每次请求带完整 options；不要依赖全局环境变量（`DISTILL_SVG_FONT_DIRS` 这类要改成显式参数）。

---

## 5. 离线模型/字体打包

### 5.1 体积预算（量级，需构建称重）

| 组件 | 体积（安装后） | 说明 |
| --- | --- | --- |
| 壳（SwiftUI/WinUI） | 2-15 MB | 不含 WebView 运行时；WinUI framework-dependent 更小 |
| CPython + 标准库 | 15-30 MB | 3.12，onedir |
| numpy | 20-30 MB | 可用更小的构建，但不要冒险 |
| OpenCV headless | 60-120 MB | 主要体积；裁掉 video/highgui/ffmpeg 可降 |
| Pillow | 10-15 MB | 字体渲染/图像 I/O |
| ONNX Runtime | 15-25 MB | CPU EP；minimal build 可降到个位数 |
| RapidOCR 代码 + 模型 | 20-35 MB | det/rec/cls；v5/v6 mobile 模型 |
| 字体（Noto Sans SC） | 10-20 MB | 单可变字体或 2-3 个字重 |
| 图标子集 | 1-3 MB | Lucide/Material 按需 |
| **合计（安装后）** | **200-400 MB** | 安装包压缩后约 80-180 MB；OpenCV 版本差异大 |

> 这不是“越小越好”的竞赛。比总体积更重要的是：**首次启动不下载、不联网、不依赖系统 Python**。

### 5.2 许可证清单

| 组件 | 许可证 | 分发注意 |
| --- | --- | --- |
| Python | PSF | 保留 LICENSE |
| numpy | BSD-3-Clause | 保留 notice |
| OpenCV | Apache-2.0 | 保留 LICENSE/NOTICE |
| Pillow | HPND | 保留 LICENSE |
| ONNX Runtime | MIT | 保留 LICENSE |
| RapidOCR 代码 | Apache-2.0 | 保留 LICENSE；修改文件要标注 |
| PaddleOCR / RapidOCR 模型 | Apache-2.0 | 保留版权/许可/NOTICE；记录模型 SHA-256 与来源 |
| Noto Sans SC | SIL OFL 1.1 | 可随软件分发；保留 OFL 文本；不要单独售卖字体 |
| Source Han Sans | SIL OFL 1.1 | 有 Reserved Font Name "Source"；修改版不能继续用该名 |
| PingFang SC | Apple 专有 | **不能再分发**；仅引用系统字体，嵌入输出需按 Font Book 的 embedding 限制并做法务确认 |
| Microsoft YaHei | 微软专有 | **不能再分发**；只能引用系统字体 |
| SF Symbols | Apple 平台授权 | 仅用于 Apple 平台 UI，不作为输出资产再分发 |
| Lucide | ISC | 保留版权/许可 |
| Material Symbols | Apache-2.0 | 保留 LICENSE/NOTICE |

**字体策略**：

- 默认输出 `<text>` + font stack，保证可复制；随包只带 OFL 字体（Noto Sans SC 为主，Source Han Sans 可选）。
- 嵌入字体只对 OFL 字体做 subset，并在 SVG 里保留 license/notice；绝不嵌入 PingFang/微软雅黑。
- 文本转 path 只作为“归档/跨端一致”选项，默认关闭；转 path 后不可复制，UI 必须提示。
- Windows 的微软雅黑、macOS 的 PingFang 只用于“本机匹配/预览”，不进入安装包、不进入 SVG 的 `@font-face`。

### 5.3 首次下载 vs 随包

- **默认随包**：中文/英文 OCR 模型 + Noto Sans SC + 图标子集。离线安装即可用，这是隐私产品的卖点。
- **可选语言包**：日/韩/繁中等按需下载；必须用户点击、HTTPS、显示体积/来源/许可证，下载后校验 SHA-256 + 签名，失败可回退。
- **不静默下载**：更新检查、语言包、崩溃上报都默认关闭；企业环境提供离线镜像/目录导入。
- **完整性**：`models/manifest.json` 记录版本、SHA-256、许可证、来源；启动时校验，失败则报错而不是降级到未知模型。只加载 ONNX，不加载 pickle。

---

## 6. 隐私与安全设计

### 6.1 威胁模型

- 同机其他进程/用户读取临时文件、socket、日志；
- 云同步/备份把输出或临时文件带走；
- 截图含姓名、手机号、订单号、身份证、地址、二维码；
- 嵌入的照片裁剪含人脸、证件、聊天内容；
- 崩溃转储/日志泄露 OCR 文本或图片；
- 更新通道/模型下载被劫持；
- 恶意 SVG/JSON 输入（如果未来支持导入）。

### 6.2 默认不联网

- 不接任何分析/广告/崩溃 SDK；不做“匿名统计”。
- macOS 不申请 `com.apple.security.network.client`；Windows MSIX 不声明 `internetClient`；Linux 默认不创建监听 socket。
- 更新检查、语言包下载、崩溃上传都是**显式 opt-in**，并在 UI 写明域名/内容。
- 预览不加载远程图片/字体；CSP `default-src 'self'`；Tauri 若使用，按官方 CSP/能力配置收紧。
- CI 加“断网运行”测试；发布前用 Little Snitch/Windows Firewall/tcpdump 审计一次。

> 注意：只有 **macOS 沙盒 + 不申请 network entitlement** 才是系统级断网保证。macOS 非沙盒版、Windows `runFullTrust` 的 Win32 进程不会被 entitlement/capability 强制断网；“无网络”必须靠代码不调用网络 API + 断网测试来证明，不能只写在文案里。

### 6.3 临时文件与清理

- 尽量在内存中完成；必须落盘时用 0700 目录 + 0600 文件，任务结束/失败/下次启动都清扫。
- 不复制原始截图到临时目录；嵌入裁剪直接在内存编码。
- 明确文档：SSD 的 wear leveling 使“安全擦除”不可承诺；高敏场景建议占位模式，不落盘。
- 日志不写图片字节、OCR 全文、用户路径；调试日志需用户主动开启并提示。

### 6.4 元数据与 EXIF

- 输入解码后立即 `exif_transpose`，转 sRGB，丢弃 EXIF/XMP/IPTC/GPS/相机/软件/时间；PNG 去 `tEXt/iTXt/zTXt/eXIf`，JPEG 重新编码去 APPn。
- 嵌入裁剪重新编码（当前 `_embed_crop` 用 OpenCV `imencode`，天然不带 EXIF；若换 Pillow 要显式 strip）。
- SVG 不写 `<metadata>`、不写源文件名/路径/时间戳；`<title>` 用通用文本；不要生成带用户名/构建路径的 generator 字符串。
- OCR 文本做 NFC，去掉 `Cf`（零宽/方向控制）和多余空白；保留真实文字，不自动“纠错”。

### 6.5 嵌入照片的警告

- `photos=embed` 是显式模式：导出前显示“将嵌入 N 个裁剪，可能含人脸/证件/聊天内容”，并提供“改为占位/模糊/灰度”。
- 隐私预设强制 `photos=placeholder`；wireframe 默认灰度或 outline。
- 可选本地面部/二维码检测（Vision/OpenCV）只用于警告，不上传、不写日志；检测结果不持久化。
- 照片裁剪仍是栅格：像素级水印/隐写不会被“矢量化”消除；文案必须说清楚。

### 6.6 敏感文本扫描

- 在导出前扫描 OCR 文本和最终 SVG `<text>`：手机号、身份证、银行卡、邮箱、地址、订单号、API key、IP 等；中文加 `1[3-9]\d{9}`、`\d{17}[\dXx]`、省/市/区/路/号等规则。
- 只提示，不静默改写；用户可选择“保留/替换为 ••••/删除该行/取消导出”。
- 批量模式给汇总（命中文件数/行数），不把全文写日志。
- 这是启发式，不是合规审计；UI 要说明“可能漏报/误报”。

### 6.7 沙盒权限

- macOS：只申请用户选择文件的读写；用 security-scoped bookmark 记住最近目录；不申请全盘、自动化、网络。
- Windows：MSIX `runFullTrust`（桌面应用必需）+ 文件选择器；不申请 `broadFileSystemAccess`；不声明网络能力。
- Linux：普通用户权限，不 setuid、不 root、不写系统目录。

### 6.8 SVG 输出安全

- 文本 `escape`；不输出 `<script>`/`<foreignObject>`/外部 `href`；只允许内嵌 data URI 图片（若 embed）。
- 限制路径点数、元素数、图片尺寸；防止巨大 SVG 拖垮查看器。
- 不接受 SVG 作为输入（v1 只收 PNG/JPEG/WebP）；未来支持时单独做 sanitizer。

---

## 7. 更新、崩溃、签名与可复现构建

### 7.1 更新矩阵

| 平台 | 机制 | 签名/校验 | 默认行为 |
| --- | --- | --- | --- |
| macOS | Sparkle 2 appcast + DMG | EdDSA + Apple 公证 | 默认不自动检查；用户同意后检查 |
| Windows | MSIX + `.appinstaller` | Authenticode/MSIX 签名 | 安装后可自动更新；也可关闭 |
| Linux | 包管理器 / 签名 tar.gz | minisign/Sigstore + SHA-256 | 手动更新，无后台检查 |
| Tauri（若采用） | 官方 updater | 强制签名，TLS | 默认不自动检查 |

- macOS 更新包必须公证并 staple；Sparkle 的 EdDSA 私钥离线保存，不进 CI 明文。
- Windows 用 MSIX 的 `OnLaunch`/`AutomaticBackgroundTask` 时注意 `ms-appinstaller:` 默认禁用；`.appinstaller` 要签。
- 更新失败要保留旧版本；不做“强制卸载重装”；灰度 5%→25%→100%，崩溃率超阈值自动停。

### 7.2 崩溃报告

- **默认仅本地**：macOS `~/Library/Logs/DiagnosticReports`、Windows WER、Linux core dump 由系统管理；应用写自己的最小日志（版本、阶段、错误码，不含文本/图片）。
- 可选上传必须逐次同意；使用自托管 Sentry 或等价服务，`send_default_pii=false`，`before_send` 清洗路径/用户名/OCR 文本；禁止附件、截图、内存转储。
- 沙盒版没有网络 entitlement，就不要设计“自动上传”；让用户导出日志文件。
- 崩溃转储可能含图片/文本，按敏感数据处理；保留期 ≤30 天，可删除。

### 7.3 签名/公证

- macOS：Developer ID Application；`codesign --timestamp --options runtime` 逐个签（主程序、helper、dylib、`.so`）；`xcrun notarytool submit --wait`；`xcrun stapler staple`；`spctl -a -vvv` 验证。
- Windows：`signtool sign /fd SHA256 /tr ... /td SHA256` 签 exe 和 MSIX；`signtool verify /pa`；证书放 HSM/Azure Key Vault，CI 用短期凭据。
- Linux：minisign/Sigstore 签 tar.gz；发布 SHA-256 和 SBOM。
- 密钥管理：签名密钥不落开发机；公证 API key 用 CI secret；轮换方案写进 runbook。

### 7.4 可复现构建

- 锁定：`uv.lock`、Cargo.lock、npm lock、GitHub Actions 固定 runner/action SHA；Python 固定 3.12.x。
- 环境：固定容器/VM 镜像；`SOURCE_DATE_EPOCH`、`PYTHONHASHSEED=0`；去掉绝对路径；`--noupx`；固定文件 mtime/顺序。
- 验证：同一源码构建两次，`diffoscope` 对比；PyInstaller 的字节码/归档顺序并非完全可复现，目标是“可重建 + 可验证”，不要承诺 bit-for-bit。
- 发布：签名前记录 SHA-256；发布 SBOM（CycloneDX）和构建 provenance；签名/公证会改变字节，哈希以“未签名产物”和“最终产物”分别记录。

---

## 8. 分阶段路线图

假设 2 名工程师 + 兼职设计/测试；工作量为**人周（pw）**，含测试但不含长期运营。

| 阶段 | 内容 | 工作量 | 退出标准 | 主要风险 |
| --- | --- | --- | --- | --- |
| P0 CLI 硬化 | 核心库化、协议 v1、OCR 适配器、wireframe/embed 回归、隐私扫描、元数据、PyInstaller | 6-8 pw | 三平台 CLI 可装；无 Python/Homebrew；断网可跑；基准不退化 | 现有 runtime `swiftc`、旧 RapidOCR 包、字体路径 |
| P1 macOS | SwiftUI 壳、Vision、预览/编辑、onedir、签名/公证、Sparkle | 14-18 pw | 干净 Mac 安装即用；公证通过；无网络权限；崩溃不丢数据 | 沙盒 sidecar、公证、PyInstaller dylib 签名 |
| P2 Windows | WinUI 3、MSIX 打包、WinOCR + RapidOCR、文件选择器、签名、App Installer | 16-22 pw | 干净 Win10 22H2/Win11 安装；OCR 语言包缺失可回退；SmartScreen 可接受 | 多 exe MSIX、语言包、SmartScreen 信誉、Win10 EOL |
| P3 Linux CLI | onedir/Nuitka、RapidOCR v3、deb/rpm/tar、CI | 4-6 pw | Ubuntu 22.04/24.04 x86_64+aarch64；无 GUI 依赖 | glibc、ONNX 轮子、模型体积 |
| P4 移动 | iOS SwiftUI + Vision 优先；Android ML Kit/PaddleOCR 评估 | 30-50 pw | 分享扩展可用；不上传；离线模型 | 无 Python；内存/耗电；商店审核 |

**总 v1 桌面约 40-55 pw**，2 人约 5-7 个月；如果只做 macOS 先行，P1 可压到 10-12 pw。

**建议顺序**：P0 必须先做，因为“核心库化 + 协议 + 预编译 OCR”是后面所有壳的地基。P1 和 P3 可以并行；P2 在 P1 的协议稳定后启动。移动端不要早于桌面 v1 的 OCR/字体/scene 模型稳定。

---

## 9. 测试策略与发布清单

### 9.1 测试矩阵

| 维度 | 覆盖 |
| --- | --- |
| OS | macOS 13/14/15（arm64；x86_64 至少一个）；Windows 10 22H2、Windows 11；Ubuntu 22.04/24.04 |
| 架构 | macOS arm64/x86_64；Windows x64；Linux x86_64/aarch64 |
| Python | 3.10（最低）、3.12（发布）、3.13（前瞻） |
| OCR | Vision / Windows.Media.Ocr / RapidOCR 三家对同一图集 |
| 输入 | PNG/JPEG/WebP；1x/2x/3x；深色模式；中文/英文/混排；emoji；头像/照片；长截图 |
| 输出 | flat/wireframe；embed/placeholder/outline；文本可选/转 path |
| 安装 | 干净 VM，无 Python/Homebrew/Xcode；断网；安装/卸载/升级 |

### 9.2 测试层次

- **单元**：scene JSON schema、SVG 转义、几何/颜色、OCR 清洗、敏感正则、元数据剥离、权限/路径。
- **黄金文件**：合成截图（HTML/Flutter 渲染，记录 DOM bbox）→ 固定 scene JSON/SVG 结构；浮点数做容差，不做字节级 diff。
- **指标**：文本 CER/WER、行中心误差、基线误差、字号误差、面板 IoU、主色 ΔE、SVG 大小/元素数、重绘后 re-OCR；建议门槛：中心误差中位数 <4 px、文本相似度 >0.95、面板 IoU >0.8（按图集校准）。
- **隐私**：默认/隐私模式无 `<image>`、无 EXIF/GPS、无源文件名、无零宽字符、无外部 href；敏感扫描命中/漏报集。
- **安全**：畸形 JSON/SVG、超大图/解压炸弹、路径穿越/符号链接、socket 未授权、临时文件残留、断网无请求。
- **打包**：`codesign --verify --deep --strict`、`spctl -a -vv`、`stapler validate`、`signtool verify /pa`、MSIX 安装/升级/卸载。
- **性能**：冷/热启动、1080×2400 单图、100 图批量、内存峰值、CPU/耗电；记录基线，PR 回归 >20% 报警。
- **辅助功能**：VoiceOver/Narrator、键盘导航、对比度、减少动态效果；预览不是唯一输出，必须能导出文件。

### 9.3 CI

- GitHub Actions matrix：`macos-14`/`macos-15`（arm64）、`windows-2022`、`ubuntu-22.04`/`24.04`；PR 跑单元+黄金文件，tag 才跑签名/公证。
- 签名 secrets 只在 tag/受保护分支；fork PR 不注入证书；构建产物保留 30 天。
- 每次发布跑一次“无网络”容器/VM 测试；用 `strace`/`dtruss`/Windows Firewall 日志确认无外连。

### 9.4 最小发布清单

1. 版本号、CHANGELOG、协议版本、scene schema 版本一致。
2. `uv.lock`/Cargo.lock/npm lock 更新并提交；依赖漏洞扫描通过。
3. 三平台单元/黄金文件/隐私/安全测试通过；基准无回退。
4. 干净 VM 安装测试（无 Python/Homebrew/Xcode），断网可用。
5. macOS：逐项签名、公证、staple、`spctl` 通过；DMG 签名。
6. Windows：exe + MSIX 签名、`signtool verify`、MSIX 安装/升级/卸载；SmartScreen 记录。
7. Linux：tar.gz/deb/rpm 校验和、minisign/Sigstore、x86_64/aarch64。
8. 模型/字体许可证与 NOTICE 随包；SBOM 生成。
9. 默认无网络请求；更新/崩溃/语言包均为 opt-in。
10. 隐私文案与默认策略一致（embed vs placeholder；`<image>` 说明）。
11. 更新源灰度发布；旧版本可回滚；崩溃率监控（若 opt-in）。
12. 发布公告、下载页、SHA-256、支持入口。

---

## 10. 参考文档

**Apple**：Vision <https://developer.apple.com/documentation/vision/recognizing-text-in-images>；App Sandbox <https://developer.apple.com/documentation/security/protecting-user-data-with-app-sandbox>；helper <https://developer.apple.com/documentation/xcode/embedding-a-helper-tool-in-a-sandboxed-app>；Hardened Runtime <https://developer.apple.com/documentation/security/hardened-runtime>；公证 <https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution>；系统字体 <https://developer.apple.com/fonts/system-fonts/>；SF Symbols <https://developer.apple.com/design/human-interface-guidelines/sf-symbols>；Sparkle <https://sparkle-project.org/documentation/>。

**Tauri（备选）**：Sidecar <https://v2.tauri.app/develop/sidecar/>；macOS 签名 <https://v2.tauri.app/distribute/sign/macos/>；Windows 签名 <https://v2.tauri.app/distribute/sign/windows/>；Updater <https://v2.tauri.app/plugin/updater/>；能力/CSP <https://v2.tauri.app/security/capabilities/>、<https://v2.tauri.app/security/csp/>；基准 <https://github.com/tauri-apps/benchmark_results>。

**Microsoft**：`Windows.Media.Ocr` <https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr?view=winrt-28000>；Windows AI OCR（NPU）<https://learn.microsoft.com/en-us/windows/ai/apis/text-recognition>；WinUI 3 <https://learn.microsoft.com/en-us/windows/apps/winui/winui3/>；Windows App SDK <https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/>；single-project MSIX <https://learn.microsoft.com/en-us/windows/apps/windows-app-sdk/single-project-msix>；打包模型 <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/packaging/>；签名选项 <https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options>；App Installer <https://learn.microsoft.com/en-us/windows/msix/app-installer/app-installer-file-overview>；Win10 生命周期 <https://learn.microsoft.com/en-us/lifecycle/products/windows-10-home-and-pro>。

**Python/打包**：PyInstaller <https://pyinstaller.org/en/stable/usage.html>；Nuitka <https://nuitka.net/doc/user-manual>；uv lock/sync <https://docs.astral.sh/uv/concepts/projects/sync/>；uv build <https://docs.astral.sh/uv/concepts/projects/build/>；PyO3 <https://pyo3.rs/main/>；SOURCE_DATE_EPOCH <https://reproducible-builds.org/docs/source-date-epoch/>。

**OCR/模型/字体/图标**：RapidOCR <https://github.com/RapidAI/RapidOCR/>；模型许可 <https://rapidai.github.io/RapidOCRDocs/main/en/model_licenses/>；PaddleOCR <https://github.com/PaddlePaddle/PaddleOCR>；ONNX Runtime <https://onnxruntime.ai/docs/install/>；裁剪构建 <https://onnxruntime.ai/docs/build/custom.html>；OpenCV 许可 <https://github.com/opencv/opencv/blob/4.x/LICENSE>；Noto CJK <https://github.com/notofonts/noto-cjk>；Source Han Sans <https://github.com/adobe-fonts/source-han-sans>；SIL OFL <https://openfontlicense.org/>；Lucide <https://lucide.dev/license>；Material Symbols <https://developers.google.com/fonts/docs/material_symbols>。
