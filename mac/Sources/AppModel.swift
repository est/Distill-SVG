import AppKit
import Foundation
import UniformTypeIdentifiers

@MainActor
final class AppModel: ObservableObject {
    @Published var inputURL: URL?
    @Published var inputImage: NSImage?
    @Published var pixelSize: CGSize?
    @Published var options = RenderOptions()
    @Published var previewMode: PreviewMode = .original
    @Published var previewFit: PreviewFit = .width

    @Published var isConverting = false
    @Published var stageLabel = ""
    @Published var progress: Double = 0
    @Published var elapsed: Double = 0
    @Published var result: ConvertResult?
    @Published var svgText: String?
    @Published var logs: [String] = []
    @Published var errorMessage: String?
    @Published var coreReady = false
    @Published var showingLog = false
    @Published var showingPythonSetup = false
    @Published var pythonPath = ""
    @Published var isDropTargeted = false

    @Published private(set) var lastRenderedOptions: RenderOptions?

    let core = CoreClient()
    private let workDir: URL
    private var generation = 0
    private var jobInCore: String?
    private var activeOptions: RenderOptions?
    private var pendingRerender = false
    private var discardedJob: String?
    private var cancelRequested = false
    private var convertWhenReady = false
    private var startedAt: Date?
    private var didShutdown = false
    private var ocrTask: Task<Void, Never>?
    private var terminateObserver: NSObjectProtocol?

    private static let supportedExtensions: Set<String> = ["png", "jpg", "jpeg", "webp"]

    var isDirty: Bool {
        guard result != nil else { return false }
        return lastRenderedOptions != options
    }

    var isExternalRuntime: Bool { CoreClient.isExternalRuntime }

    var canSave: Bool { result != nil && svgText != nil }

    var displayName: String {
        inputURL?.lastPathComponent ?? "未选择图片"
    }

    var statsLine: String {
        guard let result else { return "" }
        var parts = ["\(result.width) × \(result.height)"]
        parts.append("\(result.stats.panels) 面板")
        parts.append("\(result.stats.texts) 文本")
        if result.stats.embeddedPhotos > 0 {
            parts.append("\(result.stats.embeddedPhotos) 照片")
        }
        if result.stats.icons > 0 {
            parts.append("\(result.stats.icons) 图标")
        }
        parts.append(Formatters.bytes(result.stats.svgBytes))
        parts.append(Formatters.seconds(elapsed > 0 ? elapsed : result.totalSeconds))
        return parts.joined(separator: "  ·  ")
    }

    init() {
        let base = FileManager.default.temporaryDirectory.appendingPathComponent("DistillSVG", isDirectory: true)
        workDir = base.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)
        cleanupOldWorkDirs(base: base)
        pythonPath = CoreClient.detectedPythonPath() ?? ""

        core.onEvent = { [weak self] event in
            self?.handle(event)
        }
        core.start()

        terminateObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                self?.removeWorkDir()
            }
        }

#if DEBUG_CAPTURE
        if let style = ProcessInfo.processInfo.environment["DISTILL_SVG_STYLE"], style == "wireframe" {
            options.style = .wireframe
            options.photos = .placeholder
        }
        if let icons = ProcessInfo.processInfo.environment["DISTILL_SVG_ICONS"],
           let mode = IconMode(rawValue: icons) {
            options.iconMode = mode
        }
        if let input = ProcessInfo.processInfo.environment["DISTILL_SVG_INPUT"] {
            let url = URL(fileURLWithPath: input)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { [weak self] in
                self?.open(url: url)
            }
        }
