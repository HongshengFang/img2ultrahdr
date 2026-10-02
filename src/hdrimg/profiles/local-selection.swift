// General foreground instances, separate from the frozen person/skin helper.
import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers

func saveGray(_ values: [UInt16], width: Int, height: Int, to url: URL) throws {
    let data = values.withUnsafeBytes { Data($0) }
    let provider = CGDataProvider(data: data as CFData)!
    let image = CGImage(width:width,height:height,bitsPerComponent:16,bitsPerPixel:16,
        bytesPerRow:width*2,space:CGColorSpaceCreateDeviceGray(),
        bitmapInfo:CGBitmapInfo(rawValue:CGImageAlphaInfo.none.rawValue).union(.byteOrder16Little),
        provider:provider,decode:nil,shouldInterpolate:false,intent:.defaultIntent)!
    guard let output = CGImageDestinationCreateWithURL(url as CFURL,UTType.png.identifier as CFString,1,nil) else {
        throw NSError(domain:"Selection",code:1)
    }
    CGImageDestinationAddImage(output,image,nil)
    guard CGImageDestinationFinalize(output) else { throw NSError(domain:"Selection",code:2) }
}

do {
    let args=CommandLine.arguments
    guard args.count==3 else { exit(2) }
    let input=URL(fileURLWithPath:args[1]),root=URL(fileURLWithPath:args[2])
    let request=VNGenerateForegroundInstanceMaskRequest();request.revision=1
    let handler=VNImageRequestHandler(url:input,options:[:]);try handler.perform([request])
    guard let observation=request.results?.first else {
        try saveGray([0],width:1,height:1,to:root.appendingPathComponent("labels.png"))
        try JSONSerialization.data(withJSONObject:["instances":[Int]()]).write(to:root.appendingPathComponent("complete.json"));exit(0)
    }
    let labels=observation.instanceMask
    CVPixelBufferLockBaseAddress(labels,.readOnly)
    let w=CVPixelBufferGetWidth(labels),h=CVPixelBufferGetHeight(labels),stride=CVPixelBufferGetBytesPerRow(labels)
    let bytes=CVPixelBufferGetBaseAddress(labels)!.assumingMemoryBound(to:UInt8.self)
    var values=[UInt16](repeating:0,count:w*h)
    for y in 0..<h { for x in 0..<w { values[y*w+x]=UInt16(bytes[y*stride+x]) } }
    CVPixelBufferUnlockBaseAddress(labels,.readOnly)
    try saveGray(values,width:w,height:h,to:root.appendingPathComponent("labels.png"))
    for instance in observation.allInstances {
        let buffer=try observation.generateScaledMaskForImage(forInstances:IndexSet(integer:instance),from:handler)
        let mw=CVPixelBufferGetWidth(buffer),mh=CVPixelBufferGetHeight(buffer)
        // CIImage interprets some single-channel mattes as alpha (red is zero).
        // Read the actual coverage plane, preserving its top-left row order.
        var mask=[Float](repeating:0,count:mw*mh)
        CVPixelBufferLockBaseAddress(buffer,.readOnly)
        let format=CVPixelBufferGetPixelFormatType(buffer),rowBytes=CVPixelBufferGetBytesPerRow(buffer)
        let base=CVPixelBufferGetBaseAddress(buffer)!
        for y in 0..<mh {
            if format==kCVPixelFormatType_OneComponent32Float {
                let row=base.advanced(by:y*rowBytes).assumingMemoryBound(to:Float.self)
                for x in 0..<mw { mask[y*mw+x]=row[x] }
            } else if format==kCVPixelFormatType_OneComponent8 {
                let row=base.advanced(by:y*rowBytes).assumingMemoryBound(to:UInt8.self)
                for x in 0..<mw { mask[y*mw+x]=Float(row[x])/255 }
            } else { CVPixelBufferUnlockBaseAddress(buffer,.readOnly);throw NSError(domain:"Selection",code:3) }
        }
        CVPixelBufferUnlockBaseAddress(buffer,.readOnly)
        try saveGray(mask.map { UInt16((min(1,max(0,$0))*65535).rounded()) },width:mw,height:mh,
            to:root.appendingPathComponent("\(instance).png"))
    }
    try JSONSerialization.data(withJSONObject:["instances":Array(observation.allInstances)],options:.sortedKeys)
        .write(to:root.appendingPathComponent("complete.json"),options:.atomic)
} catch { fputs("\(error)\n",stderr);exit(1) }
