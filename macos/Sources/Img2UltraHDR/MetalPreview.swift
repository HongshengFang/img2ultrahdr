import AppKit
import SwiftUI
import MetalKit
import CoreImage
import QuartzCore

enum AppResources {
    static let bundle: Bundle = {
        // SwiftPM's generated accessor can fall back to an absolute build
        // directory. Installed apps must use their own packaged resources.
        if let url = Bundle.main.resourceURL?.appendingPathComponent("Img2UltraHDR_Img2UltraHDR.bundle"),
           let installed = Bundle(url: url) { return installed }
        #if SWIFT_PACKAGE
        return Bundle.module
        #else
        return Bundle(path:ProcessInfo.processInfo.environment["HDRIMG_RESOURCES"] ?? "") ?? Bundle.main
        #endif
    }()
    static let english:Bundle? = bundle.path(forResource:"en",ofType:"lproj").flatMap(Bundle.init(path:))
}

final class PhotoReadouts: ObservableObject {
    @Published var bins = [UInt32](repeating:0,count:1024)
    @Published var pixel: SIMD3<Float>?
    @Published var headroom: Double = 1
    @Published var displaySupportsHDR = false
    @Published var hdr = true
    @Published var kind = "preview"
    @Published var state = "精确预览"
    @Published var failure: String?
    var frameID = ""
    var histogramFrameID = ""
    var presented: [(Double,Double)] = []
    var renderedFrames = 0
    var photoEpoch = 0
}

struct TextureFrame {
    let texture: MTLTexture
    let scene: MTLTexture
    let packet: PreviewPacket?
    var maskArray:MTLTexture? = nil
    var maskSlots:[String:Int] = [:]
    var maskSizes:[String:SIMD2<UInt32>] = [:]
}

final class MetalPreviewRenderer: NSObject, MTKViewDelegate {
    let device: MTLDevice
    let queue: MTLCommandQueue
    let edit: MTLComputePipelineState
    let localEdit: MTLComputePipelineState
    let emptyMasks:MTLTexture
    let histogramPipeline: MTLComputePipelineState
    let display: MTLRenderPipelineState
    weak var view: PreviewMetalView?
    private var attachedReadouts:PhotoReadouts?
    private var readoutEpoch = 0
    var readouts:PhotoReadouts? {
        get { attachedReadouts?.photoEpoch==readoutEpoch ? attachedReadouts:nil }
        set { attachReadouts(newValue,epoch:newValue?.photoEpoch ?? 0) }
    }
    func attachReadouts(_ value:PhotoReadouts?,epoch:Int) { attachedReadouts=value;readoutEpoch=epoch }
    func deactivate() {
        generation+=1
        loadLock.lock();newestLoad=generation;loadLock.unlock()
        attachedReadouts=nil;view=nil
    }
    private var frame: TextureFrame?
    private var output: MTLTexture?
    private var globalOutput:MTLTexture?
    private var histogramBuffer: MTLBuffer
    private var loadingKey = ""
    private var generation = 0
    private var completedKey = ""
    private var requestedKey = ""
    private var inFlight = false
    private var lastHistogram = 0.0
    private var lastFrameTime = 0.0
    private var lastProbeTime = 0.0
    private var probeScheduled = false
    private var scheduled = false
    private var capturedSurface = false
    private var anchorParameters = [Float](repeating:0,count:40)
    var recipe = Recipe()
    var hdr = true
    var clipping: UInt32 = 0
    var kind = "preview"
    var native = false
    var pointer: SIMD2<Int>?
    var lastInputTime = CACurrentMediaTime()
    var lastPresentedInput = 0.0
    private let loadQueue = DispatchQueue(label:"local.img2ultrahdr.preview-load",qos:.userInitiated)
    private let loadLock = NSLock()
    private var newestLoad = 0
    // Confined to loadQueue; retain only one photo's small HDR/SDR pair.
    private var textureCache: [String:TextureFrame] = [:]
    private var textureCachePhoto = ""
    private let ci = CIContext(options:[.cacheIntermediates:false])

