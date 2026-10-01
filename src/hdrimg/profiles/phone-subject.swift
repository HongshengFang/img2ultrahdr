// Local face boxes and person matte via Vision. No identification or network.
// The renderer falls back unchanged when this optional helper is unavailable.
import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

let args = CommandLine.arguments
guard args.count == 3 || (args.count == 4 && args[3] == "--tile-faces-first") else {
    fputs("usage: phone_subject_probe input.jpg output_prefix\n", stderr)
    exit(2)
}
let input = URL(fileURLWithPath: args[1])
let prefix = args[2]
do {
    let faces = VNDetectFaceRectanglesRequest()
    let person = VNGeneratePersonSegmentationRequest()
    person.qualityLevel = .accurate
    person.outputPixelFormat = kCVPixelFormatType_OneComponent8
    let handler = VNImageRequestHandler(url: input, options: [:])
    if args.count == 4 {
        // Tile callers only consume the matte after a >= .65 face detection.
        // Avoid the expensive segmentation when its output would be discarded.
        try handler.perform([faces])
        if (faces.results ?? []).contains(where: { $0.confidence >= 0.65 }) {
            try handler.perform([person])
        }
    } else {
        try handler.perform([faces, person])
    }
    var result: [String: Any] = ["faces": (faces.results ?? []).map { face in
        let b = face.boundingBox
        return ["x": b.minX, "y": 1-b.maxY, "width": b.width,
                "height": b.height, "confidence": Double(face.confidence)]
    }]
    if let matte = person.results?.first {
        let image = CIImage(cvPixelBuffer: matte.pixelBuffer)
        let context = CIContext(options: [.useSoftwareRenderer: false])
        if let cg = context.createCGImage(image, from: image.extent),
           let output = CGImageDestinationCreateWithURL(
               URL(fileURLWithPath: prefix + "_person.png") as CFURL,
               UTType.png.identifier as CFString, 1, nil) {
            CGImageDestinationAddImage(output, cg, nil)
            guard CGImageDestinationFinalize(output) else { exit(3) }
            result["mask_width"] = cg.width
            result["mask_height"] = cg.height
        }
    }
    let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
    try data.write(to: URL(fileURLWithPath: prefix + ".json"))
} catch {
    fputs("\(error)\n", stderr)
    exit(1)
}
