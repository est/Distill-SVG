import AppKit
import SwiftUI
import UniformTypeIdentifiers
import WebKit

// MARK: - App

@main
struct DistillSVGApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup("蒸矢 DistillSVG") {
            ContentView(model: model)
                .onOpenURL { model.open(url: $0) }
        }
        .defaultSize(width: 1180, height: 780)
        .windowToolbarStyle(.unified)
        .commands { AppCommands(model: model) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

#if DEBUG_CAPTURE
    // Development-only window capture used while iterating on the UI.  The
    // shipping build never defines DEBUG_CAPTURE.
    func applicationDidFinishLaunching(_ notification: Notification) {
        let env = ProcessInfo.processInfo.environment
        guard let path = env["DISTILL_SVG_CAPTURE"] else { return }
        let delay = Double(env["DISTILL_SVG_CAPTURE_DELAY"] ?? "2.5") ?? 2.5
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            guard let window = NSApplication.shared.windows.first(where: { $0.isVisible }),
                  let view = window.contentView else {
                FileHandle.standardError.write(Data("capture: no window\n".utf8))
                return
            }
            let bounds = view.bounds
            var data: Data?
            if let cgImage = CGWindowListCreateImage(
                .null,
                .optionIncludingWindow,
                CGWindowID(window.windowNumber),
                [.boundsIgnoreFraming, .bestResolution]
            ) {
                let rep = NSBitmapImageRep(cgImage: cgImage)
                data = rep.representation(using: .png, properties: [:])
            }
            if data == nil, let rep = view.bitmapImageRepForCachingDisplay(in: bounds) {
                view.cacheDisplay(in: bounds, to: rep)
                data = rep.representation(using: .png, properties: [:])
            }
            if let data {
                try? data.write(to: URL(fileURLWithPath: path))
                FileHandle.standardError.write(Data("capture: wrote \(path)\n".utf8))
            } else {
                FileHandle.standardError.write(Data("capture: failed\n".utf8))
            }
            if env["DISTILL_SVG_CAPTURE_QUIT"] == "1" {
                NSApplication.shared.terminate(nil)
            }
        }
    }
#endif
}

struct AppCommands: Commands {
    @ObservedObject var model: AppModel

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("打开图片…") { model.chooseImage() }
                .keyboardShortcut("o")
            Divider()
            Button("保存 SVG…") { model.saveSVG() }
                .keyboardShortcut("s")
                .disabled(!model.canSave)
            Button("导出场景 JSON…") { model.exportScene() }
                .keyboardShortcut("e", modifiers: [.command, .shift])
                .disabled(model.result?.scenePath == nil)
            Button("复制 SVG") { model.copySVG() }
                .keyboardShortcut("c", modifiers: [.command, .option])
                .disabled(!model.canSave)
        }
        CommandMenu("转换") {
            Button(model.result == nil ? "开始转换" : "重新生成") { model.convert() }
                .keyboardShortcut("r")
                .disabled(model.inputURL == nil || model.isConverting)
            Button("取消转换") { model.cancel() }
                .keyboardShortcut(".", modifiers: .command)
                .disabled(!model.isConverting)
            Divider()
            Button("重新启动核心") { model.restartCore() }
            Button("Python 环境…") { model.showingPythonSetup = true }
                .disabled(!model.isExternalRuntime)
            Button("查看日志…") { model.showingLog = true }
        }
    }
}

// MARK: - Window