    init(device: MTLDevice) throws {
        self.device=device
        let emptyDescriptor=MTLTextureDescriptor.texture2DDescriptor(pixelFormat:.r32Float,width:1,height:1,mipmapped:false)
        emptyDescriptor.textureType = .type2DArray;emptyDescriptor.arrayLength=8;emptyDescriptor.storageMode = .shared
        guard let empty=device.makeTexture(descriptor:emptyDescriptor) else { throw NSError(domain:"Preview",code:8) }
        emptyMasks=empty
        var zero:Float=0
        for slot in 0..<8 { empty.replace(region:MTLRegionMake2D(0,0,1,1),mipmapLevel:0,slice:slot,withBytes:&zero,bytesPerRow:4,bytesPerImage:4) }
        if ProcessInfo.processInfo.environment["HDRIMG_DISABLE_GPU"] == "1" { throw NSError(domain:"Preview",code:0,userInfo:[NSLocalizedDescriptionKey:"实时加速不可用"]) }
        guard let q=device.makeCommandQueue(),let hist=device.makeBuffer(length:4096,options:.storageModeShared),
              let source=AppResources.bundle.url(forResource:"Preview",withExtension:"metal") else { throw NSError(domain:"Preview",code:1,userInfo:[NSLocalizedDescriptionKey:"实时加速不可用"] ) }
        queue=q;histogramBuffer=hist
        let options=MTLCompileOptions();options.mathMode = .safe
        let library=try device.makeLibrary(source:String(contentsOf:source,encoding:.utf8),options:options)
        edit=try device.makeComputePipelineState(function:library.makeFunction(name:"editPreview")!)
        localEdit=try device.makeComputePipelineState(function:library.makeFunction(name:"localLighting")!)
        histogramPipeline=try device.makeComputePipelineState(function:library.makeFunction(name:"histogram")!)
        let desc=MTLRenderPipelineDescriptor();desc.vertexFunction=library.makeFunction(name:"imageVertex")
        desc.fragmentFunction=library.makeFunction(name:"imageFragment");desc.colorAttachments[0].pixelFormat = .rgba16Float
        display=try device.makeRenderPipelineState(descriptor:desc)
        super.init()
    }
    func texture(width:Int,height:Int,data:Data?) throws -> MTLTexture {
        let desc=MTLTextureDescriptor.texture2DDescriptor(pixelFormat:.rgba16Float,width:width,height:height,mipmapped:false)
        desc.usage=[.shaderRead,.shaderWrite];desc.storageMode = .shared
        guard width>0,height>0,width<=16384,height<=16384,let texture=device.makeTexture(descriptor:desc) else { throw NSError(domain:"Preview",code:2,userInfo:[NSLocalizedDescriptionKey:"无法分配预览内存"]) }
        if let data {
            guard data.count==width*height*8 else { throw NSError(domain:"Preview",code:3,userInfo:[NSLocalizedDescriptionKey:"预览数据不完整"]) }
            data.withUnsafeBytes { texture.replace(region:MTLRegionMake2D(0,0,width,height),mipmapLevel:0,withBytes:$0.baseAddress!,bytesPerRow:width*8) }
        }
        return texture
    }
    func load(_ result:[String:Any], hdr:Bool) {
        // Files are immutable. Do not decode the 1025 analysis samples again
        // for every slider, histogram, or mouse-position update.
        let descriptor=result["preview_packet"] as? [String:Any]
        let candidate=(descriptor?[hdr ? "hdr":"sdr"] ?? result[hdr ? "ultrahdr":"sdr"]) as? String ?? ""
        if !candidate.isEmpty && candidate+(hdr ? "H":"S")==loadingKey { return }
        let packet=PreviewPacket.decode(result["preview_packet"])
        if result["preview_packet"] != nil && packet == nil { readouts?.failure="预览格式不兼容，请重新生成。";return }
        let path=packet.map { hdr ? $0.hdr : $0.sdr } ?? result[hdr ? "ultrahdr":"sdr"] as? String ?? ""
        guard !path.isEmpty else { return }
        let key=path+(hdr ? "H":"S")
        guard key != loadingKey else { return }
        loadingKey=key;generation+=1;let wanted=generation
        loadLock.lock();newestLoad=wanted;loadLock.unlock()
        frame=nil;completedKey=""
        loadQueue.async { [weak self] in
            guard let self else { return }
            self.loadLock.lock();let stillWanted=self.newestLoad==wanted;self.loadLock.unlock()
            guard stillWanted else { return }
            do {
                let loaded:TextureFrame
                if let packet {
                    if self.textureCachePhoto != packet.key { self.textureCache.removeAll();self.textureCachePhoto=packet.key }
                    if let cached=self.textureCache[key] { loaded=cached }
                    else {
                        let basePath=hdr ? (packet.base_hdr ?? path):(packet.base_sdr ?? path)
                        let t=try self.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:basePath),options:.mappedIfSafe))
                        let scene=try self.textureCache.values.first?.scene ?? self.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:packet.scene),options:.mappedIfSafe))
                        var localFrame=TextureFrame(texture:t,scene:scene,packet:packet)
                        if let shared=self.textureCache.values.first {
                            localFrame.maskArray=shared.maskArray;localFrame.maskSlots=shared.maskSlots;localFrame.maskSizes=shared.maskSizes
                        } else if let masks=packet.local_masks,!masks.isEmpty {
                            let entries=masks.sorted {$0.key<$1.key}
                            guard entries.count<=8,let first=entries.first?.value,first.width>0,first.height>0,max(first.width,first.height)<=1536 else { throw NSError(domain:"Preview",code:5) }
                            let width=entries.map { $0.value.width }.max()!,height=entries.map { $0.value.height }.max()!
                            let desc=MTLTextureDescriptor.texture2DDescriptor(pixelFormat:.r32Float,width:width,height:height,mipmapped:false)
                            desc.textureType = .type2DArray;desc.arrayLength=8;desc.storageMode = .shared;desc.usage = .shaderRead
                            guard let array=self.device.makeTexture(descriptor:desc) else { throw NSError(domain:"Preview",code:6) }
                            for (slot,entry) in entries.enumerated() {
                                let m=entry.value,data=try Data(contentsOf:URL(fileURLWithPath:m.path))
                                guard m.width>0,m.height>0,max(m.width,m.height)<=1536,data.count==m.width*m.height*4 else { throw NSError(domain:"Preview",code:7) }
                                if m.width != width || m.height != height {
                                    Data(count:width*height*4).withUnsafeBytes { array.replace(region:MTLRegionMake2D(0,0,width,height),mipmapLevel:0,slice:slot,withBytes:$0.baseAddress!,bytesPerRow:width*4,bytesPerImage:width*height*4) }
                                }
                                data.withUnsafeBytes { array.replace(region:MTLRegionMake2D(0,0,m.width,m.height),mipmapLevel:0,slice:slot,withBytes:$0.baseAddress!,bytesPerRow:m.width*4,bytesPerImage:m.width*m.height*4) }
                                localFrame.maskSlots[entry.key]=slot
                                localFrame.maskSizes[entry.key]=SIMD2(UInt32(m.width),UInt32(m.height))
                            }
                            localFrame.maskArray=array
                        }
                        loaded=localFrame
                        self.textureCache[key]=loaded
                    }
                } else {
                    self.textureCache.removeAll();self.textureCachePhoto=""
                    guard let image=CIImage(contentsOf:URL(fileURLWithPath:path),options:[.expandToHDR:hdr,.applyOrientationProperty:true]) else { throw NSError(domain:"Preview",code:4,userInfo:[NSLocalizedDescriptionKey:"无法解码图像"]) }
                    let w=Int(image.extent.width),h=Int(image.extent.height)
                    var data=Data(count:w*h*8)
                    let space=CGColorSpace(name:hdr ? CGColorSpace.extendedLinearITUR_2020 : CGColorSpace.extendedLinearDisplayP3)!
                    data.withUnsafeMutableBytes { self.ci.render(image,toBitmap:$0.baseAddress!,rowBytes:w*8,bounds:image.extent,format:.RGBAh,colorSpace:space) }
                    let t=try self.texture(width:w,height:h,data:data)
                    loaded=TextureFrame(texture:t,scene:t,packet:nil)
                }
                DispatchQueue.main.async {
                    guard self.generation==wanted else { return }
                    self.frame=loaded
                    self.anchorParameters=loaded.packet.map { PreviewMath.parameters($0,$0.anchor_recipe) } ?? [Float](repeating:0,count:40)
                    do { self.output=try self.texture(width:loaded.texture.width,height:loaded.texture.height,data:nil);self.globalOutput=nil }
                    catch { self.readouts?.failure=error.localizedDescription;return }
                    self.view?.layoutPhoto();self.view?.centerIfNeeded();self.invalidate()
                }
            } catch { DispatchQueue.main.async { if self.generation==wanted { self.readouts?.failure=error.localizedDescription } } }
        }
    }
    var imageSize:NSSize { guard let frame else { return .zero };return NSSize(width:frame.texture.width,height:frame.texture.height) }
    func update(recipe:Recipe,hdr:Bool,clipping:UInt32,kind:String,inputTime:Double? = nil) {
        self.recipe=recipe.normalized(fallback:frame?.packet?.anchor_recipe ?? Recipe());self.hdr=hdr;self.clipping=clipping;self.kind=kind
        let encoder=JSONEncoder();encoder.outputFormatting = [.sortedKeys]
        let data=(try? encoder.encode(self.recipe)).flatMap { String(data:$0,encoding:.utf8) } ?? ""
        let key=loadingKey+data+String(clipping)+kind
        if key != requestedKey { requestedKey=key;lastInputTime=inputTime ?? CACurrentMediaTime();invalidate() }
    }
    func invalidate() {
        guard !scheduled else { return };scheduled=true
        let delay=max(0,1.0/60-(CACurrentMediaTime()-lastFrameTime))
        DispatchQueue.main.asyncAfter(deadline:.now()+delay) { [weak self] in
            guard let self else { return };self.scheduled=false
            // Drive the paused MTKView directly. AppKit may postpone ordinary
            // invalidation while it coalesces control/layout updates, especially
            // during tracking. A 60 Hz ceiling leaves scheduling room for a
            // steady 30 Hz input stream without dropping every second update.
            if !self.inFlight { self.view?.draw() }
        }
    }
    func mtkView(_ view:MTKView,drawableSizeWillChange size:CGSize) { invalidate() }
    func draw(in view:MTKView) {
        guard !inFlight,let frame,let output,let drawable=view.currentDrawable,
              let pass=view.currentRenderPassDescriptor,let command=queue.makeCommandBuffer() else { return }
        inFlight=true;lastFrameTime=CACurrentMediaTime()
        readouts?.renderedFrames += 1
        let id=requestedKey,wanted=generation,inputTime=lastInputTime,selectedHDR=hdr
        let packet=frame.packet
        let compatible=packet.map { $0.anchor_recipe.style==recipe.style && $0.anchor_recipe.white_balance==recipe.white_balance } ?? false
        let globalInteractive=compatible && packet!.anchor_recipe.withoutLocal != recipe.withoutLocal && kind == "preview"
        let interactive=globalInteractive || (compatible && packet!.anchor_recipe.local_adjustments != recipe.local_adjustments && kind=="preview")
        let useLocal=packet?.version==2 && !recipe.local_adjustments.isEmpty && kind=="preview"
        if useLocal && globalOutput==nil {
            do { globalOutput=try texture(width:output.width,height:output.height,data:nil) }
            catch { inFlight=false;readouts?.failure=error.localizedDescription;return }
        }
        var current=packet.map { PreviewMath.parameters($0,recipe.normalized()) } ?? anchorParameters
        var anchor=anchorParameters
        var matrix=packet.map { PreviewMath.whiteBalance(anchor:$0.anchor_recipe,current:recipe) } ?? matrix_identity_float3x3
        var flags=SIMD2<UInt32>(hdr ? 1:0,globalInteractive ? 1:0)
        let changed=id != completedKey
        if changed,let compute=command.makeComputeCommandEncoder() {
            compute.setComputePipelineState(edit);compute.setTexture(frame.scene,index:0);compute.setTexture(frame.texture,index:1);compute.setTexture(useLocal ? globalOutput:output,index:2)
            compute.setBytes(&current,length:current.count*4,index:0);compute.setBytes(&anchor,length:anchor.count*4,index:1)
            compute.setBytes(&matrix,length:MemoryLayout<simd_float3x3>.stride,index:2);compute.setBytes(&flags,length:8,index:3)
            compute.dispatchThreads(MTLSize(width:output.width,height:output.height,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));compute.endEncoding()
        }
        if changed,useLocal,let globalOutput {
            encodeLocal(command,input:globalOutput,output:output,regions:recipe.local_adjustments,maskArray:frame.maskArray,slots:frame.maskSlots,maskSizes:frame.maskSizes,hdr:hdr,strength:recipe.hdr_strength)
        }
        let updateHistogram=(id != readouts?.histogramFrameID) && (CACurrentMediaTime()-lastHistogram >= 0.1 || !interactive || hdr != readouts?.hdr)
        if updateHistogram {
            lastHistogram=CACurrentMediaTime()
            memset(histogramBuffer.contents(),0,4096)
            if let compute=command.makeComputeCommandEncoder() {
                var h:UInt32=hdr ? 1:0
                compute.setComputePipelineState(histogramPipeline);compute.setTexture(output,index:0);compute.setBuffer(histogramBuffer,offset:0,index:0);compute.setBytes(&h,length:4,index:1)
                compute.dispatchThreadgroups(MTLSize(width:(output.width+15)/16,height:(output.height+15)/16,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));compute.endEncoding()
            }
        }
        if let render=command.makeRenderCommandEncoder(descriptor:pass) {
            var scale=self.view?.photoScale ?? SIMD2<Float>(1,1)
            var displayFlags=SIMD2<UInt32>(hdr ? 1:0,clipping)
            render.setRenderPipelineState(display);render.setVertexBytes(&scale,length:8,index:0);render.setFragmentBytes(&displayFlags,length:8,index:0);render.setFragmentTexture(output,index:0)
            render.drawPrimitives(type:.triangle,vertexStart:0,vertexCount:6);render.endEncoding()
        }
        var capture:MTLBuffer?
        let captureRowBytes=(drawable.texture.width*8+255)/256*256
        let captureHeight=drawable.texture.height,captureWidth=drawable.texture.width
        let capturePath=ProcessInfo.processInfo.environment["HDRIMG_CAPTURE_SURFACE"]
        if !capturedSurface, capturePath != nil,
           let buffer=device.makeBuffer(length:captureRowBytes*captureHeight,options:.storageModeShared),
           let blit=command.makeBlitCommandEncoder() {
            capture=buffer;capturedSurface=true
            blit.copy(from:drawable.texture,sourceSlice:0,sourceLevel:0,sourceOrigin:MTLOrigin(x:0,y:0,z:0),sourceSize:MTLSize(width:captureWidth,height:captureHeight,depth:1),to:buffer,destinationOffset:0,destinationBytesPerRow:captureRowBytes,destinationBytesPerImage:captureRowBytes*captureHeight)
            blit.endEncoding()
        }
        let captured=capture
        drawable.addPresentedHandler { [weak self] drawable in
            // CAMetalLayer may recycle the drawable before the main queue
            // handles this callback. Snapshot its timestamp here.
            let presentedTime=drawable.presentedTime
            DispatchQueue.main.async {
                guard let self,self.generation==wanted,self.lastPresentedInput != inputTime else { return }
                self.lastPresentedInput=inputTime
                self.readouts?.presented.append((inputTime,presentedTime))
                if (self.readouts?.presented.count ?? 0)>10000 { self.readouts?.presented.removeFirst(1000) }
            }
        }
        command.addCompletedHandler { [weak self] buffer in
            DispatchQueue.main.async {
                guard let self else { return };self.inFlight=false
                guard self.generation==wanted else { self.invalidate();return }
                if buffer.status == .error { self.readouts?.failure=buffer.error?.localizedDescription ?? "实时加速不可用";return }
                if let captured,let capturePath {
                    let data=Data(bytes:captured.contents(),count:captureRowBytes*captureHeight)
                    let url=URL(fileURLWithPath:capturePath)
                    try? data.write(to:url,options:.atomic)
                    let info:[String:Any]=["width":captureWidth,"height":captureHeight,"row_bytes":captureRowBytes,"hdr":selectedHDR,"format":"rgba16FloatLE","source":"actual Metal drawable before system display adaptation"]
                    try? JSONSerialization.data(withJSONObject:info,options:.prettyPrinted).write(to:url.appendingPathExtension("json"))
                }
                self.completedKey=id
                if updateHistogram {
                    self.readouts?.bins=Array(UnsafeBufferPointer(start:self.histogramBuffer.contents().assumingMemoryBound(to:UInt32.self),count:1024))
                    self.readouts?.histogramFrameID=id
                }
                self.readouts?.frameID=id
                if self.readouts?.hdr != selectedHDR { self.readouts?.hdr=selectedHDR }
                if self.readouts?.kind != self.kind { self.readouts?.kind=self.kind }
                let state=self.kind=="export" ? "导出文件":(self.kind=="full" ? "全尺寸结果":(packet != nil && !compatible ? "等待精确结果":(interactive ? "实时预览":"精确预览")))
                if self.readouts?.state != state { self.readouts?.state=state }
                self.samplePointer()
                if self.requestedKey != id { self.invalidate() }
                else if self.readouts?.histogramFrameID != id { DispatchQueue.main.asyncAfter(deadline:.now()+0.1) { self.invalidate() } }
            }
        }
        command.present(drawable);command.commit()
    }
    func encodeLocal(_ command:MTLCommandBuffer,input:MTLTexture,output:MTLTexture,regions:[LocalAdjustment],maskArray:MTLTexture?,slots:[String:Int],maskSizes:[String:SIMD2<UInt32>]=[:],hdr:Bool,strength:Double) {
        var records=[Float]()
        for r in regions.sorted(by:{$0.id<$1.id}).prefix(8) {
            records += [Float(r.center_x),Float(r.center_y),Float(r.radius_x),Float(r.radius_y),
                        Float(r.rotation * .pi/180),Float(r.mode=="smart" && slots[r.id]==nil ? 0:r.ev),Float(slots[r.id] ?? 0),r.mode=="smart" ? 1:0]
        }
        let count=records.count/8
        records += [Float](repeating:0,count:64-records.count)
        var info=SIMD4<Float>(Float(count),hdr ? Float(1+(1000/203.0-1)*strength):1,0,0)
        var sizes=[SIMD2<UInt32>](repeating:SIMD2(UInt32(maskArray?.width ?? 1),UInt32(maskArray?.height ?? 1)),count:8)
        for (id,size) in maskSizes { if let slot=slots[id],slot<8 { sizes[slot]=size } }
        guard let compute=command.makeComputeCommandEncoder() else { return }
        compute.setComputePipelineState(localEdit);compute.setTexture(input,index:0);compute.setTexture(maskArray ?? emptyMasks,index:1);compute.setTexture(output,index:2)
        compute.setBytes(&records,length:256,index:0);compute.setBytes(&info,length:16,index:1);compute.setBytes(&sizes,length:64,index:2)
        compute.dispatchThreads(MTLSize(width:output.width,height:output.height,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));compute.endEncoding()
    }
    func samplePointer() {
        // Text and histogram refresh at 10 Hz while image presentation keeps
        // its independent 30 Hz budget. Always publish the last pointer too.
        let delay=0.1-(CACurrentMediaTime()-lastProbeTime)
        if pointer != nil && delay>0 {
            if !probeScheduled {
                probeScheduled=true
                DispatchQueue.main.asyncAfter(deadline:.now()+delay) { [weak self] in
                    guard let self else { return };self.probeScheduled=false;self.samplePointer()
                }
            }
            return
        }
        guard !inFlight,let output,let p=pointer,p.x>=0,p.y>=0,p.x<output.width,p.y<output.height else { if pointer==nil && readouts?.pixel != nil { readouts?.pixel=nil };return }
        lastProbeTime=CACurrentMediaTime()
        var bytes=[UInt16](repeating:0,count:4)
        output.getBytes(&bytes,bytesPerRow:8,from:MTLRegionMake2D(p.x,p.y,1,1),mipmapLevel:0)
        let pixel=SIMD3(Float(Float16(bitPattern:bytes[0])),Float(Float16(bitPattern:bytes[1])),Float(Float16(bitPattern:bytes[2])))
        if readouts?.pixel != pixel { readouts?.pixel=pixel }
    }
}

