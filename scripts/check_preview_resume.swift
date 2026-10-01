import AppKit
import SwiftUI

// Exercise real window hide/show after a parameter changes out of view.
@main struct PreviewResumeCheck {
    @MainActor static func main() throws {
        let args=CommandLine.arguments,output=URL(fileURLWithPath:args[2])
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let cases=try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:args[1]))) as! [[String:Any]]
        let frame=cases[0]["anchor"] as! [String:Any]
        let app=NSApplication.shared;app.setActivationPolicy(.regular)
        let model=EditorModel(launchImmediately:false,stateURL:output.appendingPathComponent("state.json"))
        model.result=frame;model.previewResult=frame;model.recipe=Recipe.decode(frame["recipe"])!
        model.source=URL(fileURLWithPath:"Preview.CR2");model.ready=true;model.busy=false
        let window=NSWindow(contentRect:NSRect(x:60,y:60,width:900,height:620),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
        window.title="Img2UltraHDR · Window restore check";window.level = .floating
        window.collectionBehavior=[.canJoinAllSpaces,.fullScreenAuxiliary]
        window.contentView=NSHostingView(rootView:EditorView(model:model))
        if let screen=NSScreen.screens.first(where:{$0.maximumPotentialExtendedDynamicRangeColorComponentValue>1}) {
            window.setFrameOrigin(NSPoint(x:screen.visibleFrame.midX-450,y:screen.visibleFrame.midY-310))
        }
        window.makeKeyAndOrderFront(nil);app.activate()
        DispatchQueue.main.asyncAfter(deadline:.now()+4) {
            window.orderOut(nil)
            model.recipe.exposure_ev=0.375
            DispatchQueue.main.asyncAfter(deadline:.now()+1) {
                let before=model.readouts.renderedFrames
                window.makeKeyAndOrderFront(nil);window.orderFrontRegardless()
                DispatchQueue.main.asyncAfter(deadline:.now()+2) {
                    let after=model.readouts.renderedFrames
                    let latest=model.readouts.frameID.contains("\"exposure_ev\":0.375")
                    let passed=after>before && latest && model.readouts.failure==nil
                    let visible=window.occlusionState.contains(.visible)
                    let record:[String:Any]=["passed":passed,"status":visible ? (passed ? "passed":"failed"):"blocked_by_window_visibility","before_show":before,"after_show":after,
                        "latest_recipe":latest,"visible":window.occlusionState.contains(.visible),
                        "gpu_error":model.readouts.failure ?? ""]
                    try? JSONSerialization.data(withJSONObject:record,options:.prettyPrinted).write(to:output.appendingPathComponent("restore.json"))
                    print(passed ? "Window restore displayed the latest recipe":"Window restore check failed")
                    app.terminate(nil)
                }
            }
        }
        app.run()
    }
}