struct ContentView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        HStack(spacing: 0) {
            PreviewPane(model: model)
            Divider()
            Sidebar(model: model)
                .frame(width: 308)
        }
        .frame(minWidth: 900, minHeight: 600)
        .background(Color(nsColor: .windowBackgroundColor))
        .onDrop(of: [.fileURL], isTargeted: $model.isDropTargeted) { providers in
            handleDrop(providers)
        }
        .toolbar { toolbar }
        .sheet(isPresented: $model.showingLog) {
            LogSheet(model: model)
        }
        .sheet(isPresented: $model.showingPythonSetup) {
            PythonSetupView(model: model)
        }
        .navigationTitle(model.inputURL.map { "\($0.lastPathComponent) — 蒸矢" } ?? "蒸矢 DistillSVG")
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .navigation) {
            Button { model.chooseImage() } label: {
                Label("打开", systemImage: "folder")
            }
            .help("打开截图 (⌘O)")
        }
        ToolbarItemGroup(placement: .primaryAction) {
            Button { model.convert() } label: {
                Label("重新生成", systemImage: "arrow.triangle.2.circlepath")
            }
            .help("用当前设置重新生成 (⌘R)")
            .disabled(model.inputURL == nil || model.isConverting)

            Button { model.saveSVG() } label: {
                Label("保存 SVG", systemImage: "square.and.arrow.down")
            }
            .help("保存 SVG (⌘S)")
            .disabled(!model.canSave)

            Menu {
                Button("在 Finder 中显示") { model.revealInFinder() }
                    .disabled(!model.canSave)
                Button("复制 SVG") { model.copySVG() }
                    .disabled(!model.canSave)
                Button("导出场景 JSON…") { model.exportScene() }
                    .disabled(model.result?.scenePath == nil)
                Divider()
                Button("Python 环境…") { model.showingPythonSetup = true }
                    .disabled(!model.isExternalRuntime)
                Button("查看日志…") { model.showingLog = true }
                Button("重新启动核心") { model.restartCore() }
            } label: {
                Label("更多", systemImage: "ellipsis.circle")
            }
        }
    }

    private func handleDrop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first(where: { $0.canLoadObject(ofClass: URL.self) }) else {
            return false
        }
        _ = provider.loadObject(ofClass: URL.self) { url, _ in
            guard let url else { return }
            DispatchQueue.main.async { model.open(url: url) }
        }
        return true
    }
}

// MARK: - Preview pane

private struct PreviewPane: View {
    @ObservedObject var model: AppModel
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(spacing: 0) {
            if model.inputImage != nil {
                topBar
                Divider()
            }
            ZStack {
                canvas
                if model.inputURL == nil {
                    EmptyState(model: model, targeted: model.isDropTargeted)
                }
                if model.isConverting {
                    ProgressOverlay(model: model)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            if model.result != nil {
                statusBar
            }
        }
        .background(backgroundColor)
    }

    private var backgroundColor: Color {
        colorScheme == .dark ? Color(red: 0.11, green: 0.115, blue: 0.125) : Color(red: 0.945, green: 0.95, blue: 0.958)
    }

    private var topBar: some View {
        HStack(spacing: 12) {
            if model.result != nil {
                Picker("预览", selection: $model.previewMode) {
                    ForEach(PreviewMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 148)
            } else {
                Text("预览")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
            }

            Spacer()

            if model.inputImage != nil {
                Picker("缩放", selection: $model.previewFit) {
                    ForEach(PreviewFit.allCases) { fit in
                        Text(fit.title).tag(fit)
                    }
                }
                .pickerStyle(.menu)
                .labelsHidden()
                .frame(width: 104)
            }

            if let size = model.pixelSize {
                Text("\(Int(size.width)) × \(Int(size.height))")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 9)
        .background(.bar)
    }

    @ViewBuilder
    private var canvas: some View {
        if model.previewMode == .original, let image = model.inputImage {
            OriginalPreview(image: image, fitWidth: model.previewFit == .width)
        } else if let svg = model.svgText {
            SVGPreview(svg: svg, dark: colorScheme == .dark, fitWidth: model.previewFit == .width)
        } else if let image = model.inputImage {
            OriginalPreview(image: image, fitWidth: model.previewFit == .width)
        }
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundStyle(.green)
                .font(.system(size: 11))
            Text(model.displayName)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer(minLength: 12)
            Text(model.statsLine)
                .monospacedDigit()
                .lineLimit(1)
        }
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }
}

private struct EmptyState: View {
    @ObservedObject var model: AppModel
    let targeted: Bool

    var body: some View {
        VStack(spacing: 16) {
            ZStack {
                Circle()
                    .fill(Color.accentColor.opacity(0.10))
                    .frame(width: 92, height: 92)
                Image(systemName: "photo.on.rectangle.angled")
                    .font(.system(size: 36, weight: .light))
                    .foregroundStyle(.tint)
            }
            VStack(spacing: 6) {
                Text("拖入一张截图")
                    .font(.system(size: 21, weight: .semibold))
                Text("UI 会被重建为可编辑 SVG：文字可选中，图标变成路径。")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            Button("选择图片…") { model.chooseImage() }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            Text("PNG · JPEG · WebP    全程本地处理")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(40)
        .background(
            RoundedRectangle(cornerRadius: 18)
                .fill(Color(nsColor: .controlBackgroundColor).opacity(0.55))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(
                    targeted ? Color.accentColor : Color.secondary.opacity(0.28),
                    style: StrokeStyle(lineWidth: 1.5, dash: [8, 6])
                )
        )
        .padding(48)
        .animation(.easeOut(duration: 0.15), value: targeted)
    }
}

private struct ProgressOverlay: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text(model.stageLabel.isEmpty ? "处理中…" : model.stageLabel)
                    .font(.system(size: 12, weight: .medium))
                Spacer(minLength: 16)
                Text("\(Int(model.progress * 100))%")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(.secondary)
                Button("取消") { model.cancel() }
                    .buttonStyle(.link)
                    .font(.system(size: 11))
            }
            ProgressView(value: model.progress)
                .progressViewStyle(.linear)
        }
        .padding(14)
        .frame(width: 340)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(Color.primary.opacity(0.08))
        )
        .shadow(color: .black.opacity(0.18), radius: 18, y: 8)
        .padding(28)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}

