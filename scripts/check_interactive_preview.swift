import AppKit
import MetalKit
import simd

@main struct InteractiveChecks {
    @MainActor static func main() throws {
        let args=CommandLine.arguments
        let outputURL=URL(fileURLWithPath:args[2]);try FileManager.default.createDirectory(at:outputURL,withIntermediateDirectories:true)
        guard let device=MTLCreateSystemDefaultDevice() else { fatalError("Metal device missing") }
        let renderer=try MetalPreviewRenderer(device:device)
        let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
        var report:[[String:Any]]=[]
        for (index,row) in rows.enumerated() {
            let base=row["anchor"] as! [String:Any],target=row["target"] as! [String:Any]
            let packet=PreviewPacket.decode(base["preview_packet"])!,recipe=Recipe.decode(target["recipe"])!
            let scene=try renderer.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:packet.scene)))
            for hdr in [false,true] {
                let exact=try renderer.texture(width:packet.width,height:packet.height,data:Data(contentsOf:URL(fileURLWithPath:hdr ? packet.hdr:packet.sdr)))
                let texture=try renderer.texture(width:packet.width,height:packet.height,data:nil)
                var current=PreviewMath.parameters(packet,recipe),anchor=PreviewMath.parameters(packet,packet.anchor_recipe)
                var wb=PreviewMath.whiteBalance(anchor:packet.anchor_recipe,current:recipe)
                var flags=SIMD2<UInt32>(hdr ? 1:0,recipe==packet.anchor_recipe ? 0:1)
                let command=renderer.queue.makeCommandBuffer()!,compute=command.makeComputeCommandEncoder()!
                compute.setComputePipelineState(renderer.edit);compute.setTexture(scene,index:0);compute.setTexture(exact,index:1);compute.setTexture(texture,index:2)
                compute.setBytes(&current,length:160,index:0);compute.setBytes(&anchor,length:160,index:1)
                compute.setBytes(&wb,length:MemoryLayout<simd_float3x3>.stride,index:2);compute.setBytes(&flags,length:8,index:3)
                compute.dispatchThreads(MTLSize(width:packet.width,height:packet.height,depth:1),threadsPerThreadgroup:MTLSize(width:16,height:16,depth:1));compute.endEncoding()
                command.commit();command.waitUntilCompleted()
                if let error=command.error { throw error }
                var data=Data(count:packet.width*packet.height*8)
                data.withUnsafeMutableBytes { texture.getBytes($0.baseAddress!,bytesPerRow:packet.width*8,from:MTLRegionMake2D(0,0,packet.width,packet.height),mipmapLevel:0) }
                let file=outputURL.appendingPathComponent("\(index)-\(hdr ? "hdr":"sdr").rgba16f")
                try data.write(to:file)
                report.append(["case":index,"hdr":hdr,"path":file.path,"gpu_ms":(command.gpuEndTime-command.gpuStartTime)*1000])
            }
        }
        try JSONSerialization.data(withJSONObject:report,options:.prettyPrinted).write(to:outputURL.appendingPathComponent("gpu.json"))
        print("GPU evaluated \(report.count) frames")
    }
}
