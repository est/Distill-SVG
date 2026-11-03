import AppKit
import ImageIO
import Vision

enum VisionOCRError: LocalizedError {
    case unreadable(String)
    case failed(String)

    var errorDescription: String? {
        switch self {
        case let .unreadable(name):
            return "无法读取图片：\(name)"
        case let .failed(message):
            return "文字识别失败：\(message)"
        }
    }
}

/// Vision OCR runs inside the app instead of spawning the old `swiftc` helper.
/// That keeps the bundled core free of Swift toolchain / RapidOCR / ONNX
/// dependencies and shaves roughly 50 MB from the distribution.
enum VisionOCR {
    static func recognize(url: URL, languages: [String]) async throws -> [OCRLine] {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(returning: try recognizeSync(url: url, languages: languages))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private static func recognizeSync(url: URL, languages: [String]) throws -> [OCRLine] {
        guard let image = NSImage(contentsOf: url),
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            throw VisionOCRError.unreadable(url.lastPathComponent)
        }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = languages
        request.usesLanguageCorrection = true
        request.minimumTextHeight = 0.003

        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw VisionOCRError.failed(error.localizedDescription)
        }

        let width = Double(cgImage.width)
        let height = Double(cgImage.height)
        return (request.results ?? []).compactMap { observation -> OCRLine? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let box = observation.boundingBox
            return OCRLine(
                text: candidate.string,
                confidence: Double(candidate.confidence),
                x: box.minX * width,
                y: (1.0 - box.maxY) * height,
                w: box.width * width,
                h: box.height * height
            )
        }
    }

    /// Pixel dimensions as stored in the file (ignores screen points / DPI).
    static func pixelSize(of url: URL) -> CGSize? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? Double,
              let height = properties[kCGImagePropertyPixelHeight] as? Double else {
            return nil
        }
        return CGSize(width: width, height: height)
    }
}
