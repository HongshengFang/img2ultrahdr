import AppKit
import CoreImage
import QuartzCore

struct DisplayImage {
    let pixels: CGImage
    let headroom: Float
    let hdr: Bool
}

final class HDRImageLoader: @unchecked Sendable {
    static let shared = HDRImageLoader()
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let lock = NSLock()
    private var cache: [(String, DisplayImage)] = []
    static func cacheKey(path:String,hdr:Bool) -> String {
        let modified = (try? FileManager.default.attributesOfItem(atPath:path)[.modificationDate]) as? Date
        return path + (hdr ? "hdr" : "sdr") + String(modified?.timeIntervalSince1970 ?? 0)
    }
    func load(path: String, hdr: Bool) throws -> DisplayImage {
        lock.lock(); defer { lock.unlock() }
        let key = Self.cacheKey(path:path,hdr:hdr)
        if let result = cache.first(where: { $0.0 == key }) { return result.1 }
        let url = URL(fileURLWithPath: path)
        let pixels: CGImage
        var headroom: Float = 1
        if hdr {
            guard let ci = CIImage(contentsOf: url, options: [.expandToHDR: true, .applyOrientationProperty: true]),
                  let cg = context.createCGImage(ci, from: ci.extent, format: .RGB10,
                          colorSpace: CGColorSpace(name: CGColorSpace.itur_2100_PQ)!) else {
                throw NSError(domain:"HDR", code:1, userInfo:[NSLocalizedDescriptionKey:"无法解码 HDR 预览"])
            }
            headroom = max(1, ci.contentHeadroom)
            pixels = CGImageCreateCopyWithContentHeadroom(headroom, cg) ?? cg
        } else {
            guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
                  let cg = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
                throw NSError(domain:"HDR", code:2, userInfo:[NSLocalizedDescriptionKey:"无法解码 SDR 预览"])
            }
            pixels = cg
        }
        let result = DisplayImage(pixels:pixels, headroom:headroom, hdr:hdr)
        cache.append((key,result))
        while cache.count > 2 { cache.removeFirst() }
        return result
    }
    func load(packet:PreviewPacket,hdr:Bool) throws -> DisplayImage {
        lock.lock();defer { lock.unlock() }
        let path=hdr ? packet.hdr:packet.sdr,key=path+(hdr ? "H":"S")
        if let result=cache.first(where:{$0.0==key}) { return result.1 }
        let data=try Data(contentsOf:URL(fileURLWithPath:path),options:.mappedIfSafe)
        guard data.count==packet.width*packet.height*8 else { throw NSError(domain:"Preview",code:3,userInfo:[NSLocalizedDescriptionKey:"预览数据不完整"]) }
        let inputSpace=CGColorSpace(name:hdr ? CGColorSpace.extendedLinearITUR_2020:CGColorSpace.extendedLinearDisplayP3)!
        let ci=CIImage(bitmapData:data,bytesPerRow:packet.width*8,size:CGSize(width:packet.width,height:packet.height),format:.RGBAh,colorSpace:inputSpace)
        guard let pixels=context.createCGImage(ci,from:ci.extent,format:hdr ? .RGB10:.RGBA8,colorSpace:CGColorSpace(name:hdr ? CGColorSpace.itur_2100_PQ:CGColorSpace.displayP3)!) else {
            throw NSError(domain:"Preview",code:4,userInfo:[NSLocalizedDescriptionKey:"无法解码图像"])
        }
        let headroom:Float=hdr ? 1000/203:1
        let result=DisplayImage(pixels:hdr ? (CGImageCreateCopyWithContentHeadroom(headroom,pixels) ?? pixels):pixels,headroom:headroom,hdr:hdr)
        cache.append((key,result));while cache.count>2 { cache.removeFirst() };return result
    }
}

// NSImageView's ordinary raster path resolves these gain-map JPEGs as SDR on
// the tested system. Host the explicitly decoded/tagged CGImage in a native
// EDR layer instead, so no SDR bitmap backing store clips the HDR rendition.
final class HDRCanvas: NSView {
    private var dragOrigin: NSPoint?
    private var scrollOrigin: NSPoint?
    var nativeSize = false { didSet { needsDisplay = true } }
    var displayImage: DisplayImage? { didSet { needsDisplay = true } }
    override var wantsUpdateLayer: Bool { true }
    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.contentsGravity = .resizeAspect
        layer?.backgroundColor = NSColor(calibratedWhite:0.09, alpha:1).cgColor
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is unsupported") }
    override func updateLayer() {
        guard let layer else { return }
        CATransaction.begin(); CATransaction.setDisableActions(true)
        layer.contents = displayImage?.pixels
        layer.contentsGravity = nativeSize ? .center : .resizeAspect
        layer.contentsScale = window?.backingScaleFactor ?? 2
        if #available(macOS 26.0, *) {
            layer.preferredDynamicRange = displayImage?.hdr == true ? .high : .standard
        } else {
            layer.wantsExtendedDynamicRangeContent = displayImage?.hdr == true
        }
        CATransaction.commit()
    }
    override func viewDidChangeBackingProperties() { super.viewDidChangeBackingProperties(); needsDisplay = true }
    override func resetCursorRects() { addCursorRect(bounds, cursor: nativeSize ? .openHand : .arrow) }
    override func mouseDown(with event: NSEvent) {
        dragOrigin = event.locationInWindow
        scrollOrigin = enclosingScrollView?.contentView.bounds.origin
        NSCursor.closedHand.push()
    }
    override func mouseDragged(with event: NSEvent) {
        guard let origin = dragOrigin, let position = scrollOrigin, let clip = enclosingScrollView?.contentView else { return }
        let point = NSPoint(x:position.x - event.locationInWindow.x + origin.x,
                            y:position.y - event.locationInWindow.y + origin.y)
        clip.scroll(to:clip.constrainBoundsRect(NSRect(origin:point,size:clip.bounds.size)).origin)
        enclosingScrollView?.reflectScrolledClipView(clip)
    }
    override func mouseUp(with event: NSEvent) { dragOrigin = nil; scrollOrigin = nil; NSCursor.pop() }
}
