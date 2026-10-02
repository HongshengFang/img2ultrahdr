import AppKit
import SwiftUI

private struct ControlsProbe:View {
    @ObservedObject var model:EditorModel
    var body:some View {
        VStack {
            GlobalControls(model:model,recipe:model.recipe.withoutLocal,tone:true,customReady:true,language:"en").equatable()
            GlobalControls(model:model,recipe:model.recipe.withoutLocal,tone:false,customReady:true,language:"en").equatable()
        }.padding(18).background(Color(nsColor:.windowBackgroundColor))
            .preferredColorScheme(.light).environment(\.locale,Locale(identifier:"en_US"))
    }
}

/// Read the actual AppKit text fields, so a correct model with frozen labels
/// fails this check. Compile with the same EDITOR_MODEL_CHECK sources as the
/// broader editor harness; no RAW processing or Python worker is launched.
@main struct GlobalControlChecks {
    @MainActor static func main() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        let output=URL(fileURLWithPath:CommandLine.arguments[1])
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        let model=EditorModel(launchImmediately:false,stateURL:output.appendingPathComponent("session.json"))
        model.ready=true;model.busy=false;model.source=output.appendingPathComponent("fixture.CR2")
        model.recipe.white_balance="custom";model.committed=model.recipe
        let view=NSHostingView(rootView:ControlsProbe(model:model))
        view.frame=NSRect(x:0,y:0,width:320,height:920)
        let window=NSWindow(contentRect:view.frame,styleMask:[.titled],backing:.buffered,defer:false)
        window.appearance=NSAppearance(named:.aqua);view.appearance=NSAppearance(named:.aqua)
        window.contentView=view;window.makeKeyAndOrderFront(nil)
        defer { window.orderOut(nil) }
        func fields(_ v:NSView)->[NSTextField] {
            (v as? NSTextField).map { [$0] } ?? v.subviews.flatMap(fields)
        }
        var checks:[String]=[]
        func expect(_ values:[Double],_ name:String) {
            RunLoop.main.run(until:Date().addingTimeInterval(0.3))
            let actual=fields(view).map(\.doubleValue)
            if actual != values {
                FileHandle.standardError.write(Data("\(name): displayed \(actual), expected \(values)\n".utf8))
            }
            precondition(actual==values,"\(name): displayed \(actual), expected \(values)")
            checks.append(name)
        }
        let initial:[Double]=[0,0,0,0,0,5600,0,100,100]
        expect(initial,"Initial numeric fields match all nine controls")
        model.beginDrag()
        model.recipe.exposure_ev=0.7;model.recipe.highlight_ev = -0.6;model.recipe.shadow_ev=0.8
        model.recipe.white_ev=0.2;model.recipe.black_ev = -0.3
        model.recipe.temperature_k=7000;model.recipe.tint=15
        model.recipe.saturation=1.12;model.recipe.hdr_strength=0.8
        let adjusted:[Double]=[0.7,-30,40,10,-15,7000,-15,112,80]
        expect(adjusted,"All numeric fields follow parameter changes during a drag")
        model.endDrag()
        expect(adjusted,"Finishing a drag retains displayed values")
        model.undo();expect(initial,"Undo restores numeric fields")
        model.redo();expect(adjusted,"Redo restores numeric fields")
        model.resetAdjustment(\.exposure_ev)
        expect([0,-30,40,10,-15,7000,-15,112,80],"Resetting one adjustment updates only its value")
        model.undo();expect(adjusted,"An individual reset is undoable")
        model.beginDrag();var local=LocalAdjustment();local.id="local-probe"
        model.recipe.local_adjustments=[local]
        expect(adjusted,"Local-only changes preserve the global fields")
        model.endDrag()
        let exposure=fields(view)[0]
        exposure.selectText(nil)
        let editor=exposure.currentEditor()!
        editor.selectAll(nil);editor.insertText("0.25")
        window.makeFirstResponder(nil)
        expect([0.25,-30,40,10,-15,7000,-15,112,80],"Numeric input updates the binding and preserves the other fields")
        precondition(model.recipe.exposure_ev==0.25)
        if let bitmap=view.bitmapImageRepForCachingDisplay(in:view.bounds) {
            view.cacheDisplay(in:view.bounds,to:bitmap)
            try bitmap.representation(using:.png,properties:[:])?.write(to:output.appendingPathComponent("controls.png"))
        }
        try JSONSerialization.data(withJSONObject:["passed":checks.count,"checks":checks],options:.prettyPrinted)
            .write(to:output.appendingPathComponent("checks.json"))
        print("Passed \(checks.count) native numeric-field checks")
    }
}