private struct OriginalPreview: View {
    let image: NSImage
    let fitWidth: Bool

    var body: some View {
        GeometryReader { geo in
            if fitWidth {
                ScrollView(.vertical) {
                    Image(nsImage: image)
                        .resizable()
                        .interpolation(.high)
                        .aspectRatio(contentMode: .fit)
                        .frame(width: max(160, geo.size.width - 56))
                        .background(Color.white)
                        .shadow(color: .black.opacity(0.22), radius: 18, y: 8)
                        .padding(.horizontal, 28)
                        .padding(.vertical, 22)
                }
            } else {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: geo.size.width - 56, maxHeight: geo.size.height - 56)
                    .background(Color.white)
                    .shadow(color: .black.opacity(0.22), radius: 18, y: 8)
                    .frame(width: geo.size.width, height: geo.size.height)
            }
        }
    }
}

// MARK: - WKWebView preview

private struct SVGPreview: NSViewRepresentable {
    let svg: String
    let dark: Bool
    let fitWidth: Bool

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = false
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.allowsMagnification = true
        view.underPageBackgroundColor = .clear
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        let html = Self.html(for: svg, dark: dark, fitWidth: fitWidth)
        guard context.coordinator.lastHTML != html else { return }
        context.coordinator.lastHTML = html
        view.loadHTMLString(html, baseURL: nil)
    }

    static func html(for svg: String, dark: Bool, fitWidth: Bool) -> String {
        var body = svg
        if body.hasPrefix("<?xml") {
            if let range = body.range(of: "?>") {
                body = String(body[range.upperBound...])
            }
        }
        let background = dark ? "#111214" : "#f1f2f5"
        let shadow = dark ? "0 10px 34px rgba(0,0,0,.55)" : "0 8px 26px rgba(20,28,45,.16)"
        let css: String
        if fitWidth {
            css = """
            html{background:\(background);}
            body{margin:0;padding:26px 28px;box-sizing:border-box;background:\(background);}
            svg{display:block;width:100%;height:auto;background:#fff;
                box-shadow:\(shadow);border-radius:6px;}
            """
        } else {
            css = """
            html,body{margin:0;padding:0;height:100%;overflow:hidden;background:\(background);}
            body{display:flex;align-items:center;justify-content:center;padding:28px;box-sizing:border-box;}
            svg{max-width:100%;max-height:100%;width:auto;height:auto;background:#fff;
                box-shadow:\(shadow);border-radius:6px;}
            """
        }
        return """
        <!doctype html><html><head><meta charset="utf-8"><style>
        \(css)
        </style></head><body>\(body)</body></html>
        """
    }

    final class Coordinator {
        var lastHTML: String?
    }
}

// MARK: - Sidebar

