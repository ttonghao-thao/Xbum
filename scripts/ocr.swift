// Uses only the operating system Vision framework. No network or package dependency.
import Foundation
import Vision
import ImageIO

if CommandLine.arguments.count != 2 { exit(2) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["zh-Hans", "en-US"]
request.usesLanguageCorrection = true
let handler = VNImageRequestHandler(url: url, options: [:])
do {
    try handler.perform([request])
    let regions: [[String: Any]] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return ["text": candidate.string, "confidence": candidate.confidence,
                "bbox": [b.minX, 1.0 - b.maxY, b.width, b.height]]
    }
    let output = try JSONSerialization.data(withJSONObject: ["regions": regions])
    FileHandle.standardOutput.write(output)
} catch { exit(1) }
