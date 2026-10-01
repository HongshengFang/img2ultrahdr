import AppKit
import SwiftUI
import QuartzCore

@main struct WindowCheck {
    @MainActor static func main() throws {
        let args=CommandLine.arguments
        let output=URL(fileURLWithPath:args[2]);try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
        let base=rows[0]["anchor"] as! [String:Any]
        let app=NSApplication.shared;app.setActivationPolicy(.regular)
        let prefs=AppPreferences.shared
        let oldLanguage=prefs.language,oldAppearance=prefs.appearance
        let model=EditorModel(launchImmediately:false,stateURL:output.appendingPathComponent("window.json"))
        model.ready=true;model.busy=false;model.source=URL(fileURLWithPath:(base["source"] as! [String:Any])["path"] as! String)
        model.recipe=Recipe.decode(base["recipe"])!;model.committed=model.recipe
        model.result=base;model.previewResult=base;model.status="精确预览"
        let window=NSWindow(contentRect:NSRect(x:80,y:80,width:1200,height:800),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
        window.title="Img2UltraHDR · Preview validation"
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
        window.contentView=NSHostingView(rootView:EditorView(model:model).preferredColorScheme(prefs.scheme))
        window.makeKeyAndOrderFront(nil);app.activate()
        var screenshot=0
        func snapshot() {
            guard screenshot<6 else { return }
            prefs.language=screenshot<3 ? "zh-Hans":"en"
            prefs.appearance=["system","light","dark"][screenshot%3]
            if screenshot==3 { window.setContentSize(NSSize(width:900,height:620)) }
            DispatchQueue.main.asyncAfter(deadline:.now()+0.5) {
                if let view=window.contentView,let rep=view.bitmapImageRepForCachingDisplay(in:view.bounds) {
                    view.cacheDisplay(in:view.bounds,to:rep)
                    if let data=rep.representation(using:.png,properties:[:]) { try? data.write(to:output.appendingPathComponent("ui-\(screenshot).png")) }
                }
                screenshot+=1;snapshot()
            }
        }
        var timer:Timer?
        var memory:[[String:Any]]=[]
        var lastMemoryTime = -30.0
        DispatchQueue.main.asyncAfter(deadline:.now()+2) { snapshot() }
        DispatchQueue.main.asyncAfter(deadline:.now()+8) {
            let start=CACurrentMediaTime()
            let duration=args.count>3 ? Double(args[3]) ?? 30:30
            model.readouts.presented=[]
            timer=Timer.scheduledTimer(withTimeInterval:1.0/30,repeats:true) { _ in
                let elapsed=CACurrentMediaTime()-start
                if elapsed>=duration {
                    timer?.invalidate()
                    DispatchQueue.main.asyncAfter(deadline:.now()+0.5) {
                        let frames=model.readouts.presented.filter { $0.1>0 && $0.1 >= $0.0 }
                        let latencies=frames.map { ($0.1-$0.0)*1000 }.sorted()
                        let intervals=zip(frames.dropFirst(),frames).map { ($0.0.1-$0.1.1)*1000 }.filter{$0>0}.sorted()
                        func p95(_ values:[Double])->Double { values.isEmpty ? -1:values[min(values.count-1,Int(Double(values.count-1)*0.95))] }
                        let report:[String:Any]=["frames":frames.count,"duration":duration,"latency_p95_ms":p95(latencies),"frame_interval_p95_ms":p95(intervals),"latencies_ms":latencies,"display_headroom":model.readouts.headroom,"supports_hdr":model.readouts.displaySupportsHDR,"rendered_frames":model.readouts.renderedFrames,"raw_presentations":model.readouts.presented.map { [$0.0,$0.1] },"is_active":app.isActive,"window_visible":window.isVisible,"window_occluded":!window.occlusionState.contains(.visible),"histogram_count":model.readouts.bins[768..<1024].reduce(UInt32(0),+),"pixel_count":(base["width"] as! Int)*(base["height"] as! Int),"error":model.readouts.failure ?? "","gpu_memory_samples":memory,"physical_hdr_review":"requires human observation"]
                        try? JSONSerialization.data(withJSONObject:report,options:.prettyPrinted).write(to:output.appendingPathComponent("window-check.json"))
                        prefs.language=oldLanguage;prefs.appearance=oldAppearance
                        print("Window validation complete");app.terminate(nil)
                    }
                    return
                }
                let wave=sin(elapsed*3),group=Int(elapsed/3)%10
                var recipe=Recipe.decode(base["recipe"])!
                switch group {
                case 0:recipe.exposure_ev=wave*0.5
                case 1:recipe.highlight_ev=wave*0.5
                case 2:recipe.shadow_ev=wave*0.5
                case 3:recipe.saturation=1+wave*0.1
                case 4:recipe.hdr_strength=0.9+wave*0.1
                case 5:recipe.sdr_exposure_ev=wave*0.5
                case 6:recipe.exposure_ev=wave*0.3;recipe.shadow_ev=wave*0.3
                case 7:recipe.exposure_ev=wave*0.5;MainActor.assumeIsolated { model.hdr=Int(elapsed*2)%2==0 }
                case 8:recipe.temperature_k=recipe.temperature_k+Int(wave*1000)
                default:recipe.tint=wave*10
                }
                MainActor.assumeIsolated {
                    model.recipe=recipe;model.status="实时预览"
                    func canvas(_ view:NSView)->PreviewMetalView? {
                        if let canvas=view as? PreviewMetalView { return canvas }
                        for child in view.subviews { if let found=canvas(child) { return found } };return nil
                    }
                    if let renderer=window.contentView.flatMap({canvas($0)?.renderer}) {
                        let size=renderer.imageSize
                        renderer.pointer=SIMD2(Int(size.width*(0.5+wave*0.25)),Int(size.height*0.5));renderer.samplePointer()
                        if elapsed-lastMemoryTime>=30 {
                            lastMemoryTime=elapsed
                            memory.append(["seconds":elapsed,"allocated_mib":Double(renderer.device.currentAllocatedSize)/1048576])
                            try? JSONSerialization.data(withJSONObject:memory,options:.prettyPrinted).write(to:output.appendingPathComponent("gpu-memory-progress.json"))
                        }
                    }
                }
            }
        }
        app.run()
    }
}
