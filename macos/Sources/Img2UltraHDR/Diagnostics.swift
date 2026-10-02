import AppKit
import QuartzCore

// Opt-in validation of the shipped .app through Launch Services. Regular
// launches never enter this path or change a photo's saved edit history.
extension EditorModel {
    var previewBenchmarkPath:String? {
        let args=ProcessInfo.processInfo.arguments
        return args.firstIndex(of:"--preview-benchmark").flatMap { args.count>$0+1 ? args[$0+1]:nil }
    }
    func loadPreviewBenchmark()->Bool {
        guard let path=previewBenchmarkPath else { return false }
        do {
            let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:path))) as? [[String:Any]]
            guard let frame=rows?.first?["anchor"] as? [String:Any],PreviewPacket.decode(frame["preview_packet"]) != nil,
                  let recipe=Recipe.decode(frame["recipe"]) else { throw NSError(domain:"PreviewCheck",code:1) }
            self.recipe=recipe;committed=recipe;result=frame;previewResult=frame
            source=URL(fileURLWithPath:(frame["source"] as? [String:Any])?["path"] as? String ?? "Preview.CR2")
            if ProcessInfo.processInfo.arguments.contains("--benchmark-local") {
                let size=NSSize(width:frame["width"] as? Int ?? 1536,height:frame["height"] as? Int ?? 1024)
                self.recipe.local_adjustments=(0..<8).map { i in
                    var r=LocalAdjustment.make(point:NSPoint(x:0.18+Double(i)*0.08,y:0.35+Double(i)*0.04),size:size)
                    r.id="benchmark-\(i)";r.amount=0.5;r.direction_chosen=true;r.shape="ellipse";r.rotation=Double(i)*12
                    if i<2,let ref=frame["benchmark_mask_ref"] as? String { r.mode="smart";r.mask_ref=ref }
                    r.direction=i%2==0 ? "brighten":"darken";return r
                }
                committed=self.recipe;localEditing=true;selectedLocalID=self.recipe.local_adjustments[0].id
            }
            ready=true;busy=false;status="精确预览";diagnostic(event:"benchmark-fixture-ready")
        } catch { self.error=error.localizedDescription;busy=false;diagnostic(event:"benchmark-fixture-error") }
        return true
    }
    func startPreviewBenchmark() {
        guard previewBenchmarkPath != nil,!benchmarkStarted,let output=diagnosticRoot,let frame=result else { return }
        benchmarkStarted=true
        let anchor=recipe,args=ProcessInfo.processInfo.arguments
        let localMode=args.contains("--benchmark-local")
        let duration=args.firstIndex(of:"--benchmark-seconds").flatMap { args.count>$0+1 ? Double(args[$0+1]):nil } ?? 35
        try? FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        DispatchQueue.main.asyncAfter(deadline:.now()+1) {
            if let window=NSApp.windows.first(where:{$0.title==AppVersion.windowTitle}),
               let screen=NSScreen.screens.first(where:{$0.maximumPotentialExtendedDynamicRangeColorComponentValue>1}) ?? window.screen ?? NSScreen.main {
                let area=screen.visibleFrame
                window.level = .floating
                window.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
                window.setFrameOrigin(NSPoint(x:area.midX-window.frame.width/2,y:area.midY-window.frame.height/2))
                window.makeKeyAndOrderFront(nil);window.orderFrontRegardless();NSApp.activate()
            }
        }
        DispatchQueue.main.asyncAfter(deadline:.now()+4) {
            let started=CACurrentMediaTime();self.readouts.presented=[]
            var timer:Timer?
            var memory:[[String:Any]]=[]
            var visibility:[[String:Any]]=[]
            let window=NSApp.windows.first(where:{$0.title==AppVersion.windowTitle})
            func recordVisibility() { visibility.append(["seconds":CACurrentMediaTime()-started,"visible":window?.occlusionState.contains(.visible) ?? false]) }
            recordVisibility()
            let observer=NotificationCenter.default.addObserver(forName:NSWindow.didChangeOcclusionStateNotification,object:window,queue:.main) { _ in recordVisibility() }
            self.beginDrag()
            var lastMemoryTime = -30.0
            timer=Timer.scheduledTimer(withTimeInterval:1.0/30,repeats:true) { _ in
                MainActor.assumeIsolated {
                    let elapsed=CACurrentMediaTime()-started
                    if elapsed>=duration {
                        timer?.invalidate()
                        DispatchQueue.main.asyncAfter(deadline:.now()+0.5) {
                            let frames=self.readouts.presented.filter{$0.1>0 && $0.1 >= $0.0}
                            let latency=frames.map{($0.1-$0.0)*1000}.sorted()
                            let gaps=zip(frames.dropFirst(),frames).map{($0.0.1-$0.1.1)*1000}.filter{$0>0}.sorted()
                            func percentile(_ a:[Double])->Double { a.isEmpty ? -1:a[min(a.count-1,Int(Double(a.count-1)*0.95))] }
                            let window=NSApp.windows.first(where:{$0.contentView != nil && $0.title==AppVersion.windowTitle})
                            NotificationCenter.default.removeObserver(observer)
                            let report:[String:Any]=["frames":frames.count,"duration":duration,"fps":Double(frames.count)/duration,"local_regions":localMode ? 8:anchor.local_adjustments.count,"retained_sample_window_seconds":(frames.last?.1 ?? 0)-(frames.first?.1 ?? 0),"presentation_buffer_limit":10000,"latency_p95_ms":percentile(latency),"frame_interval_p95_ms":percentile(gaps),"latencies_ms":latency,"frame_intervals_ms":gaps,"raw_presentations":self.readouts.presented.map { [$0.0,$0.1] },"window_occluded":!(window?.occlusionState.contains(.visible) ?? false),"visibility_history":visibility,"screen":window?.screen?.localizedName ?? "unknown","headroom":self.readouts.headroom,"supports_hdr":self.readouts.displaySupportsHDR,"rendered_frames":self.readouts.renderedFrames,"gpu_failure":self.readouts.failure ?? "","histogram_count":self.readouts.bins[768..<1024].reduce(UInt32(0),+),"pixel_count":(frame["width"] as? Int ?? 0)*(frame["height"] as? Int ?? 0),"method":"30 Hz native parameter updates with histogram and pixel probe; MTLDrawable.presentedTime; no hardware mouse timing"]
                            try? JSONSerialization.data(withJSONObject:report,options:.prettyPrinted).write(to:output.appendingPathComponent("presentation.json"))
                            self.diagnostic(event:"preview-benchmark-complete"); self.captureWindow()
                            self.source=nil // Diagnostic fixtures never become the user's last photo.
                            NSApp.terminate(nil)
                        }
                        return
                    }
                    let wave=sin(elapsed*3),group=Int(elapsed/3)%12
                    var current=anchor
                    switch group {
                    case 0:current.exposure_ev=wave*0.5
                    case 1:current.highlight_ev=wave*0.5
                    case 2:current.shadow_ev=wave*0.5
                    case 3:current.saturation=1+wave*0.1
                    case 4:current.hdr_strength=0.9+wave*0.1
                    case 5:current.sdr_exposure_ev=wave*0.5
                    case 6:current.exposure_ev=wave*0.3;current.shadow_ev=wave*0.3
                    case 7:current.exposure_ev=wave*0.5;self.hdr=Int(elapsed*2)%2==0
                    case 8:current.temperature_k+=Int(wave*1000)
                    case 9:current.tint=wave*10
                    case 10:current.white_ev=wave*0.5
                    default:current.black_ev=wave*0.5
                    }
                    if localMode {
                        current=anchor
                        let i=group%8
                        current.local_adjustments[i].amount=0.5+wave*0.3
                        if group>=5 && current.local_adjustments[i].mode=="soft" { current.local_adjustments[i].rotation += wave*20;current.local_adjustments[i].center_x += wave*0.04 }
                        let selected=current.local_adjustments[i].id
                        if self.selectedLocalID != selected { self.selectedLocalID=selected }
                    }
                    self.recipe=current;self.status="实时预览"
                    func canvas(_ view:NSView)->PreviewMetalView? {
                        if let canvas=view as? PreviewMetalView { return canvas }
                        for child in view.subviews { if let found=canvas(child) { return found } };return nil
                    }
                    if let renderer=NSApp.windows.compactMap({$0.contentView.flatMap{canvas($0)?.renderer}}).first {
                        let size=renderer.imageSize
                        renderer.pointer=SIMD2(Int(size.width*(0.5+wave*0.25)),Int(size.height*0.5));renderer.samplePointer()
                        if elapsed-lastMemoryTime>=30 {
                            lastMemoryTime=elapsed
                            var usage=rusage();getrusage(RUSAGE_SELF,&usage)
                            memory.append(["seconds":elapsed,"allocated_mib":Double(renderer.device.currentAllocatedSize)/1048576,
                                           "process_peak_mib":Double(usage.ru_maxrss)/1048576,"rendered_frames":self.readouts.renderedFrames])
                            try? JSONSerialization.data(withJSONObject:memory,options:.prettyPrinted).write(to:output.appendingPathComponent("gpu-memory-progress.json"),options:.atomic)
                        }
                    }
                }
            }
        }
    }
}
