import AppKit
import CoreImage

@main
struct HDRProbe {
    static func main() throws {
        let app=NSApplication.shared;app.setActivationPolicy(.regular)
        guard CommandLine.arguments.count>1 else { exit(2) }
        let start=ProcessInfo.processInfo.systemUptime
        let image=try HDRImageLoader.shared.load(path:CommandLine.arguments[1],hdr:true)
        let coldHDRMilliseconds=(ProcessInfo.processInfo.systemUptime-start)*1000
        var switches:[Double]=[]
        if CommandLine.arguments.count>3 {
            _ = try HDRImageLoader.shared.load(path:CommandLine.arguments[3],hdr:false)
            for i in 0..<100 {
                let begin=ProcessInfo.processInfo.systemUptime
                _ = try HDRImageLoader.shared.load(path:CommandLine.arguments[i%2==0 ? 1:3],hdr:i%2==0)
                switches.append((ProcessInfo.processInfo.systemUptime-begin)*1000)
            }
        }
        let window=NSWindow(contentRect:NSRect(x:100,y:100,width:800,height:600),styleMask:[.titled,.closable],backing:.buffered,defer:false)
        window.title="Img2UltraHDR · HDR display check"
        let view=HDRCanvas(frame:window.contentView!.bounds);view.displayImage=image
        window.contentView=view;window.makeKeyAndOrderFront(nil);app.activate()
        DispatchQueue.main.asyncAfter(deadline:.now()+3) {
            let rendered=view.layer?.contents.map { $0 as! CGImage }
            let enabled:Bool
            if #available(macOS 26.0, *) { enabled=view.layer?.preferredDynamicRange == .high }
            else { enabled=view.layer?.wantsExtendedDynamicRangeContent == true }
            let report:[String:Any]=[
                "cold_hdr_decode_ms":coldHDRMilliseconds,
                "warm_switch_cache_ms_max":switches.max() ?? 0,
                "warm_switch_cache_ms_mean":switches.isEmpty ? 0 : switches.reduce(0,+)/Double(switches.count),
                "path":CommandLine.arguments[1],"display_path":"explicit PQ CGImage in native EDR layer",
                "hdr_content_headroom":image.headroom,"layer_content_headroom":rendered?.contentHeadroom ?? 0,
                "display_headroom":window.screen?.maximumExtendedDynamicRangeColorComponentValue ?? 1,
                "hdr_enabled":enabled,
                "hdr_pixels_reached_display_layer":(rendered?.contentHeadroom ?? 0)>1,
                "is_active":app.isActive,"physical_visual_acceptance":"requires human HDR screen observation"]
            let data=try! JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys])
            print(String(data:data,encoding:.utf8)!)
            if CommandLine.arguments.count>2 { try! data.write(to:URL(fileURLWithPath:CommandLine.arguments[2])) }
            app.terminate(nil)
        }
        app.run()
    }
}