final class PreviewMetalView: MTKView {
    let localOverlay=LocalOverlay()
    var localInteraction=LocalInteraction() { didSet { localOverlay.interaction=localInteraction } }
    var renderer:MetalPreviewRenderer?
    var nativeSize=false
    var photoScale=SIMD2<Float>(1,1)
    var centerOnLoad = false
    private var tracking:NSTrackingArea?
    private var drag:NSPoint?
    private var origin:NSPoint?
    private var displayKey=""
    private var visibilityObserver:NSObjectProtocol?
    deinit { if let visibilityObserver { NotificationCenter.default.removeObserver(visibilityObserver) } }
    override var isFlipped:Bool { true }
    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let visibilityObserver { NotificationCenter.default.removeObserver(visibilityObserver) }
        visibilityObserver=nil
        if let window {
            visibilityObserver=NotificationCenter.default.addObserver(forName:NSWindow.didChangeOcclusionStateNotification,object:window,queue:.main) { [weak self] _ in
                guard let self,self.window?.occlusionState.contains(.visible)==true else { return }
                self.layoutPhoto();self.updateDisplayRange();self.renderer?.invalidate()
            }
        }
    }
    func configure(_ renderer:MetalPreviewRenderer) {
        self.renderer=renderer;renderer.view=self;device=renderer.device;delegate=renderer
        colorPixelFormat = .rgba16Float;framebufferOnly=false;isPaused=true;enableSetNeedsDisplay=true
        clearColor=MTLClearColor(red:0.00854,green:0.00854,blue:0.00854,alpha:1)
        updateDisplayRange()
        localOverlay.panOwner=self;addSubview(localOverlay)
    }
    func updateDisplayRange() {
        guard let renderer,let metal=layer as? CAMetalLayer else { return }
        let headroom=Double(window?.screen?.maximumExtendedDynamicRangeColorComponentValue ?? 1)
        let supports=(window?.screen?.maximumPotentialExtendedDynamicRangeColorComponentValue ?? 1)>1
        let key="\(renderer.hdr)-\(headroom)-\(supports)"
        guard key != displayKey else { return };displayKey=key
        colorspace=CGColorSpace(name:renderer.hdr ? CGColorSpace.extendedLinearITUR_2020:CGColorSpace.extendedLinearDisplayP3)
        metal.wantsExtendedDynamicRangeContent=renderer.hdr
        metal.edrMetadata=renderer.hdr ? CAEDRMetadata.hdr10(minLuminance:0.005,maxLuminance:1000,opticalOutputScale:203):nil
        if #available(macOS 26.0,*) { metal.preferredDynamicRange=renderer.hdr ? .high:.standard }
        renderer.readouts?.headroom=headroom
        renderer.readouts?.displaySupportsHDR=supports
        renderer.invalidate()
    }
    func layoutPhoto() {
        guard let scroll=enclosingScrollView,let renderer else { return }
        let image=renderer.imageSize,available=scroll.contentView.frame.size,backing=window?.backingScaleFactor ?? 2
        let size=nativeSize ? NSSize(width:max(available.width,image.width/backing),height:max(available.height,image.height/backing)):available
        if frame.size != size { setFrameSize(size) }
        guard image.width>0,image.height>0,bounds.width>0,bounds.height>0 else { return }
        let fit=nativeSize ? 1/backing:min(bounds.width/image.width,bounds.height/image.height)
        let scale=SIMD2(Float(image.width*fit/bounds.width),Float(image.height*fit/bounds.height))
        if photoScale != scale { photoScale=scale;renderer.invalidate() }
        localOverlay.frame=bounds
        localOverlay.photoSize=image
        localOverlay.photoRect=NSRect(x:(bounds.width-image.width*fit)/2,y:(bounds.height-image.height*fit)/2,width:image.width*fit,height:image.height*fit)
    }
    func centerIfNeeded() {
        guard centerOnLoad,let scroll=enclosingScrollView else { return }
        let clip=scroll.contentView
        clip.scroll(to:NSPoint(x:max(0,(frame.width-clip.bounds.width)/2),y:max(0,(frame.height-clip.bounds.height)/2)))
        scroll.reflectScrolledClipView(clip);centerOnLoad=false
    }
    override func layout() { super.layout();layoutPhoto() }
    override func viewDidChangeBackingProperties() { super.viewDidChangeBackingProperties();layoutPhoto();updateDisplayRange() }
    override func updateTrackingAreas() {
        if let tracking { removeTrackingArea(tracking) }
        tracking=NSTrackingArea(rect:.zero,options:[.activeInKeyWindow,.inVisibleRect,.mouseMoved,.mouseEnteredAndExited],owner:self,userInfo:nil)
        addTrackingArea(tracking!);super.updateTrackingAreas()
    }
    override func mouseMoved(with event:NSEvent) {
        guard let renderer else { return }
        let p=convert(event.locationInWindow,from:nil),size=renderer.imageSize
        renderer.pointer=Self.samplePosition(p,bounds:bounds.size,scale:photoScale,image:size)
        renderer.samplePointer()
    }
    static func samplePosition(_ point:NSPoint,bounds:NSSize,scale:SIMD2<Float>,image:NSSize)->SIMD2<Int>? {
        let w=bounds.width*CGFloat(scale.x),h=bounds.height*CGFloat(scale.y)
        let x=(point.x-(bounds.width-w)/2)/max(w,1),y=(point.y-(bounds.height-h)/2)/max(h,1)
        return (x>=0 && y>=0 && x<1 && y<1) ? SIMD2(Int(x*image.width),Int(y*image.height)):nil
    }
    override func mouseExited(with event:NSEvent) { renderer?.pointer=nil;renderer?.samplePointer() }
    override func mouseDown(with event:NSEvent) { drag=event.locationInWindow;origin=enclosingScrollView?.contentView.bounds.origin;NSCursor.closedHand.push() }
    override func mouseDragged(with event:NSEvent) {
        guard let drag,let origin,let scroll=enclosingScrollView else { return }
        let p=NSPoint(x:origin.x-event.locationInWindow.x+drag.x,y:origin.y+event.locationInWindow.y-drag.y)
        scroll.contentView.scroll(to:scroll.contentView.constrainBoundsRect(NSRect(origin:p,size:scroll.contentView.bounds.size)).origin);scroll.reflectScrolledClipView(scroll.contentView)
    }
    override func mouseUp(with event:NSEvent) { drag=nil;origin=nil;NSCursor.pop() }
}

