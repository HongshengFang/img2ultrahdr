// Synthetic GPU checks for the actual shipped shader, without a display server.
import AppKit
import MetalKit
import simd

@main struct PreviewToolChecks {
    @MainActor static func main() throws {
        let args=CommandLine.arguments,output=URL(fileURLWithPath:args[2])
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
        let base=rows[0]["anchor"] as! [String:Any]
        let object=base["preview_packet"] as! [String:Any],packet=PreviewPacket.decode(object)!
        let renderer=try MetalPreviewRenderer(device:MTLCreateSystemDefaultDevice()!)
        var checks:[String]=[]
        let readings=PhotoReadouts();renderer.readouts=readings
        readings.photoEpoch+=1
        renderer.readouts?.pixel=SIMD3(repeating:1)
        precondition(readings.pixel==nil)
        renderer.attachReadouts(readings,epoch:0)
        renderer.readouts?.bins[0]=99
        precondition(readings.bins[0]==0)
        renderer.attachReadouts(readings,epoch:readings.photoEpoch)
        precondition(renderer.readouts === readings)
        renderer.deactivate();precondition(renderer.readouts==nil)
        checks.append("Callbacks and delayed loads from a previous photo cannot overwrite current readouts")
        renderer.update(recipe:packet.anchor_recipe,hdr:true,clipping:0,kind:"preview")
        let sameInputTime=renderer.lastInputTime
        for _ in 0..<100 { renderer.update(recipe:packet.anchor_recipe,hdr:true,clipping:0,kind:"preview") }
        precondition(renderer.lastInputTime==sameInputTime)
        checks.append("Identical view updates retain one frame key and do not restart idle rendering")
        for (key,value) in [("version",2 as Any),("pixel_format","rgba8"),("row_bytes",1),("gpu_version","future"),("peak_nits",4000),("reference_white_nits",100)] {
            var bad=object;bad[key]=value;precondition(PreviewPacket.decode(bad)==nil)
        }
        checks.append("Packet format, stride, algorithm, reference white and peak are validated")
        let size=NSSize(width:1024,height:1536),bounds=NSSize(width:900,height:768),scale=SIMD2<Float>(512/900,1)
        precondition(PreviewMetalView.samplePosition(NSPoint(x:450,y:384),bounds:bounds,scale:scale,image:size)==SIMD2(512,768))
        precondition(PreviewMetalView.samplePosition(NSPoint(x:100,y:400),bounds:bounds,scale:scale,image:size)==nil)
        precondition(PreviewMetalView.samplePosition(NSPoint(x:706,y:768),bounds:bounds,scale:scale,image:size)==nil)
        precondition(PreviewMetalView.samplePosition(NSPoint(x:450,y:0),bounds:bounds,scale:scale,image:size)==SIMD2(512,0))
        checks.append("Portrait letterboxing, physical 100% at 2x Retina, top origin and outside-image sampling")
        let colors:[SIMD3<Float>]=[.zero,SIMD3(repeating:1),SIMD3(repeating:2),SIMD3(repeating:1000/203),SIMD3(1,0,0),SIMD3(0,1,0),SIMD3(0,0,1),SIMD3(repeating:0.18)]
        let raw=colors.flatMap { [Float16($0.x).bitPattern,Float16($0.y).bitPattern,Float16($0.z).bitPattern,Float16(1).bitPattern] }
        let input=try renderer.texture(width:8,height:1,data:raw.withUnsafeBytes { Data($0) })
        let bins=renderer.device.makeBuffer(length:4096,options:.storageModeShared)!
        for hdr in [false,true] {
            memset(bins.contents(),0,4096)
            let command=renderer.queue.makeCommandBuffer()!,encoder=command.makeComputeCommandEncoder()!
            var h:UInt32=hdr ? 1:0
            encoder.setComputePipelineState(renderer.histogramPipeline);encoder.setTexture(input,index:0)
            encoder.setBuffer(bins,offset:0,index:0);encoder.setBytes(&h,length:4,index:1)
            encoder.dispatchThreadgroups(MTLSize(width:1,height:1,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));encoder.endEncoding()
            command.commit();command.waitUntilCompleted();precondition(command.error==nil)
            let values=Array(UnsafeBufferPointer(start:bins.contents().assumingMemoryBound(to:UInt32.self),count:1024))
            for channel in 0..<4 { precondition(values[channel*256..<(channel+1)*256].reduce(0,+)==8) }
            // Exact endpoints, neutral white, and one stop over reference white.
            if hdr { precondition(values[191]>=1 && values[219]>=1 && values[254]+values[255]>=1) }
            else { precondition(values[255]>=4) }
        }
        checks.append("GPU RGB/luminance histograms count every pixel including partial thread groups and HDR EV bins")
        let desc=MTLTextureDescriptor.texture2DDescriptor(pixelFormat:.rgba16Float,width:8,height:1,mipmapped:false)
        desc.usage=[.renderTarget,.shaderRead];desc.storageMode = .shared
        let target=renderer.device.makeTexture(descriptor:desc)!
        for hdr in [false,true] {
            let pass=MTLRenderPassDescriptor();pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction = .clear;pass.colorAttachments[0].storeAction = .store
            let command=renderer.queue.makeCommandBuffer()!,encoder=command.makeRenderCommandEncoder(descriptor:pass)!
            var scale=SIMD2<Float>(1,1),flags=SIMD2<UInt32>(hdr ? 1:0,3)
            encoder.setRenderPipelineState(renderer.display);encoder.setVertexBytes(&scale,length:8,index:0)
            encoder.setFragmentBytes(&flags,length:8,index:0);encoder.setFragmentTexture(input,index:0)
            encoder.drawPrimitives(type:.triangle,vertexStart:0,vertexCount:6);encoder.endEncoding();command.commit();command.waitUntilCompleted()
            var pixels=[UInt16](repeating:0,count:32);target.getBytes(&pixels,bytesPerRow:64,from:MTLRegionMake2D(0,0,8,1),mipmapLevel:0)
            func pixel(_ i:Int)->SIMD3<Float> { SIMD3(Float(Float16(bitPattern:pixels[i*4])),Float(Float16(bitPattern:pixels[i*4+1])),Float(Float16(bitPattern:pixels[i*4+2]))) }
            precondition(pixel(0).z>0.8 && pixel(0).x<0.1)
            precondition(pixel(3).x>0.9 && pixel(3).y<0.1)
            if hdr { precondition(pixel(1)==SIMD3(repeating:1) && pixel(2)==SIMD3(repeating:2)) }
            else { precondition(pixel(1).y<0.1) }
        }
        checks.append("Output boundary overlays mark black and channel ceilings; ordinary HDR above SDR white is not red")
        let scene=try renderer.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:packet.scene)))
        let exact=try renderer.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:packet.hdr)))
        let edited=try renderer.texture(width:packet.width,height:packet.height,data:nil)
        for kelvin in [2000,5600,15000] {
            for direction in [-1.0,1.0] {
                var anchorRecipe=packet.anchor_recipe;anchorRecipe.white_balance="custom";anchorRecipe.temperature_k=5600
                var recipe=anchorRecipe;recipe.temperature_k=kelvin;recipe.tint=100*direction
                recipe.exposure_ev=3*direction;recipe.highlight_ev=2*direction;recipe.shadow_ev=2*direction;recipe.hdr_strength=direction>0 ? 1:0;recipe.saturation=1+0.2*direction
                var p=PreviewMath.parameters(packet,recipe),a=PreviewMath.parameters(packet,anchorRecipe),wb=PreviewMath.whiteBalance(anchor:anchorRecipe,current:recipe),flags=SIMD2<UInt32>(1,1)
                precondition(p.allSatisfy(\.isFinite))
                let command=renderer.queue.makeCommandBuffer()!,encoder=command.makeComputeCommandEncoder()!
                encoder.setComputePipelineState(renderer.edit);encoder.setTexture(scene,index:0);encoder.setTexture(exact,index:1);encoder.setTexture(edited,index:2)
                encoder.setBytes(&p,length:160,index:0);encoder.setBytes(&a,length:160,index:1);encoder.setBytes(&wb,length:MemoryLayout<simd_float3x3>.stride,index:2);encoder.setBytes(&flags,length:8,index:3)
                encoder.dispatchThreads(MTLSize(width:packet.width,height:packet.height,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));encoder.endEncoding();command.commit();command.waitUntilCompleted()
                var result=[UInt16](repeating:0,count:packet.width*packet.height*4)
                edited.getBytes(&result,bytesPerRow:packet.width*8,from:MTLRegionMake2D(0,0,packet.width,packet.height),mipmapLevel:0)
                precondition(result.allSatisfy { let v=Float(Float16(bitPattern:$0));return v.isFinite && v>=0 && v<=4.93 })
            }
        }
        checks.append("Combined extreme EV, shadows, highlights, saturation, HDR and 2000–15000 K / tint remain finite and bounded")
        try JSONSerialization.data(withJSONObject:["passed":checks.count,"checks":checks],options:.prettyPrinted).write(to:output.appendingPathComponent("checks.json"))
        print("Passed \(checks.count) synthetic native/GPU checks")
    }
}