#endif
    }

    deinit {
        // `shutdown()` is main-actor; deinit cannot hop. Let the pipe closing
        // on process exit be the fallback and only do a best-effort terminate.
        if let terminateObserver {
            NotificationCenter.default.removeObserver(terminateObserver)
        }
        core.terminate()
        try? FileManager.default.removeItem(at: workDir)
    }

    // MARK: - Files

    func chooseImage() {
        let panel = NSOpenPanel()
        panel.title = "选择截图"
        panel.prompt = "打开"
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.png, .jpeg, .webP]
        if panel.runModal() == .OK, let url = panel.url {
            open(url: url)
        }
    }

    /// Import an image from the general pasteboard.  Screenshot tools provide
    /// TIFF/PNG data while Finder or browser copies can provide a file URL;
    /// both paths end in `open(url:)` because the pipeline needs a real file.
    func pasteImage() {
        let pasteboard = NSPasteboard.general

        if let urls = pasteboard.readObjects(
            forClasses: [NSURL.self],
            options: [.urlReadingFileURLsOnly: true]
        ) as? [URL],
           let url = urls.first(where: { Self.supportedExtensions.contains($0.pathExtension.lowercased()) }) {
            open(url: url)
            return
        }

        let data: Data?
        if let png = pasteboard.data(forType: .png) {
            data = png
        } else if let tiff = pasteboard.data(forType: .tiff),
                  let rep = NSBitmapImageRep(data: tiff) {
            data = rep.representation(using: .png, properties: [:])
        } else if let image = NSImage(pasteboard: pasteboard) {
            data = Self.pngData(from: image)
        } else {
            data = nil
        }

        guard let data else {
            errorMessage = "剪贴板中没有图片。复制一张截图后再试。"
            return
        }

        // The core only accepts file paths, so materialize the paste in this
        // session's work directory and go through the normal open path.
        let url = workDir.appendingPathComponent("clipboard-\(UUID().uuidString.prefix(8)).png")
        do {
            try data.write(to: url, options: .atomic)
        } catch {
            errorMessage = "无法保存剪贴板图片：\(error.localizedDescription)"
            return
        }
        open(url: url)
    }

    /// ⌘V is also the standard text-paste shortcut.  Keep filename fields and
    /// the Python path field working by forwarding the command to a focused
    /// text editor; otherwise import the clipboard image.
    func pasteCommand() {
        if let responder = NSApp.keyWindow?.firstResponder ?? NSApp.mainWindow?.firstResponder {
            if let editor = responder as? NSTextView {
                editor.paste(nil)
                return
            }
            if responder is NSTextField {
                if !NSApp.sendAction(#selector(NSTextView.paste(_:)), to: nil, from: nil) {
                    pasteImage()
                }
                return
            }
        }
        pasteImage()
    }

    private static func pngData(from image: NSImage) -> Data? {
        if let rep = image.representations
            .compactMap({ $0 as? NSBitmapImageRep })
            .max(by: { $0.pixelsWide * $0.pixelsHigh < $1.pixelsWide * $1.pixelsHigh }),
           let png = rep.representation(using: .png, properties: [:]) {
            return png
        }
        if let tiff = image.tiffRepresentation,
           let rep = NSBitmapImageRep(data: tiff),
           let png = rep.representation(using: .png, properties: [:]) {
            return png
        }
        var rect = CGRect(origin: .zero, size: image.size)
        guard let cgImage = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
            return nil
        }
        return NSBitmapImageRep(cgImage: cgImage).representation(using: .png, properties: [:])
    }

    /// Clear the current document without restarting the app or the core.
    /// Render options are intentionally preserved.  An in-flight job is
    /// cancelled and marked discarded; `jobInCore` is kept as the core-busy
    /// marker so a later paste queues behind the cancel instead of colliding
    /// with the single-job worker.
    func newDocument() {
        ocrTask?.cancel()
        ocrTask = nil
        generation += 1

        if let job = jobInCore {
            discardedJob = job
            core.cancel(id: job)
        }
        pendingRerender = false
        cancelRequested = false
        convertWhenReady = false
        isConverting = false
        startedAt = nil
        activeOptions = nil

        inputURL = nil
        inputImage = nil
        pixelSize = nil
        result = nil
        svgText = nil
        lastRenderedOptions = nil
        errorMessage = nil
        logs.removeAll()
        progress = 0
        stageLabel = ""
        elapsed = 0
        previewMode = .original
    }

    func open(url: URL) {
        guard Self.supportedExtensions.contains(url.pathExtension.lowercased()) else {
            errorMessage = "只支持 PNG / JPEG / WebP 图片。"
            return
        }
        guard let image = NSImage(contentsOf: url) else {
            errorMessage = "无法读取图片：\(url.lastPathComponent)"
            return
        }
        inputURL = url
        inputImage = image
        pixelSize = VisionOCR.pixelSize(of: url) ?? inputImage?.size
        result = nil
        svgText = nil
        lastRenderedOptions = nil
        errorMessage = nil
        logs.removeAll()
        previewMode = .original
        progress = 0
        stageLabel = "准备转换"
        convert()
    }

    func convert() {
        guard inputURL != nil else { return }
        // `jobInCore` can outlive `isConverting` after `newDocument()` marks a
        // cancelled job as discarded; it still means the worker is busy.
        if let running = jobInCore {
            discardedJob = running
            pendingRerender = true
            // After `newDocument()` this flag is already false; turning it
            // back on keeps the waiting-for-cancel state visible.
            isConverting = true
            requestCancel(explicit: false)
            return
        }
        if !coreReady {
            convertWhenReady = true
            errorMessage = "转换核心未运行，请从「转换 → 重新启动核心」恢复。"
            return
        }
        generation += 1
        let gen = generation
        ocrTask?.cancel()
        isConverting = true
        errorMessage = nil
        startedAt = Date()
        progress = 0.02
        stageLabel = "识别文字"
        ocrTask = Task { [weak self] in
            await self?.runConversion(generation: gen)
        }
    }

    private func runConversion(generation gen: Int) async {
        guard let inputURL else { return }
        let snapshot = options
        activeOptions = snapshot
        do {
            let lines = try await VisionOCR.recognize(
                url: inputURL,
                languages: snapshot.languages.visionLanguages
            )
            guard gen == generation, !Task.isCancelled else { return }
            guard core.isRunning else {
                isConverting = false
                jobInCore = nil
                if !coreReady {
                    convertWhenReady = true
                    stageLabel = "等待核心…"
                } else {
                    errorMessage = "转换核心未运行，请从「转换 → 重新启动核心」恢复。"
                }
                return
            }
            let jobID = UUID().uuidString
            let short = jobID.prefix(8)
            let stem = "render-\(snapshot.style.rawValue)-\(short)"
            let brain = workDir.appendingPathComponent(stem + ".svg")
            let scene = workDir.appendingPathComponent(stem + ".json")
            jobInCore = jobID
            stageLabel = "重建布局"
            progress = 0.2
            core.convert(
                id: jobID,
                image: inputURL,
                output: brain,
                scene: scene,
                options: snapshot,
                ocr: lines
            )
        } catch {
            guard gen == generation else { return }
            isConverting = false
            errorMessage = error.localizedDescription
        }
    }

    func cancel() {
        pendingRerender = false
        ocrTask?.cancel()
        requestCancel(explicit: true)
    }

    private func requestCancel(explicit: Bool) {
        cancelRequested = explicit
        guard let job = jobInCore else {
            // Still in the OCR phase; invalidate the running task.
            if explicit {
                generation += 1
                isConverting = false
                stageLabel = "已取消"
                progress = 0
            }
            return
        }
        stageLabel = "正在取消…"
        core.cancel(id: job)
    }

    func setPythonPath(_ path: String) {
        let trimmed = (path.trimmingCharacters(in: .whitespacesAndNewlines) as NSString).expandingTildeInPath
        guard !trimmed.isEmpty else {
            errorMessage = "请选择一个 Python 可执行文件。"
            return
        }
        pythonPath = trimmed
        CoreClient.savePythonPath(trimmed)
        showingPythonSetup = false
        restartCore()
    }

    func choosePython() {
        let panel = NSOpenPanel()
        panel.title = "选择 Python 解释器"
        panel.prompt = "选择"
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        panel.message = "请选择已安装 numpy、opencv-python、Pillow 的 Python 3.10+ 可执行文件。"
        if panel.runModal() == .OK, let url = panel.url {
            setPythonPath(url.path)
        }
    }

    func copyPipCommand() {
        let python = pythonPath.isEmpty ? "python3" : pythonPath
        let command = "\(python) -m pip install -r \(CoreClient.externalRequirements.path)"
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(command, forType: .string)
    }

    func revealPythonRequirements() {
        let url = CoreClient.externalRequirements
        if FileManager.default.fileExists(atPath: url.path) {
            NSWorkspace.shared.activateFileViewerSelecting([url])
        } else {
            NSWorkspace.shared.open(CoreClient.externalSourceDir)
        }
    }

    func restartCore() {
        ocrTask?.cancel()
        ocrTask = nil
        generation += 1
        isConverting = false
        jobInCore = nil
        discardedJob = nil
        pendingRerender = false
        cancelRequested = false
        convertWhenReady = false
        startedAt = nil
        progress = 0
        coreReady = false
        stageLabel = "正在启动核心…"
        errorMessage = nil
        core.restart()
    }

    func shutdown() {
        didShutdown = true
        ocrTask?.cancel()
        core.shutdown()
        removeWorkDir()
    }

    // MARK: - Saving

    func saveSVG() {
        guard let svgText, let inputURL else { return }
        let panel = NSSavePanel()
        panel.title = "保存 SVG"
        panel.prompt = "保存"
        panel.canCreateDirectories = true
        panel.allowedContentTypes = [.svg]
        let suffix = lastRenderedOptions?.style == .wireframe ? "-wireframe" : "-flat"
        panel.nameFieldStringValue = inputURL.deletingPathExtension().lastPathComponent + suffix + ".svg"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try svgText.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            errorMessage = "保存失败：\(error.localizedDescription)"
        }
    }

    func exportScene() {
        guard let scenePath = result?.scenePath else { return }
        let panel = NSSavePanel()
        panel.title = "导出场景 JSON"
        panel.prompt = "导出"
        panel.canCreateDirectories = true
        panel.allowedContentTypes = [.json]
        let stem = inputURL?.deletingPathExtension().lastPathComponent ?? "scene"
        panel.nameFieldStringValue = stem + ".json"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            if FileManager.default.fileExists(atPath: url.path) {
                try FileManager.default.removeItem(at: url)
            }
            try FileManager.default.copyItem(at: URL(fileURLWithPath: scenePath), to: url)
        } catch {
            errorMessage = "导出失败：\(error.localizedDescription)"
        }
    }

    func copySVG() {
        guard let svgText else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(svgText, forType: .string)
    }

    func revealInFinder() {
        guard let result else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result.svgPath)])
    }

    // MARK: - Core events

    private func handle(_ event: CoreEvent) {
        switch event {
        case let .ready(core):
            coreReady = true
            if isExternalRuntime { showingPythonSetup = false }
            logs.append("核心 \(core) 已就绪")
            if convertWhenReady {
                convertWhenReady = false
                convert()
            }

        case let .progress(id, _, pct, label):
            guard isConverting, id == jobInCore, id != discardedJob else { return }
            if !label.isEmpty { stageLabel = label }
            progress = min(0.99, 0.2 + pct * 0.8)

        case let .log(id, message):
            guard id == jobInCore, id != discardedJob else { return }
            logs.append(message)
            if logs.count > 500 { logs.removeFirst(logs.count - 500) }

        case let .result(id, convertResult):
            if id == discardedJob {
                // A superseded render finished before it noticed the cancel.
                finishDiscardedJob()
                return
            }
            guard id == jobInCore else { return }
            jobInCore = nil
            discardedJob = nil
            guard let svg = try? String(contentsOfFile: convertResult.svgPath, encoding: .utf8) else {
                fail("无法读取生成的 SVG")
                return
            }
            svgText = svg
            result = convertResult
            lastRenderedOptions = activeOptions ?? options
            elapsed = startedAt.map { Date().timeIntervalSince($0) } ?? 0
            startedAt = nil
            isConverting = false
            progress = 1
            stageLabel = "完成"
            previewMode = .result
            if pendingRerender {
                pendingRerender = false
                convert()
            }

        case let .error(id, code, message):
            if let id, id == discardedJob {
                finishDiscardedJob()
                return
            }
            if let id, !id.isEmpty, id != jobInCore { return }
            jobInCore = nil
            discardedJob = nil
            pendingRerender = false
            isConverting = false
            startedAt = nil
            stageLabel = ""
            if code == "CORE_MISSING" || code == "CORE_START_FAILED" {
                coreReady = false
            }
            if code == "PYTHON_NOT_FOUND" {
                coreReady = false
                showingPythonSetup = true
            }
            errorMessage = message

        case let .cancelled(id):
            if id == discardedJob {
                finishDiscardedJob()
                return
            }
            guard id == jobInCore else { return }
            jobInCore = nil
            if pendingRerender {
                pendingRerender = false
                isConverting = false
                convert()
                return
            }
            isConverting = false
            startedAt = nil
            stageLabel = cancelRequested ? "已取消" : ""
            progress = 0
            cancelRequested = false

        case let .exited(status, stderr):
            coreReady = false
            isConverting = false
            jobInCore = nil
            discardedJob = nil
            pendingRerender = false
            cancelRequested = false
            startedAt = nil
            guard !didShutdown else { return }
            let tail = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            if !tail.isEmpty {
                logs.append("core stderr:\n\(tail.suffix(2000))")
            }
            if CoreClient.isExternalRuntime {
                let lower = tail.lowercased()
                if lower.contains("no module named") || lower.contains("modulenotfounderror")
                    || lower.contains("syntaxerror") || lower.contains("unsupported operand") {
                    showingPythonSetup = true
                    errorMessage = "外部 Python 缺少依赖或版本过低，请重新选择 Python 环境。"
                    return
                }
            }
            errorMessage = "转换核心已退出（状态 \(status)）。可从「转换 → 重新启动核心」恢复。"
        }
    }

    private func fail(_ message: String) {
        isConverting = false
        startedAt = nil
        jobInCore = nil
        errorMessage = message
    }

    private func finishDiscardedJob() {
        // A newer document may already have started its own conversion while
        // the superseded job was still shutting down.  Do not clear that job's
        // state (or its `isConverting` / progress) here.
        if let current = jobInCore, current != discardedJob {
            discardedJob = nil
            return
        }
        jobInCore = nil
        discardedJob = nil
        startedAt = nil
        if pendingRerender {
            pendingRerender = false
            convert()
            return
        }
        isConverting = false
        progress = 0
        stageLabel = cancelRequested ? "已取消" : ""
        cancelRequested = false
    }

    private func removeWorkDir() {
        try? FileManager.default.removeItem(at: workDir)
    }

    private func cleanupOldWorkDirs(base: URL) {
        let manager = FileManager.default
        guard let entries = try? manager.contentsOfDirectory(
            at: base,
            includingPropertiesForKeys: [.contentModificationDateKey],
            options: [.skipsHiddenFiles]
        ) else { return }
        let cutoff = Date().addingTimeInterval(-24 * 3600)
        for entry in entries {
            guard let values = try? entry.resourceValues(forKeys: [.contentModificationDateKey]),
                  let modified = values.contentModificationDate,
                  modified < cutoff else { continue }
            try? manager.removeItem(at: entry)
        }
    }
}
