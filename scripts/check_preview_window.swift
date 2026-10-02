import AppKit
import SwiftUI
import QuartzCore

@main struct WindowCheck {
    @MainActor static func main() throws {
        let args=CommandLine.arguments
        let localMode=args.contains("--local")
        let output=URL(fileURLWithPath:args[2]);try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
        let base=rows[0]["anchor"] as! [String:Any]
        let app=NSApplication.shared;app.setActivationPolicy(.regular)
        // The timer substitutes for mouse tracking. Keep its process activity
        // equivalent to an active edit even if another app becomes key.
        let activity=ProcessInfo.processInfo.beginActivity(options:.userInitiatedAllowingIdleSystemSleep,reason:"Img2UltraHDR active edit measurement")
        defer { ProcessInfo.processInfo.endActivity(activity) }
        let prefs=AppPreferences.shared
        let oldLanguage=prefs.language,oldAppearance=prefs.appearance
        let model=EditorModel(launchImmediately:false,stateURL:output.appendingPathComponent("window.json"))
        model.ready=true;model.busy=false;model.source=URL(fileURLWithPath:(base["source"] as! [String:Any])["path"] as! String)
        model.recipe=Recipe.decode(base["recipe"])!;model.committed=model.recipe
        if localMode {
            let size=NSSize(width:base["width"] as! Int,height:base["height"] as! Int)
            model.recipe.local_adjustments=(0..<8).map { i in
                var r=LocalAdjustment.make(point:NSPoint(x:0.18+Double(i)*0.08,y:0.35+Double(i)*0.04),size:size)
                r.id="benchmark-\(i)";r.amount=0.5;r.shape="ellipse";r.rotation=Double(i)*12
                if i<2,let ref=base["benchmark_mask_ref"] as? String { r.mode="smart";r.mask_ref=ref }
                r.direction=i%2==0 ? "brighten":"darken";return r
            }
            model.localEditing=true;model.selectedLocalID=model.recipe.local_adjustments[0].id
        }
        let localBaseline=model.recipe
        model.result=base;model.previewResult=base;model.status="精确预览"
        let window=NSWindow(contentRect:NSRect(x:80,y:80,width:1200,height:800),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
        window.title="Img2UltraHDR · Preview validation"
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
        window.contentView=NSHostingView(rootView:EditorView(model:model).preferredColorScheme(prefs.scheme))
        window.center()
        window.makeKeyAndOrderFront(nil);app.activate(ignoringOtherApps:true)
        let ownWindows=(CGWindowListCopyWindowInfo([.optionOnScreenOnly,.excludeDesktopElements],kCGNullWindowID) as? [[String:Any]] ?? []).filter { ($0[kCGWindowOwnerPID as String] as? Int)==Int(ProcessInfo.processInfo.processIdentifier) }
        try JSONSerialization.data(withJSONObject:["window_frame":NSStringFromRect(window.frame),"screens":NSScreen.screens.map { NSStringFromRect($0.frame) },"own_windows":ownWindows],options:.prettyPrinted).write(to:output.appendingPathComponent("visibility.json"))
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
        var visibility:[[String:Any]]=[]
        let visibilityStart=CACurrentMediaTime()
        func recordVisibility() {
            visibility.append(["seconds":CACurrentMediaTime()-visibilityStart,"visible":window.occlusionState.contains(.visible),"active":app.isActive])
        }
        recordVisibility()
        let visibilityObserver=NotificationCenter.default.addObserver(forName:NSWindow.didChangeOcclusionStateNotification,object:window,queue:.main) { _ in recordVisibility() }
        defer { NotificationCenter.default.removeObserver(visibilityObserver) }
        var lastMemoryTime = -30.0
        var inputUpdates=0
        if args.contains("--profile") {
            DispatchQueue.global(qos:.utility).asyncAfter(deadline:.now()+50) {
                let process=Process();process.executableURL=URL(fileURLWithPath:"/usr/bin/sample")
                process.arguments=[String(ProcessInfo.processInfo.processIdentifier),"2","1","-file",output.appendingPathComponent("sample.txt").path]
                process.standardOutput=FileHandle.nullDevice;process.standardError=FileHandle.nullDevice
                try? process.run();process.waitUntilExit()
            }
        }
        DispatchQueue.main.asyncAfter(deadline:.now()+2) { snapshot() }
        DispatchQueue.main.asyncAfter(deadline:.now()+8) {
            window.makeKeyAndOrderFront(nil);app.activate(ignoringOtherApps:true)
            model.beginDrag()
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
                        let report:[String:Any]=["frames":frames.count,"duration":duration,"fps":Double(frames.count)/duration,"local_regions":localMode ? 8:0,"local_drag":"one selected region with eight enabled","latency_p95_ms":p95(latencies),"frame_interval_p95_ms":p95(intervals),"latencies_ms":latencies,"display_headroom":model.readouts.headroom,"supports_hdr":model.readouts.displaySupportsHDR,"rendered_frames":model.readouts.renderedFrames,"raw_presentations":model.readouts.presented.map { [$0.0,$0.1] },"is_active":app.isActive,"window_visible":window.isVisible,"window_occluded":!window.occlusionState.contains(.visible),"visibility_history":visibility,"histogram_count":model.readouts.bins[768..<1024].reduce(UInt32(0),+),"pixel_count":(base["width"] as! Int)*(base["height"] as! Int),"error":model.readouts.failure ?? "","gpu_memory_samples":memory,"physical_hdr_review":"requires human observation"]
                        try? JSONSerialization.data(withJSONObject:report,options:.prettyPrinted).write(to:output.appendingPathComponent("window-check.json"))
                        prefs.language=oldLanguage;prefs.appearance=oldAppearance
                        print("Window validation complete");app.terminate(nil)
                    }
                    return
                }
                let wave=sin(elapsed*3),group=Int(elapsed/3)%10
                inputUpdates+=1
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
                if localMode {
                    recipe=localBaseline
                    let i=group%8
                    recipe.local_adjustments[i].amount=0.5+wave*0.3
                    if group>=5 && recipe.local_adjustments[i].mode=="soft" { recipe.local_adjustments[i].rotation += wave*20;recipe.local_adjustments[i].center_x += wave*0.04 }
                }
                MainActor.assumeIsolated {
                    if localMode {
                        let selected=recipe.local_adjustments[group%8].id
                        if model.selectedLocalID != selected { model.selectedLocalID=selected }
                    }
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
                            var usage=rusage();getrusage(RUSAGE_SELF,&usage)
                            memory.append(["seconds":elapsed,"allocated_mib":Double(renderer.device.currentAllocatedSize)/1048576,"process_peak_mib":Double(usage.ru_maxrss)/1048576])
                            try? JSONSerialization.data(withJSONObject:["updates":inputUpdates,"rendered":model.readouts.renderedFrames,"seconds":elapsed],options:.prettyPrinted).write(to:output.appendingPathComponent("input-progress.json"))
                            try? JSONSerialization.data(withJSONObject:memory,options:.prettyPrinted).write(to:output.appendingPathComponent("gpu-memory-progress.json"))
                        }
                    }
                }
            }
        }
        app.run()
    }
}