private struct Sidebar: View {
    @ObservedObject var model: AppModel
#if DEBUG_CAPTURE
    @State private var advancedOpen = ProcessInfo.processInfo.environment["DISTILL_SVG_ADVANCED"] == "1"
#else
    @State private var advancedOpen = false
#endif

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if let message = model.errorMessage {
                        ErrorBanner(
                            message: message,
                            actionTitle: model.isExternalRuntime ? "设置 Python…" : nil,
                            action: { model.showingPythonSetup = true },
                            dismiss: { model.errorMessage = nil }
                        )
                    }
                    sourceCard
                    styleSection
                    photoSection
                    speedSection
                    advancedSection
                }
                .padding(16)
            }
            Divider()
            actionBar
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: Source

    private var sourceCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let image = model.inputImage {
                HStack(spacing: 10) {
                    Image(nsImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: 44, height: 44)
                        .clipShape(RoundedRectangle(cornerRadius: 7))
                        .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(Color.primary.opacity(0.08)))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(model.displayName)
                            .font(.system(size: 12.5, weight: .medium))
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Text(sourceSubtitle)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                    Button("更换") { model.chooseImage() }
                        .buttonStyle(.link)
                        .font(.system(size: 11))
                }
            } else {
                Button { model.chooseImage() } label: {
                    HStack(spacing: 9) {
                        Image(systemName: "plus.circle.fill")
                            .foregroundStyle(.tint)
                        Text("选择一张截图")
                            .font(.system(size: 12.5, weight: .medium))
                        Spacer()
                    }
                    .padding(10)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var sourceSubtitle: String {
        guard let size = model.pixelSize else { return "未转换" }
        var parts = ["\(Int(size.width)) × \(Int(size.height))"]
        if model.isConverting { parts.append("转换中") }
        return parts.joined(separator: "  ·  ")
    }

    // MARK: Style

    private var styleSection: some View {
        SidebarSection("输出风格") {
            HStack(spacing: 8) {
                ForEach(OutputStyle.allCases) { style in
                    StyleCard(style: style, selected: model.options.style == style) {
                        model.options.style = style
                    }
                }
            }
        }
    }

    // MARK: Photos

    private var photoSection: some View {
        SidebarSection("照片与头像") {
            Picker("照片", selection: Binding(
                get: { model.options.photos },
                set: { mode in
                    model.options.photos = mode
                    if mode != .embed, model.options.iconMode == .embed {
                        model.options.iconMode = .vector
                    }
                }
            )) {
                ForEach(PhotoMode.allCases) { mode in
                    Text(mode.title).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            Text(model.options.photos.help)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            if model.options.photos == .embed {
                Label("嵌入会保留照片像素，导出前请确认不含敏感内容。", systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)

                Button("一键隐私模式") {
                    model.options.style = .wireframe
                    model.options.photos = .placeholder
                    model.options.iconMode = .vector
                    model.convert()
                }
                .buttonStyle(.link)
                .font(.system(size: 11))
            }
        }
    }

    // MARK: Speed

    private var speedSection: some View {
        SidebarSection("速度") {
            Picker("速度", selection: $model.options.quality) {
                ForEach(RenderOptions.Quality.allCases) { quality in
                    Text(quality.title).tag(quality)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            Text(model.options.quality == .preview
                 ? "跳过字体投票，适合边调边看；图标仍按「图标」选项处理。"
                 : "包含字体匹配与图标处理，导出质量最好。")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    // MARK: Advanced

    private var advancedSection: some View {
        DisclosureGroup(isExpanded: $advancedOpen) {
            VStack(alignment: .leading, spacing: 11) {
                row("分隔线") {
                    Picker("", selection: $model.options.strokes) {
                        ForEach(StrokeMode.allCases) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .frame(width: 88)
                }

                row("分析精度") {
                    Picker("", selection: $model.options.scale) {
                        Text("1×").tag(1)
                        Text("2×").tag(2)
                        Text("3×").tag(3)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 108)
                }

                row("识别语言") {
                    Picker("", selection: $model.options.languages) {
                        ForEach(LanguageChoice.allCases) { choice in
                            Text(choice.title).tag(choice)
                        }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .frame(width: 108)
                }

                row("调色板") {
                    HStack(spacing: 6) {
                        Text("\(model.options.palette) 色")
                            .monospacedDigit()
                            .foregroundStyle(.secondary)
                        Stepper("", value: $model.options.palette, in: 6...32, step: 2)
                            .labelsHidden()
                    }
                }

                row("图标") {
                    Picker("", selection: $model.options.iconMode) {
                        ForEach(IconMode.allCases.filter {
                            $0 != .embed || model.options.photos == .embed
                        }) { mode in
                            Text(mode.title).tag(mode)
                        }
                    }
                    .pickerStyle(.menu)
                    .labelsHidden()
                    .frame(width: 108)
                    .help(model.options.iconMode.help)
                }

                if model.options.iconMode == .embed {
                    Text("抠图会把图标像素嵌入 SVG；线框模式下自动灰度，照片占位/轮廓模式下自动切回矢量。")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Toggle("照片灰度", isOn: $model.options.photoGrayscale)
                    .toggleStyle(.checkbox)
                    .font(.system(size: 12))

                Toggle("去除低位像素", isOn: $model.options.photoScrub)
                    .toggleStyle(.checkbox)
                    .font(.system(size: 12))
                    .help("丢弃每通道低 2 位，减轻像素级隐写")

                Toggle("保留照片内文字", isOn: $model.options.keepPhotoText)
                    .toggleStyle(.checkbox)
                    .font(.system(size: 12))
                    .help("默认会移除照片区域内的重复 OCR 文本")

                if model.options.photos == .embed {
                    Divider()
                    row("照片格式") {
                        Picker("", selection: $model.options.photoFormat) {
                            ForEach(PhotoEncoding.allCases) { encoding in
                                Text(encoding.title).tag(encoding)
                            }
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                        .frame(width: 108)
                    }
                    if model.options.photoFormat == .jpeg {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text("JPEG 质量")
                                Spacer()
                                Text("\(model.options.photoQuality)")
                                    .monospacedDigit()
                                    .foregroundStyle(.secondary)
                            }
                            Slider(
                                value: Binding(
                                    get: { Double(model.options.photoQuality) },
                                    set: { model.options.photoQuality = Int($0) }
                                ),
                                in: 40...100,
                                step: 1
                            )
                        }
                        .font(.system(size: 12))
                    }
                    row("最大边长") {
                        HStack(spacing: 6) {
                            Text("\(model.options.photoMaxSize) px")
                                .monospacedDigit()
                                .foregroundStyle(.secondary)
                            Stepper("", value: $model.options.photoMaxSize, in: 400...4000, step: 200)
                                .labelsHidden()
                        }
                    }
                }
            }
            .padding(.top, 10)
        } label: {
            Text("高级")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
        }
    }

    private func row<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 12))
            Spacer(minLength: 8)
            content()
        }
    }

    // MARK: Actions

    private var saveButton: some View {
        Button { model.saveSVG() } label: {
            Label("保存 SVG…", systemImage: "square.and.arrow.down")
                .frame(maxWidth: .infinity)
        }
        .controlSize(.large)
        .disabled(!model.canSave)
    }

    private var actionBar: some View {
        VStack(spacing: 9) {
            if model.isDirty {
                Button { model.convert() } label: {
                    Label("重新生成", systemImage: "arrow.triangle.2.circlepath")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(model.isConverting)
            }

            if model.isDirty {
                saveButton
                    .buttonStyle(.bordered)
            } else {
                saveButton
                    .buttonStyle(.borderedProminent)
            }

            HStack(spacing: 14) {
                Button { model.copySVG() } label: {
                    Label("复制", systemImage: "doc.on.doc")
                }
                .disabled(!model.canSave)
                Button { model.revealInFinder() } label: {
                    Label("显示", systemImage: "folder")
                }
                .disabled(!model.canSave)
                Spacer()
                Button("日志") { model.showingLog = true }
            }
            .font(.system(size: 11))
            .buttonStyle(.link)
        }
        .padding(14)
        .background(.bar)
    }
}

private struct SidebarSection<Content: View>: View {
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            content
        }
    }
}

private struct StyleCard: View {
    let style: OutputStyle
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 5) {
                    Image(systemName: style.symbol)
                        .font(.system(size: 12, weight: .medium))
                    Text(style.title)
                        .font(.system(size: 12.5, weight: .semibold))
                    Spacer(minLength: 0)
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 11))
                            .foregroundStyle(.tint)
                    }
                }
                Text(style.subtitle)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(selected ? Color.accentColor.opacity(0.12) : Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(selected ? Color.accentColor.opacity(0.8) : Color.primary.opacity(0.08),
                                  lineWidth: selected ? 1.5 : 1)
            )
        }
        .buttonStyle(.plain)
    }
}

private struct ErrorBanner: View {
    let message: String
    let actionTitle: String?
    let action: () -> Void
    let dismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 7) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                Text(message)
                    .font(.system(size: 11.5))
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Button(action: dismiss) {
                    Image(systemName: "xmark")
                        .font(.system(size: 9, weight: .bold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
            }
            if let actionTitle {
                Button(actionTitle, action: action)
                    .buttonStyle(.link)
                    .font(.system(size: 11))
            }
        }
        .padding(9)
        .background(Color.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.red.opacity(0.18)))
    }
}

private struct LogSheet: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("转换日志")
                    .font(.headline)
                Spacer()
                Button("复制") {
                    let pasteboard = NSPasteboard.general
                    pasteboard.clearContents()
                    pasteboard.setString(model.logs.joined(separator: "\n"), forType: .string)
                }
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(14)
            Divider()
            ScrollView {
                Text(model.logs.isEmpty ? "暂无日志" : model.logs.joined(separator: "\n"))
                    .font(.system(size: 11.5, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
            }
        }
        .frame(width: 660, height: 420)
    }
}
