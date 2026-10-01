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
        do {
            let old: [String:Any] = ["schema_version":1,"style":"phone-clear","white_balance":"auto","temperature_k":5600,"tint":0,"exposure_ev":0.4,"highlight_ev":0,"shadow_ev":0,"saturation":1,"hdr_strength":1,"sdr_exposure_ev":0]
            let restored=Recipe.decode(old)!
            precondition(restored.white_ev==0 && restored.black_ev==0 && restored.exposure_ev==0.4)
            var remembered=restored;remembered.temperature_k=8000;remembered.tint=20
            precondition(restored.previewKey==remembered.previewKey && restored.previewKey==restored.previewKey)
            remembered.white_balance="custom";precondition(restored.previewKey != remembered.previewKey)
            checks.append("Old editing records default Whites and Blacks to zero; cache identity ignores inactive custom WB")
        }
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
            let m=model();m.recipe.white_ev=1;m.recipe.black_ev = -0.5;m.recipe.exposure_ev=0.2;m.commit()
            m.resetAdjustment(\.white_ev)
            precondition(m.recipe.white_ev==0 && m.recipe.black_ev == -0.5 && m.recipe.exposure_ev==0.2)
            m.undo();precondition(m.recipe.white_ev==1)
            checks.append("Endpoint adjustments undo correctly; resetting one label preserves all other settings")
        }
        do {
            let m=model();m.recipe.white_balance="camera";m.scheduleWhiteBalance()
            m.recipe.white_balance="custom";m.scheduleWhiteBalance()
            m.recipe.white_balance="camera";m.scheduleWhiteBalance()
            RunLoop.main.run(until:Date().addingTimeInterval(0.35))
            precondition(m.recipe.white_balance=="camera" && m.revision==1 && m.undoStack.count==1)
            checks.append("Rapid WB selection is coalesced into one render for the latest mode")
        }
        do {
            let m=model()
            let folder=root.appendingPathComponent("float-fixture")
            try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:true)
            for name in ["scene","sdr","hdr"] { try Data([0]).write(to:folder.appendingPathComponent(name)) }
            func frame(_ recipe:Recipe)->[String:Any] {
                let packet:[String:Any] = ["version":1,"gpu_version":"scene-tone-2","pixel_format":"rgba16FloatLE","row_bytes":8,
                    "scene_color_space":"linear-rec2020","sdr_color_space":"linear-display-p3","hdr_color_space":"linear-rec2020",
                    "reference_white_nits":203,"peak_nits":1000,"width":1,"height":1,
                    "scene":folder.appendingPathComponent("scene").path,"sdr":folder.appendingPathComponent("sdr").path,"hdr":folder.appendingPathComponent("hdr").path,
                    "anchor_recipe":recipe.dictionary,"tone":[:],"exposure":[:],"luminance_quantiles":[Double](repeating:0,count:1025)]
                return ["recipe":recipe.dictionary,"preview_packet":packet,"full":false]
            }
            m.request("preview");m.receive(["id":m.activeID,"event":"result","result":frame(m.recipe)])
            m.recipe.white_balance="camera";m.commit();m.receive(["id":m.activeID,"event":"result","result":frame(m.recipe)])
            m.recipe.white_balance="auto";m.scheduleWhiteBalance()
            precondition(m.resultPacket?.anchor_recipe.white_balance=="auto")
            m.pending?.cancel()
            for i in 1...12 {
                m.recipe.white_ev=Double(i)/10;m.request("preview")
                m.receive(["id":m.activeID,"event":"result","result":frame(m.recipe)])
            }
            precondition(m.cachedPreviews.count==8 && m.previewCacheOrder.count==8)
            checks.append("A cached WB mode displays synchronously; retained previews are bounded at eight")
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
        do {
            let menu=NSMenu(title:"Edit")
            for (title,action) in [("撤销","undo:"),("重做","redo:"),("Cut","cut:"),("Copy","copy:"),("Paste","paste:"),("Writing Tools","showWritingTools:"),("AutoFill","autoFill:"),("Start Dictation…","startDictation:"),("Emoji & Symbols","orderFrontCharacterPalette:")] {
                menu.addItem(NSMenuItem(title:title,action:NSSelectorFromString(action),keyEquivalent:""))
            }
            EditorMenuPolicy.shared.apply(to:menu)
            precondition(menu.items.map(\.title)==["撤销","重做","Cut","Copy","Paste"])
            precondition(menu.items[3].action==#selector(NSText.copy(_:)) && menu.items[3].target==nil)
            checks.append("Edit menu retains only Undo/Redo and native responder-chain Cut/Copy/Paste")
            let updated:[String:Any]=["passed":checks.count,"checks":checks,"no_raw_or_python_launched":true]
            try JSONSerialization.data(withJSONObject:updated,options:.prettyPrinted).write(to:output.appendingPathComponent("checks.json"))
        }
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
