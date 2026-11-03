import AppKit
import Foundation

// MARK: - Wire types

struct OCRLine: Codable {
    let text: String
    let confidence: Double
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}

struct ConvertStats: Decodable {
    let panels: Int
    let texts: Int
    let images: Int
    let embeddedPhotos: Int
    let icons: Int
    let strokes: Int
    let svgBytes: Int
    let ocrLines: Int
}

struct ConvertResult: Decodable {
    let svgPath: String
    let scenePath: String?
    let width: Int
    let height: Int
    let background: String
    let fontFamily: String
    let stats: ConvertStats
    let timings: [String: Double]
    let log: [String]

    var totalSeconds: Double {
        timings.values.reduce(0, +)
    }
}

enum CoreEvent {
    case ready(core: String)
    case progress(id: String, stage: String, pct: Double, label: String)
    case log(id: String, message: String)
    case result(id: String, result: ConvertResult)
    case error(id: String?, code: String, message: String)
    case cancelled(id: String)
    case exited(status: Int32, stderr: String)
}

// MARK: - NDJSON client

/// Owns the `distill-core` helper process and turns its NDJSON stdout into
/// events on the main queue.  One instance lives for the whole app session, so
/// the expensive `cv2` import happens once.
final class CoreClient {
    var onEvent: ((CoreEvent) -> Void)?

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutPipe: Pipe?
    private var stderrPipe: Pipe?
    private var stdoutBuffer = Data()
    private var stderrText = ""
    private(set) var isRunning = false

    static func locateCore() -> URL? {
        if let override = ProcessInfo.processInfo.environment["DISTILL_SVG_CORE"],
           FileManager.default.isExecutableFile(atPath: override) {
            return URL(fileURLWithPath: override)
        }
        // DistillSVG.app/Contents/MacOS/DistillSVG
        let bundled = Bundle.main.bundleURL
            .appendingPathComponent("Contents/Resources/core/distill-core")
        if FileManager.default.isExecutableFile(atPath: bundled.path) {
            return bundled
        }
        // Development fallback: run the SwiftUI binary straight from dist/.
        let dev = Bundle.main.bundleURL
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("build/core/dist/distill-core/distill-core")
        if FileManager.default.isExecutableFile(atPath: dev.path) {
            return dev
        }
        return nil
    }

    func start() {
        guard !isRunning else { return }
        guard let executable = Self.locateCore() else {
            onEvent?(.error(id: nil, code: "CORE_MISSING", message: "找不到 distill-core。请重新构建 .app。"))
            return
        }

        let process = Process()
        process.executableURL = executable
        process.arguments = []
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process.environment = environment

        let stdinPipe = Pipe()
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardInput = stdinPipe
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe

        stdoutBuffer.removeAll()
        stderrText.removeAll()

        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            self?.consumeStdout(data)
        }
        stderrPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            if let text = String(data: data, encoding: .utf8) {
                self?.appendStderr(text)
            }
        }
        process.terminationHandler = { [weak self] finished in
            DispatchQueue.main.async {
                guard let self, self.process === finished else { return }
                let wasRunning = self.isRunning
                self.isRunning = false
                self.stdoutPipe?.fileHandleForReading.readabilityHandler = nil
                self.stderrPipe?.fileHandleForReading.readabilityHandler = nil
                if wasRunning {
                    self.onEvent?(.exited(status: finished.terminationStatus, stderr: self.stderrText))
                }
            }
        }

        do {
            try process.run()
        } catch {
            onEvent?(.error(id: nil, code: "CORE_START_FAILED", message: error.localizedDescription))
            return
        }

        self.process = process
        self.stdinPipe = stdinPipe
        self.stdoutPipe = stdoutPipe
        self.stderrPipe = stderrPipe
        self.isRunning = true
    }

    func send(_ object: [String: Any]) {
        guard let stdinPipe, isRunning else { return }
        do {
            var data = try JSONSerialization.data(withJSONObject: object, options: [])
            data.append(0x0A)
            try stdinPipe.fileHandleForWriting.write(contentsOf: data)
        } catch {
            onEvent?(.error(id: nil, code: "CORE_WRITE_FAILED", message: error.localizedDescription))
        }
    }

    func convert(
        id: String,
        image: URL,
        output: URL,
        scene: URL,
        options: RenderOptions,
        ocr: [OCRLine]
    ) {
        var payload: [String: Any] = [
            "v": 1,
            "cmd": "convert",
            "id": id,
            "input": image.path,
            "options": options.wireDictionary(input: image.path, output: output.path, scene: scene.path),
        ]
        if let data = try? JSONEncoder().encode(ocr),
           let lines = try? JSONSerialization.jsonObject(with: data) {
            payload["ocr"] = lines
        }
        send(payload)
    }

    func cancel(id: String) {
        send(["v": 1, "cmd": "cancel", "id": id])
    }

    func shutdown() {
        guard isRunning else { return }
        send(["v": 1, "cmd": "shutdown"])
        let process = self.process
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.4) {
            if process?.isRunning == true {
                process?.terminate()
            }
        }
    }

    func restart() {
        terminate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.start()
        }
    }

    func terminate() {
        stdoutPipe?.fileHandleForReading.readabilityHandler = nil
        stderrPipe?.fileHandleForReading.readabilityHandler = nil
        process?.terminate()
        process = nil
        stdinPipe = nil
        stdoutPipe = nil
        stderrPipe = nil
        isRunning = false
    }

    // MARK: - Parsing

    private func appendStderr(_ text: String) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.stderrText += text
            if self.stderrText.count > 20_000 {
                self.stderrText = String(self.stderrText.suffix(20_000))
            }
        }
    }

    private func consumeStdout(_ data: Data) {
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.stdoutBuffer.append(data)
            while let newline = self.stdoutBuffer.firstIndex(of: 0x0A) {
                let lineData = self.stdoutBuffer[self.stdoutBuffer.startIndex..<newline]
                self.stdoutBuffer.removeSubrange(self.stdoutBuffer.startIndex...newline)
                guard !lineData.isEmpty else { continue }
                self.handleLine(Data(lineData))
            }
        }
    }

    private func handleLine(_ data: Data) {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let event = object["event"] as? String else {
            return
        }
        switch event {
        case "hello":
            // The unsolicited hello is the readiness signal; replies to an
            // explicit hello request carry no meaning for the UI.
            onEvent?(.ready(core: object["core"] as? String ?? "?"))
        case "progress":
            let label = object["label"] as? String ?? ""
            let pct = object["pct"] as? Double ?? 0
            onEvent?(.progress(
                id: object["id"] as? String ?? "",
                stage: object["stage"] as? String ?? "",
                pct: pct,
                label: label
            ))
        case "log":
            onEvent?(.log(id: object["id"] as? String ?? "", message: object["message"] as? String ?? ""))
        case "result":
            guard let raw = object["result"],
                  let resultData = try? JSONSerialization.data(withJSONObject: raw) else { return }
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            if let result = try? decoder.decode(ConvertResult.self, from: resultData) {
                onEvent?(.result(id: object["id"] as? String ?? "", result: result))
            } else {
                onEvent?(.error(id: object["id"] as? String, code: "BAD_RESULT", message: "核心返回了无法解析的结果"))
            }
        case "error":
            let error = object["error"] as? [String: Any]
            let code = error?["code"] as? String ?? "UNKNOWN"
            let message = error?["message"] as? String ?? "未知错误"
            let id = object["id"] as? String
            if code == "CANCELLED" {
                onEvent?(.cancelled(id: id ?? ""))
            } else {
                onEvent?(.error(id: id, code: code, message: message))
            }
        default:
            break
        }
    }
}