struct InteractivePhotoView:NSViewRepresentable {
    let result:[String:Any]
    let recipe:Recipe
    let hdr:Bool
    let nativeSize:Bool
    let viewReset:Int
    let clipping:UInt32
    let readouts:PhotoReadouts
    var inputTime:Double? = nil
    var local:LocalInteraction = LocalInteraction()
    final class Coordinator {
        var renderer:MetalPreviewRenderer?
        var latest:InteractivePhotoView?
        var reset = -1
        var timer:Timer?
        var active = true
        deinit { timer?.invalidate() }
    }
    func makeCoordinator()->Coordinator { Coordinator() }
    func makeNSView(context:Context)->NSScrollView {
        context.coordinator.latest=self
        let epoch=readouts.photoEpoch
        let scroll=NSScrollView();scroll.hasHorizontalScroller=true;scroll.hasVerticalScroller=true
        scroll.drawsBackground=true;scroll.backgroundColor=NSColor(calibratedWhite:0.09,alpha:1)
        scroll.allowsMagnification=true;scroll.minMagnification=0.25;scroll.maxMagnification=4
        let canvas=PreviewMetalView();scroll.documentView=canvas
        // Compile on the loading queue before enabling edits, not on a slider event.
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                guard let device=MTLCreateSystemDefaultDevice() else { throw NSError(domain:"Metal",code:0,userInfo:[NSLocalizedDescriptionKey:"实时加速不可用"]) }
                let renderer=try MetalPreviewRenderer(device:device)
                DispatchQueue.main.async {
                    guard context.coordinator.active,readouts.photoEpoch==epoch else { return }
                    context.coordinator.renderer=renderer;renderer.attachReadouts(readouts,epoch:epoch);canvas.configure(renderer)
                    context.coordinator.latest?.updateNSView(scroll,context:context)
                }
            } catch { DispatchQueue.main.async {
                if context.coordinator.active && readouts.photoEpoch==epoch { readouts.failure=error.localizedDescription }
            } }
        }
        context.coordinator.timer=Timer.scheduledTimer(withTimeInterval:1,repeats:true) { [weak canvas] _ in canvas?.updateDisplayRange() }
        return scroll
    }
    static func dismantleNSView(_ scroll:NSScrollView,coordinator:Coordinator) {
        coordinator.active=false;coordinator.timer?.invalidate();coordinator.renderer?.deactivate()
        (scroll.documentView as? PreviewMetalView)?.renderer=nil
    }
    func updateNSView(_ scroll:NSScrollView,context:Context) {
        context.coordinator.latest=self
        guard let renderer=context.coordinator.renderer,let canvas=scroll.documentView as? PreviewMetalView else { return }
        // Current headroom can remain 1 until an EDR layer is enabled. Gating
        // activation on that value would permanently prevent HDR from starting.
        let selectedHDR=hdr && (scroll.window?.screen?.maximumPotentialExtendedDynamicRangeColorComponentValue ?? 1)>1
        if context.coordinator.reset != viewReset { scroll.magnification=1;context.coordinator.reset=viewReset;canvas.centerOnLoad=true }
        canvas.nativeSize=nativeSize
        canvas.localInteraction=local
        let kind=result["exported"] != nil ? "export":(result["full"] as? Bool == true ? "full":"preview")
        renderer.load(result,hdr:selectedHDR)
        renderer.update(recipe:recipe,hdr:selectedHDR,clipping:clipping,kind:kind,inputTime:inputTime);canvas.layoutPhoto()
        if renderer.hdr != selectedHDR || canvas.colorspace?.name != (selectedHDR ? CGColorSpace.extendedLinearITUR_2020:CGColorSpace.extendedLinearDisplayP3) { canvas.updateDisplayRange() }
    }
}
