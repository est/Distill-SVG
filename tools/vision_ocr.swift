// Local OCR helper for distill-svg (macOS only).
//
// Usage: vision_ocr <image> [zh-Hans,en-US]
// Output: JSON array of {text, confidence, x, y, w, h} in image pixels.
//
// This intentionally uses Apple's on-device Vision framework: no network,
// no API key, and good Simplified Chinese support.  The Python side treats
// this as one interchangeable OCR backend.

import Foundation
import Vision
import AppKit

struct Line: Codable {
    let text: String
    let confidence: Double
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write("usage: vision_ocr <image> [languages]\n".data(using: .utf8)!)
    exit(2)
}

let path = CommandLine.arguments[1]
let languages = CommandLine.arguments.count >= 3
    ? CommandLine.arguments[2].split(separator: ",").map(String.init)
    : ["zh-Hans", "en-US"]

let url = URL(fileURLWithPath: path)
guard let image = NSImage(contentsOf: url),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot read image: \(path)\n".data(using: .utf8)!)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = languages
request.usesLanguageCorrection = true
request.minimumTextHeight = 0.003

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do {
    try handler.perform([request])
} catch {
    FileHandle.standardError.write("OCR failed: \(error)\n".data(using: .utf8)!)
    exit(1)
}

let width = Double(cg.width)
let height = Double(cg.height)
var lines: [Line] = []

for observation in (request.results ?? []) {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    // Vision uses a bottom-left origin; SVG uses top-left.
    lines.append(Line(
        text: candidate.string,
        confidence: Double(candidate.confidence),
        x: box.minX * width,
        y: (1.0 - box.maxY) * height,
        w: box.width * width,
        h: box.height * height
    ))
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]
let data = try encoder.encode(lines)
FileHandle.standardOutput.write(data)
