// A native executable harness: Command Line Tools has SwiftUI but no XCTest.
import AppKit
import SwiftUI

final class OverlayPanProbe:NSView {
    var downs=0,drags=0,ups=0
    override func mouseDown(with event:NSEvent) { downs+=1 }
    override func mouseDragged(with event:NSEvent) { drags+=1 }
    override func mouseUp(with event:NSEvent) { ups+=1 }
}

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
            let resources=URL(fileURLWithPath:"/Applications/Example.app/Contents/Resources")
            precondition(EngineBridge.enginePath("runtime",resources:resources).path==resources.appendingPathComponent("runtime").path)
            precondition(EngineBridge.enginePath("runtime/.venv/bin/python",resources:resources).path==resources.appendingPathComponent("runtime/.venv/bin/python").path)
            precondition(EngineBridge.enginePath("/opt/local/python",resources:resources).path=="/opt/local/python")
            checks.append("Packaged engine paths follow the app's Resources location while absolute legacy paths remain supported")
        }
        do {
            let m=model();m.source=nil;m.request("hello");let expired=m.activeID
            m.startupExpired(expired)
            precondition(!m.busy && !m.ready && m.error != nil && m.status=="图像后台启动超时")
            precondition(m.startupTimeout==nil && m.startupNotice==nil)
            m.receive(["id":expired,"event":"result","protocol":2,"capabilities":[]])
            precondition(!m.ready)
            m.request("hello");let retry=m.activeID
            precondition(m.busy && m.error==nil && m.status=="正在启动本机图像后台…" && m.startupTimeout != nil)
            m.startupExpired(expired);precondition(m.busy && m.activeID==retry)
            m.receive(["id":retry,"event":"result","protocol":2,"capabilities":[]])
            precondition(m.ready && !m.busy && m.startupTimeout==nil)
            m.startupExpired(retry);precondition(m.ready && m.error==nil)
            checks.append("Startup timeout releases the spinner, ignores late results and cannot expire a newer retry or a completed startup")
        }
        do {
            let m=model();m.request("hello");let identity=m.activeID
            m.notice="pending access reminder"
            m.receive(["id":identity,"event":"progress","phase_code":"dependencies"])
            precondition(m.startupProgressReceived && m.notice==nil && m.status=="正在检查本机图像工具…")
            m.startupExpired(identity)
            precondition(m.status=="工具检查超时" && m.error?.contains("60") == true)
            m.request("hello");let cancelled=m.activeID;m.cancel()
            precondition(!m.busy && m.startupTimeout==nil && m.startupNotice==nil)
            m.startupExpired(cancelled);precondition(m.error==nil)
            checks.append("Backend startup and running tool checks have distinct timeout messages; cancellation removes startup timers")
        }
        do {
            let bridge=EngineBridge(),process=Process()
            process.executableURL=URL(fileURLWithPath:"/bin/sh")
            process.arguments=["-c","trap '' TERM; exec /bin/sleep 10"]
            try process.run();bridge.process=process
            RunLoop.main.run(until:Date().addingTimeInterval(0.1))
            bridge.stopStartup()
            RunLoop.main.run(until:Date().addingTimeInterval(1.3))
            precondition(bridge.process==nil && !process.isRunning)
            checks.append("Stopping a backend that ignores SIGTERM escalates to SIGKILL for the original owned process")
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
            let m=model();m.compare();let obsolete=m.activeID
            precondition(m.comparing)
            m.beginDrag();m.recipe.exposure_ev=0.5;m.endDrag()
            precondition(!m.comparing)
            m.receive(["id":obsolete,"event":"result","result":["key":"initial"]])
            m.receive(["id":m.activeID,"event":"result","result":["key":"adjusted"]])
            precondition(m.result?["key"] as? String=="adjusted" && !m.showInitial)
            m.compare();m.open(root.appendingPathComponent("different.RAF"))
            precondition(!m.comparing)
            m.receive(["id":m.activeID,"event":"result","result":["key":"new-photo"]])
            precondition(m.result?["key"] as? String=="new-photo" && !m.showInitial)
            checks.append("Editing or switching photos during Initial look cannot misroute the next result into comparison")
        }
        do {
            let m=model();m.localEditing=true
            m.addLocal(at:NSPoint(x:0.5,y:0.5));precondition(m.selectionTimeout != nil)
            m.open(root.appendingPathComponent("different.RAF"))
            precondition(m.selectionTimeout==nil && m.pendingLocal==nil)
            checks.append("Opening another photo cancels the previous local selection timeout")
        }
        do {
            let m=model();m.localEditing=true
            m.addLocal(at:NSPoint(x:0.5,y:0.5));let old=m.activeID
            m.reset()
            precondition(m.pendingLocal==nil && m.selectionTimeout==nil)
            m.receive(["id":old,"event":"result","result":["mode":"soft"]])
            precondition(m.recipe.local_adjustments.isEmpty)
            m.addLocal(at:NSPoint(x:0.5,y:0.5));m.beginDrag()
            precondition(m.pendingLocal==nil && m.selectionTimeout==nil)
            checks.append("Resetting or editing during selection cannot later reinsert the cancelled region")
        }
        do {
            var previous=0.0
            for i in 0...20000 {
                let y=Double(i)/10000
                let current=PreviewMath.adjusted(y,0,0,-2)
                precondition(current.isFinite && current>=previous && current<=y+1e-12)
                previous=current
            }
            checks.append("Native strong highlight reduction preserves brightness order")
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
        do {
            let m=model();m.result=["width":1200,"height":800];m.localEditing=true
            var r=LocalAdjustment.make(point:NSPoint(x:0.2,y:0.7),size:m.localImageSize)
            precondition(abs(r.radius_x*1200-r.radius_y*800)<1e-9)
            m.finishLocalSelection(r);precondition(m.recipe.local_adjustments.count==1 && m.undoStack.count==1)
            m.localDirection("brighten");precondition(m.recipe.local_adjustments[0].amount==0.5)
            let history=m.undoStack.count;m.localDirection("brighten");precondition(m.undoStack.count==history)
            m.localDirection("darken");precondition(m.recipe.local_adjustments[0].ev == -0.25)
            m.updateLocal { $0.amount=0 };m.localDirection("brighten")
            precondition(m.recipe.local_adjustments[0].amount==0)
            m.updateLocal { $0.amount=0.5 }
            m.beginDrag();m.updateLocal({$0.center_x=0.3},commitNow:false);m.updateLocal({$0.center_x=0.4},commitNow:false);m.endDrag()
            let after=m.recipe;m.undo();precondition(m.recipe.local_adjustments[0].center_x==0.2);m.redo();precondition(m.recipe==after)
            m.localBypass=true;precondition(m.displayRecipe.local_adjustments.isEmpty && m.recipe==after);m.localBypass=false
            m.setLocalShape("ellipse");precondition(abs(m.localAspect(m.selectedLocal!)-1.5)<1e-9)
            m.deleteLocal(r.id);m.undo();precondition(m.recipe.local_adjustments.count==1)
            r.mode="smart";r.mask_ref=String(repeating:"a",count:64)
            let original=m.recipe.local_adjustments[0];m.addLocal(at:NSPoint(x:0.6,y:0.4),replacing:original)
            let obsolete=m.activeID;m.useSoftSelection()
            m.receive(["id":obsolete,"event":"result","result":["mode":"smart","mask_ref":String(repeating:"b",count:64)]])
            precondition(m.recipe.local_adjustments.count==1 && m.recipe.local_adjustments[0].mode=="soft")
            checks.append("Local regions preserve circular geometry, signed strength, one-step drag undo, bypass and stale-selection isolation")
        }
        do {
            let overlay=LocalOverlay(frame:NSRect(x:0,y:0,width:800,height:600))
            overlay.photoRect=NSRect(x:100,y:50,width:600,height:400)
            precondition(overlay.imagePoint(NSPoint(x:400,y:250))==NSPoint(x:0.5,y:0.5))
            precondition(overlay.imagePoint(NSPoint(x:0,y:0))==nil)
            let r=LocalAdjustment.make(point:NSPoint(x:0.1,y:0.9),size:NSSize(width:1200,height:800))
            precondition(overlay.screenPoint(r)==NSPoint(x:160,y:410))
            var bad=Recipe().dictionary;bad["local_adjustments"]=[["id":"one","radius_x":0]]
            precondition(Recipe.decode(bad)==nil)
            checks.append("Local hit testing uses the image rectangle and top-left normalized coordinates; invalid saved geometry is rejected")
        }
        do {
            let state=root.appendingPathComponent("eight-regions/window.json")
            let m=EditorModel(launchImmediately:false,stateURL:state)
            m.ready=true;m.source=root.appendingPathComponent("eight.CR2");m.localEditing=true;m.result=["width":800,"height":1200]
            for i in 0..<8 {
                var r=LocalAdjustment.make(point:NSPoint(x:0.1+Double(i)*0.1,y:0.6),size:m.localImageSize)
                r.id="region-\(i)";m.finishLocalSelection(r)
            }
            m.addLocal(at:NSPoint(x:0.5,y:0.5));precondition(m.pendingLocal==nil && m.recipe.local_adjustments.count==8)
            m.addLocal(at:NSPoint(x:0.4,y:0.4),replacing:m.recipe.local_adjustments[0])
            precondition(m.localInteraction.regions.count==8 && m.pendingLocal != nil)
            m.cancelLocalSelection()
            m.recipe.local_adjustments[0].mode="smart";m.recipe.local_adjustments[0].mask_ref=String(repeating:"a",count:64)
            m.recipe.style="phone-natural";m.recipe.white_balance="camera";m.commit()
            precondition(m.recipe.local_adjustments[0].mask_ref==String(repeating:"a",count:64))
            m.persist()
            let restored=EditorModel(launchImmediately:false,stateURL:state);restored.restoreWindow()
            precondition(restored.recipe.local_adjustments==m.recipe.local_adjustments)
            m.open(root.appendingPathComponent("next.RAF"));precondition(m.pendingLocal==nil && !m.localEditing && m.recipe.local_adjustments.isEmpty)
            checks.append("Eight local regions restore after restart, survive style/WB changes, reject a ninth region and stay isolated between photos")
        }
        do {
            _ = NSApplication.shared
            let overlay=LocalOverlay(frame:NSRect(x:0,y:0,width:800,height:600))
            let window=NSWindow(contentRect:overlay.frame,styleMask:[],backing:.buffered,defer:false);window.contentView=overlay
            overlay.photoRect=NSRect(x:100,y:50,width:600,height:400);overlay.photoSize=NSSize(width:1200,height:800)
            let probe=OverlayPanProbe();overlay.panOwner=probe
            var r=LocalAdjustment.make(point:NSPoint(x:0.5,y:0.5),size:overlay.photoSize)
            var begins=0,ends=0,created:NSPoint?
            overlay.interaction=LocalInteraction(enabled:true,adding:true,regions:[r],selected:r.id,add:{created=$0},begin:{begins+=1},change:{r=$0;overlay.interaction.regions=[r]},end:{ends+=1})
            func mouse(_ type:NSEvent.EventType,_ point:NSPoint)->NSEvent {
                NSEvent.mouseEvent(with:type,location:overlay.convert(point,to:nil),modifierFlags:[],timestamp:0,windowNumber:window.windowNumber,context:nil,eventNumber:0,clickCount:1,pressure:1)!
            }
            overlay.mouseDown(with:mouse(.leftMouseDown,NSPoint(x:220,y:130)))
            precondition(created==NSPoint(x:0.2,y:0.2))
            let center=overlay.screenPoint(r)
            overlay.mouseDown(with:mouse(.leftMouseDown,center));overlay.mouseDragged(with:mouse(.leftMouseDragged,NSPoint(x:center.x+60,y:center.y+40)));overlay.mouseUp(with:mouse(.leftMouseUp,center))
            precondition(abs(r.center_x-0.6)<1e-9 && abs(r.center_y-0.6)<1e-9 && begins==1 && ends==1)
            let handle=overlay.handlePoints(r)[0]
            overlay.mouseDown(with:mouse(.leftMouseDown,handle));overlay.mouseDragged(with:mouse(.leftMouseDragged,NSPoint(x:handle.x+1000,y:handle.y)));overlay.mouseUp(with:mouse(.leftMouseUp,handle))
            precondition(abs(r.radius_x*1200-r.radius_y*800)<1e-9 && r.radius_y<=1 && begins==2 && ends==2)
            r=LocalAdjustment.make(point:NSPoint(x:0.5,y:0.5),size:overlay.photoSize);r.shape="ellipse"
            overlay.interaction.regions=[r];overlay.interaction.selected=r.id
            let rotate=overlay.handlePoints(r)[4],mid=overlay.screenPoint(r)
            overlay.mouseDown(with:mouse(.leftMouseDown,rotate));overlay.mouseDragged(with:mouse(.leftMouseDragged,NSPoint(x:mid.x-80,y:mid.y)));overlay.mouseUp(with:mouse(.leftMouseUp,rotate))
            precondition(abs(r.rotation+90)<1e-9 && begins==3 && ends==3)
            let key=NSEvent.keyEvent(with:.keyDown,location:.zero,modifierFlags:[],timestamp:0,windowNumber:window.windowNumber,context:nil,characters:" ",charactersIgnoringModifiers:" ",isARepeat:false,keyCode:49)!
            overlay.keyDown(with:key);overlay.mouseDown(with:mouse(.leftMouseDown,center));overlay.mouseDragged(with:mouse(.leftMouseDragged,center));overlay.mouseUp(with:mouse(.leftMouseUp,center))
            precondition(probe.downs==1 && probe.drags==1 && probe.ups==1)
            overlay.interaction.enabled=false;precondition(overlay.hitTest(center)==nil)
            window.orderOut(nil)
            checks.append("Canvas mouse events create and move regions, resize a physical circle, commit each drag once and route space-drag to panning")
        }
        do {
            let m=model();var r=LocalAdjustment();r.mode="smart";r.amount=0.5;r.mask_ref=String(repeating:"c",count:64)
            m.recipe.local_adjustments=[r];m.committed=m.recipe;m.request("prepare")
            m.receive(["id":m.activeID,"event":"error","error_code":"local_asset_missing","region_id":r.id,"message":"missing"])
            precondition(m.localEditing && m.invalidLocalIDs.contains(r.id) && m.hasInvalidActiveLocal && m.recipe.local_adjustments[0].enabled && !m.comparing)
            m.receive(["id":m.activeID,"event":"result","result":["key":"recoverable-canvas"]])
            precondition(m.result?["key"] as? String=="recoverable-canvas" && !m.showInitial)
            m.updateLocal { $0.mode="soft" };precondition(!m.hasInvalidActiveLocal)
            checks.append("Missing saved selections mark the affected region, recover an editable canvas and block export without rewriting the recipe")
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
        let prefs=AppPreferences.shared,previousLanguage=prefs.language
        let local=model();local.result=["width":1200,"height":800];local.localEditing=true
        local.recipe.local_adjustments=(0..<8).map { i in
            var r=LocalAdjustment.make(point:NSPoint(x:0.1+Double(i)*0.1,y:0.5),size:local.localImageSize)
            r.id="layout-\(i)";r.shape="ellipse";r.amount=0.5;r.direction_chosen=true;return r
        }
        local.selectedLocalID=local.recipe.local_adjustments[0].id
        for language in ["zh-Hans","en"] {
            prefs.language=language
            let panel=NSHostingView(rootView:LocalControls(model:local).padding(18).background(Color(nsColor:.windowBackgroundColor)).preferredColorScheme(.light).environment(\.locale,prefs.locale))
            panel.frame=NSRect(x:0,y:0,width:320,height:780)
            let localWindow=NSWindow(contentRect:panel.frame,styleMask:[.titled],backing:.buffered,defer:false)
            localWindow.appearance=NSAppearance(named:.aqua);panel.appearance=NSAppearance(named:.aqua)
            localWindow.contentView=panel;localWindow.makeKeyAndOrderFront(nil)
            RunLoop.main.run(until:Date().addingTimeInterval(0.5));panel.layoutSubtreeIfNeeded();panel.displayIfNeeded()
            if let bitmap=panel.bitmapImageRepForCachingDisplay(in:panel.bounds) {
                panel.cacheDisplay(in:panel.bounds,to:bitmap)
                try bitmap.representation(using:.png,properties:[:])?.write(to:output.appendingPathComponent("local-\(language).png"))
            }
            localWindow.orderOut(nil)
        }
        prefs.language=previousLanguage
        print("Passed \(checks.count) native editor checks")
    }
}
