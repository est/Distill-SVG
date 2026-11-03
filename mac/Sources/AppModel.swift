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
        if isConverting, let running = jobInCore {
            discardedJob = running
            pendingRerender = true
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
