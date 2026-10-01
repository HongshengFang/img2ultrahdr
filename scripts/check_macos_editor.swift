// A native executable harness: Command Line Tools has SwiftUI but no XCTest.
import AppKit
import SwiftUI

@main struct EditorChecks {
    @MainActor static func main() throws {
        let root=FileManager.default.temporaryDirectory.appendingPathComponent("editor-check-"+UUID().uuidString)
        defer { try? FileManager.default.removeItem(at:root) }
        let output=URL(fileURLWithPath:CommandLine.arguments[1])
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        var checks:[String]=[]
        func model() -> EditorModel {
            let model=EditorModel(launchImmediately:false,stateURL:root.appendingPathComponent(UUID().uuidString+"/window.json"))
            model.ready=true;model.busy=false;model.source=root.appendingPathComponent("中文 photo.CR2")
            return model
        }
        do {
            let m=model()
            m.result=["full":true,"exported":"/tmp/delivered.jpg","ultrahdr":"/tmp/cached-hdr.jpg","sdr":"/tmp/pre-encoding.jpg"]
            precondition(m.imagePath=="/tmp/delivered.jpg")
            m.hdr=false;precondition(m.imagePath=="/tmp/delivered.jpg")
            m.comparisonResult=["sdr":"/tmp/initial.jpg"];m.showInitial=true
            precondition(m.imagePath=="/tmp/initial.jpg")
            checks.append("Export HDR and SDR both decode the delivered file; initial-look statistics keep their own source")
        }
        do {
            let m=model();m.request("preview");let obsolete=m.activeID
            m.beginDrag();m.recipe.exposure_ev=0.4;m.interactiveChanged()
            m.receive(["id":obsolete,"event":"result","result":["key":"obsolete during drag"]])
            precondition(m.result==nil && !m.busy && m.undoStack.isEmpty)
            m.endDrag();precondition(m.busy && m.undoStack.count==1)
            checks.append("Dragging invalidates an in-flight exact result before the next commit")
        }
        do {
            let m=model();let prefs=AppPreferences.shared
            let language=prefs.language,appearance=prefs.appearance
            let recipe=m.recipe,revision=m.revision
            prefs.language="en";precondition(L("曝光")=="Exposure")
            prefs.appearance="dark";precondition(m.recipe==recipe && m.revision==revision && m.undoStack.isEmpty)
            prefs.language=language;prefs.appearance=appearance
            checks.append("Language and appearance are independent of photo recipes and render revisions")
        }
        do {
            let m=model();m.request("preview");let obsolete=m.activeID
            m.recipe.exposure_ev=0.5;m.commit();let latest=m.activeID
            m.receive(["id":obsolete,"event":"result","result":["key":"old"]])
            precondition(m.result == nil && m.busy)
            m.receive(["id":latest,"event":"result","result":["key":"new"]])
            precondition(m.result?["key"] as? String == "new" && !m.busy)
            checks.append("Stale results cannot replace the current image")
        }
        do {
            let m=model();let before=m.recipe;m.beginDrag()
            for value in stride(from:0.1,through:0.6,by:0.1) { m.recipe.exposure_ev=value }
            m.endDrag();let after=m.recipe;precondition(m.undoStack.count==1)
            m.undo();precondition(m.recipe==before);m.redo();precondition(m.recipe==after)
            checks.append("A slider drag is one undo step; redo restores its exact recipe")
        }
        do {
            let m=model();m.compare();precondition(m.comparing)
            m.recipe.shadow_ev=0.5;m.commit();precondition(!m.comparing)
            m.receive(["id":m.activeID,"event":"result","result":["key":"edited"]])
            precondition(!m.showInitial && m.comparisonResult == nil)
            checks.append("New edits supersede a pending initial-effect comparison")
        }
        do {
            let m=model()
            for i in 1...110 { m.recipe.exposure_ev=Double(i)/100;m.commit() }
            precondition(m.undoStack.count==100)
            m.recipe.style="phone-natural";m.commit();precondition(m.recipe.exposure_ev==1.1)
            m.reset();precondition(m.recipe.style=="phone-natural" && m.recipe.exposure_ev==0)
            checks.append("History is bounded at 100; styles keep adjustments; reset keeps the style")
        }
        do {
            let m=model();m.exporting=true;m.request("export");let exportID=m.activeID
            m.recipe.exposure_ev=0.5;m.commit();precondition(m.activeID==exportID && m.undoStack.isEmpty)
            m.cancel();m.receive(["id":exportID,"event":"result","result":["key":"late"]])
            precondition(m.result==nil)
            m.receive(["id":m.activeID,"event":"cancelled"]);precondition(!m.busy && !m.exporting)
            checks.append("Export locks edits; cancellation discards late results")
        }
        do {
            let m=model();let original=m.source
            m.drop([URL(fileURLWithPath:"/a.CR2"),URL(fileURLWithPath:"/b.RAF")])
            precondition(m.source==original && m.error != nil)
            checks.append("Dropping multiple files preserves the current photo and reports the issue")
        }
        do {
            let state=root.appendingPathComponent("restore/window.json")
            let first=EditorModel(launchImmediately:false,stateURL:state)
            first.source=root.appendingPathComponent("moved 中文.RAF")
            first.expectedSHA="saved-content-fingerprint";first.recipe.temperature_k=6200
            first.recipe.exposure_ev=0.7;first.persist()
            let restored=EditorModel(launchImmediately:false,stateURL:state)
            restored.restoreWindow()
            precondition(restored.recipe==first.recipe && restored.expectedSHA==first.expectedSHA)
            precondition(restored.needsRelocation && restored.source==first.source)
            checks.append("Restart restores unsent adjustments and asks to relocate a missing original")
        }
        do {
            let m=model();m.recipe.temperature_k=7100;m.recipe.tint=17
            m.recipe.white_balance="custom";m.commit()
            m.recipe.white_balance="auto";m.commit()
            m.recipe.white_balance="custom";m.commit()
            precondition(m.recipe.temperature_k==7100 && m.recipe.tint==17)
            checks.append("White balance remembers custom values across mode changes")
        }
        do {
            let m=model();m.recipe.exposure_ev=0.4;m.commit();m.engine.onExit?()
            precondition(!m.ready && !m.busy && m.error != nil && m.status=="后台已停止")
            precondition(m.recipe.exposure_ev==0.4)
            checks.append("Backend failure releases the busy state while retaining adjustments")
        }
        do {
            let state=root.appendingPathComponent("partial-input/window.json")
            let m=EditorModel(launchImmediately:false,stateURL:state)
            m.source=root.appendingPathComponent("typing.CR2")
            m.recipe.temperature_k=5;m.recipe.exposure_ev=Double.infinity;m.persist()
            let restored=EditorModel(launchImmediately:false,stateURL:state);restored.restoreWindow()
            precondition(restored.recipe.temperature_k==2000 && restored.recipe.exposure_ev==0)
            checks.append("Closing during a pending numeric edit cannot persist an invalid recipe")
        }
        let report:[String:Any]=["passed":checks.count,"checks":checks,"no_raw_or_python_launched":true]
        try JSONSerialization.data(withJSONObject:report,options:.prettyPrinted).write(to:output.appendingPathComponent("checks.json"))
        _ = NSApplication.shared;NSApp.setActivationPolicy(.accessory)
        let m=model();m.status="预览已更新";m.displayStatus="HDR · 4.9× 显示余量"
        let view=NSHostingView(rootView:EditorView(model:m));view.frame=NSRect(x:0,y:0,width:1200,height:800)
        let window=NSWindow(contentRect:view.frame,styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
        window.contentView=view;window.orderFront(nil)
        RunLoop.main.run(until:Date().addingTimeInterval(0.5));view.layoutSubtreeIfNeeded()
        if let bitmap=view.bitmapImageRepForCachingDisplay(in:view.bounds) {
            view.cacheDisplay(in:view.bounds,to:bitmap)
            try bitmap.representation(using:.png,properties:[:])?.write(to:output.appendingPathComponent("layout.png"))
        }
        window.orderOut(nil)
        print("Passed \(checks.count) native editor checks")
    }
}
