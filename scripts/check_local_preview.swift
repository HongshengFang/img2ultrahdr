import AppKit
import MetalKit

@main struct LocalPreviewChecks {
    @MainActor static func main() throws {
        let args=CommandLine.arguments,output=URL(fileURLWithPath:CommandLine.arguments[2])
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
        let device=MTLCreateSystemDefaultDevice()!,renderer=try MetalPreviewRenderer(device:device)
        var report=[[String:Any]]()
        for (i,row) in rows.enumerated() {
            let anchorRow=row["anchor"] as! [String:Any],targetRow=row["target"] as! [String:Any]
            let packet=PreviewPacket.decode(anchorRow["preview_packet"])!,recipe=Recipe.decode(targetRow["recipe"])!
            let scene=try renderer.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:packet.scene)))
            var array:MTLTexture?,slots=[String:Int](),sizes=[String:SIMD2<UInt32>]()
            for (slot,entry) in (packet.local_masks ?? [:]).sorted(by:{$0.key<$1.key}).enumerated() {
                let m=entry.value
                if array==nil {
                    let masks=Array((packet.local_masks ?? [:]).values)
                    let desc=MTLTextureDescriptor.texture2DDescriptor(pixelFormat:.r32Float,width:masks.map(\.width).max()!,height:masks.map(\.height).max()!,mipmapped:false)
                    desc.textureType = .type2DArray;desc.arrayLength=8;desc.storageMode = .shared;array=device.makeTexture(descriptor:desc)
                }
                let data=try Data(contentsOf:URL(fileURLWithPath:m.path))
                if m.width != array!.width || m.height != array!.height {
                    Data(count:array!.width*array!.height*4).withUnsafeBytes { array!.replace(region:MTLRegionMake2D(0,0,array!.width,array!.height),mipmapLevel:0,slice:slot,withBytes:$0.baseAddress!,bytesPerRow:array!.width*4,bytesPerImage:array!.width*array!.height*4) }
                }
                data.withUnsafeBytes { array!.replace(region:MTLRegionMake2D(0,0,m.width,m.height),mipmapLevel:0,slice:slot,withBytes:$0.baseAddress!,bytesPerRow:m.width*4,bytesPerImage:m.width*m.height*4) }
                slots[entry.key]=slot
                sizes[entry.key]=SIMD2(UInt32(m.width),UInt32(m.height))
            }
            for hdr in [false,true] {
                let path=hdr ? (packet.base_hdr ?? packet.hdr):(packet.base_sdr ?? packet.sdr)
                let base=try renderer.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:path)))
                let intermediate=try renderer.texture(width:packet.width,height:packet.height,data:nil)
                let final=try renderer.texture(width:packet.width,height:packet.height,data:nil)
                let command=renderer.queue.makeCommandBuffer()!,compute=command.makeComputeCommandEncoder()!
                var current=PreviewMath.parameters(packet,recipe),anchor=PreviewMath.parameters(packet,packet.anchor_recipe)
                var wb=PreviewMath.whiteBalance(anchor:packet.anchor_recipe,current:recipe)
                var flags=SIMD2<UInt32>(hdr ? 1:0,recipe.withoutLocal==packet.anchor_recipe.withoutLocal ? 0:1)
                compute.setComputePipelineState(renderer.edit);compute.setTexture(scene,index:0);compute.setTexture(base,index:1);compute.setTexture(intermediate,index:2)
                compute.setBytes(&current,length:160,index:0);compute.setBytes(&anchor,length:160,index:1)
                compute.setBytes(&wb,length:MemoryLayout<simd_float3x3>.stride,index:2);compute.setBytes(&flags,length:8,index:3)
                compute.dispatchThreads(MTLSize(width:packet.width,height:packet.height,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));compute.endEncoding()
                renderer.encodeLocal(command,input:intermediate,output:final,regions:recipe.local_adjustments,maskArray:array,slots:slots,maskSizes:sizes,hdr:hdr,strength:recipe.hdr_strength)
                command.commit();command.waitUntilCompleted();if let error=command.error { throw error }
                var data=Data(count:packet.width*packet.height*8)
                data.withUnsafeMutableBytes { final.getBytes($0.baseAddress!,bytesPerRow:packet.width*8,from:MTLRegionMake2D(0,0,packet.width,packet.height),mipmapLevel:0) }
                let file=output.appendingPathComponent("\(i)-\(hdr ? "hdr":"sdr").rgba16f");try data.write(to:file)
                report.append(["case":i,"hdr":hdr,"path":file.path,"gpu_ms":(command.gpuEndTime-command.gpuStartTime)*1000])
            }
        }
        try JSONSerialization.data(withJSONObject:report,options:.prettyPrinted).write(to:output.appendingPathComponent("gpu.json"))
        print("Checked \(report.count) local GPU frames")
    }
}
