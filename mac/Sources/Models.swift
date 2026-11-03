import Foundation

enum OutputStyle: String, CaseIterable, Identifiable {
    case flat
    case wireframe

    var id: String { rawValue }
    var title: String { self == .flat ? "扁平色" : "线框" }
    var subtitle: String { self == .flat ? "保留截图配色" : "白底 · 灰线" }
    var symbol: String { self == .flat ? "paintpalette" : "square.dashed" }
}

enum PhotoMode: String, CaseIterable, Identifiable {
    case embed
    case placeholder
    case outline

    var id: String { rawValue }
    var title: String {
        switch self {
        case .embed: return "嵌入"
        case .placeholder: return "占位"
        case .outline: return "轮廓"
        }
    }
    var help: String {
        switch self {
        case .embed: return "把照片/头像裁剪后嵌入 SVG，最接近原图。"
        case .placeholder: return "用中性色块代替照片，适合隐私场景。"
        case .outline: return "只保留空线框，完全不含照片像素。"
        }
    }
}

enum StrokeMode: String, CaseIterable, Identifiable {
    case off
    case auto
    case all

    var id: String { rawValue }
    var title: String {
        switch self {
        case .off: return "关闭"
        case .auto: return "自动"
        case .all: return "全部"
        }
    }
}

enum LanguageChoice: String, CaseIterable, Identifiable {
    case zhEn
    case zh
    case en

    var id: String { rawValue }
    var title: String {
        switch self {
        case .zhEn: return "中 + 英"
        case .zh: return "仅中文"
        case .en: return "仅英文"
        }
    }
    var visionLanguages: [String] {
        switch self {
        case .zhEn: return ["zh-Hans", "en-US"]
        case .zh: return ["zh-Hans"]
        case .en: return ["en-US"]
        }
    }
}

enum PhotoEncoding: String, CaseIterable, Identifiable {
    case png
    case jpeg
    var id: String { rawValue }
    var title: String { self == .png ? "PNG" : "JPEG" }
}

struct RenderOptions: Equatable {
    var style: OutputStyle = .flat
    var photos: PhotoMode = .embed
    var quality: Quality = .full
    var strokes: StrokeMode = .auto
    var palette: Int = 14
    var scale: Int = 2
    var photoFormat: PhotoEncoding = .png
    var photoQuality: Int = 92
    var photoMaxSize: Int = 2000
    var photoGrayscale: Bool = false
    var photoScrub: Bool = false
    var keepPhotoText: Bool = false
    var traceIcons: Bool = false
    var noIcons: Bool = false
    var languages: LanguageChoice = .zhEn

    enum Quality: String, CaseIterable, Identifiable {
        case preview
        case full
        var id: String { rawValue }
        var title: String { self == .preview ? "快速" : "完整" }
        var fast: Bool { self == .preview }
    }

    func wireDictionary(input: String, output: String, scene: String) -> [String: Any] {
        [
            "input": input,
            "output": output,
            "scene": scene,
            "style": style.rawValue,
            "photos": photos.rawValue,
            "strokes": strokes.rawValue,
            "scale": scale,
            "k": palette,
            "photo_format": photoFormat.rawValue,
            "photo_quality": photoQuality,
            "photo_max_size": photoMaxSize,
            "photo_grayscale": photoGrayscale,
            "photo_scrub": photoScrub,
            "keep_photo_text": keepPhotoText,
            "fast": quality.fast,
            "icons": traceIcons,
            "no_icons": noIcons,
            "languages": languages.visionLanguages.joined(separator: ","),
            // OCR lines are supplied by the shell; this is only a fallback.
            "ocr_engine": "vision",
        ]
    }
}

enum PreviewMode: String, CaseIterable, Identifiable {
    case result
    case original
    var id: String { rawValue }
    var title: String { self == .result ? "结果" : "原图" }
}

enum PreviewFit: String, CaseIterable, Identifiable {
    case width
    case window
    var id: String { rawValue }
    var title: String { self == .width ? "适应宽度" : "适应窗口" }
}

enum Formatters {
    static func bytes(_ count: Int) -> String {
        let formatter = ByteCountFormatter()
        formatter.allowedUnits = count < 1024 ? [.useBytes] : [.useKB, .useMB]
        formatter.countStyle = .file
        return formatter.string(fromByteCount: Int64(count))
    }

    static func seconds(_ value: Double) -> String {
        value < 10 ? String(format: "%.1f s", value) : String(format: "%.0f s", value)
    }
}
