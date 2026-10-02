// Repeatedly replace real packets while hiding, resizing and restoring the window.
import AppKit
import SwiftUI

@main struct WindowStress {
    @MainActor static func main() throws {
        let root=URL(fileURLWithPath:CommandLine.arguments[2])
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        let rows=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[1]))) as! [[String:Any]]
        let app=NSApplication.shared;app.setActivationPolicy(.regular)
        app.finishLaunching()
        let model=EditorModel(launchImmediately:false,stateURL:root.appendingPathComponent("session.json"))
        model.ready=true;model.busy=false;model.source=URL(fileURLWithPath:"fixture.RAF")
        let window=NSWindow(contentRect:NSRect(x:40,y:40,width:950,height:720),styleMask:[.titled,.resizable,.miniaturizable],backing:.buffered,defer:false)
        window.title="Img2UltraHDR · Window stress"
        window.level = .floating;window.collectionBehavior=[.canJoinAllSpaces,.fullScreenAuxiliary]
        if let screen=NSScreen.screens.first(where:{$0.maximumPotentialExtendedDynamicRangeColorComponentValue>1}) ?? NSScreen.main {
            window.setFrameOrigin(NSPoint(x:screen.visibleFrame.midX-475,y:screen.visibleFrame.midY-360))
        }
        window.contentView=NSHostingView(rootView:EditorView(model:model));window.makeKeyAndOrderFront(nil)
        app.activate()
        var checks=[[String:Any]]()
        func pump(_ seconds:Double) { RunLoop.main.run(until:Date().addingTimeInterval(seconds)) }
        for cycle in 0..<40 {
            let frame=rows[(cycle%4)*4]["anchor"] as! [String:Any]
            let recipe=Recipe.decode(frame["recipe"])!
            window.orderOut(nil);model.result=frame;model.previewResult=frame;model.recipe=recipe
            model.recipe.exposure_ev=Double(cycle%7-3)*0.1;model.hdr=cycle%2==0
            model.localEditing=cycle%3==0;model.localBypass=false
            if model.localEditing {
                model.recipe.local_adjustments=(0..<8).map { i in
                    var region=LocalAdjustment();region.id="window-\(i)";region.center_x=Double(i)/7;region.center_y=1-region.center_x
                    region.amount=1;region.direction=i%2==0 ? "brighten":"darken";return region
                }
            }
            pump(0.04)
            window.setContentSize(NSSize(width:cycle%2==0 ? 820:1100,height:cycle%2==0 ? 600:760))
            let before=model.readouts.renderedFrames
            window.makeKeyAndOrderFront(nil);window.orderFrontRegardless()
            var restored=false;let deadline=Date().addingTimeInterval(2)
            while Date()<deadline {
                pump(0.03)
                if model.readouts.frameID.contains(model.recipe.previewKey) { restored=true;break }
            }
            // The frame identifier also contains local bypass and display mode.
            let record:[String:Any]=["cycle":cycle,"visible":window.occlusionState.contains(.visible),
                "rendered_after_show":model.readouts.renderedFrames-before,"latest_recipe":restored,"gpu_failure":model.readouts.failure ?? ""]
            checks.append(record)
            if cycle%5==0 { window.miniaturize(nil);pump(0.04);model.hdr.toggle();window.deminiaturize(nil);pump(0.1) }
            model.localBypass.toggle();model.nativeSize.toggle();model.fit();pump(0.03)
        }
        window.orderOut(nil);model.source=nil;model.close()
        let passed=checks.allSatisfy { $0["latest_recipe"] as? Bool==true && $0["gpu_failure"] as? String=="" }
        let visible=checks.filter { $0["visible"] as? Bool==true }.count
        try JSONSerialization.data(withJSONObject:["passed":passed,"visible_cycles":visible,"visibility_limited":visible != checks.count,"cycles":checks],options:.prettyPrinted)
            .write(to:root.appendingPathComponent("stress.json"))
        print("Window stress: \(checks.count) cycles, passed=\(passed)")
        if !passed { exit(1) }
    }
}
