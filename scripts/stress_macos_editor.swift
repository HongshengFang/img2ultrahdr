// Deterministic adversarial actions against the production model and real fields.
// swiftc -O -D EDITOR_MODEL_CHECK macos/Sources/Img2UltraHDR/*.swift scripts/stress_macos_editor.swift -o /tmp/editor-stress
import AppKit
import SwiftUI

struct StressControls:View {
    @ObservedObject var model:EditorModel
    var body:some View {
        VStack {
            GlobalControls(model:model,recipe:model.recipe.withoutLocal,tone:true,customReady:true,language:"en").equatable()
            GlobalControls(model:model,recipe:model.recipe.withoutLocal,tone:false,customReady:true,language:"en").equatable()
        }.padding(18).environment(\.locale,Locale(identifier:"en_US"))
    }
}

@main struct EditorStress {
    @MainActor static func main() throws {
        _=NSApplication.shared;NSApp.setActivationPolicy(.accessory)
        let root=URL(fileURLWithPath:CommandLine.arguments[1])
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        var checks=[String](),failures=[String](),actions=0
        func check(_ condition:Bool,_ label:String) {
            if condition { checks.append(label) } else { failures.append(label);print("FAIL: \(label)") }
        }
        func make()->EditorModel {
            let m=EditorModel(launchImmediately:false,stateURL:root.appendingPathComponent(UUID().uuidString+"/session.json"))
            m.ready=true;m.busy=false;m.source=root.appendingPathComponent("中文 stress.RAF")
            return m
        }
        for operation in ["undo","redo","reset","compare","full","retry","delete"] {
            let m=make();m.recipe.exposure_ev=0.25;m.commit()
            m.recipe.exposure_ev=0.5;m.commit();m.undo()
            var region=LocalAdjustment();region.id="export-region";m.recipe.local_adjustments=[region];m.committed=m.recipe
            m.exporting=true;m.request("export")
            let before=m.recipe,id=m.activeID,undo=m.undoStack,redo=m.redoStack
            switch operation {
            case "undo":m.undo()
            case "redo":m.redo()
            case "reset":m.reset()
            case "compare":m.compare()
            case "full":m.fullSize()
            case "retry":m.retry()
            default:m.deleteLocal(region.id)
            }
            check(m.recipe==before && m.activeID==id && m.undoStack==undo && m.redoStack==redo,"Export survives conflicting \(operation) action")
            m.cancel();m.receive(["id":id,"event":"result","result":["key":"late-export"]])
            check(m.result==nil,"Cancelled export ignores late result after \(operation)")
        }
        do {
            check(Recipe.decode(["style":"unknown"])==nil && Recipe.decode(["white_balance":"unknown"])==nil,"Invalid saved modes cannot enter renderer")
            let state=root.appendingPathComponent("restore.json")
            try JSONSerialization.data(withJSONObject:["source":root.appendingPathComponent("moved.RAF").path,
                "recipe":["exposure_ev":999,"temperature_k":99999,"saturation":-5]]) .write(to:state)
            let m=EditorModel(launchImmediately:false,stateURL:state);m.restoreWindow()
            check(m.recipe.exposure_ev==3 && m.recipe.temperature_k==15000 && m.recipe.saturation==0.8,"Restored out-of-range fields cannot reach live renderer")
            let original=root.appendingPathComponent("existing.RAF");try Data([1]).write(to:original)
            try JSONSerialization.data(withJSONObject:["source":original.path,"recipe":["style":"unknown"]]).write(to:state)
            let broken=EditorModel(launchImmediately:false,stateURL:state);broken.ready=true;broken.restoreWindow()
            check(broken.lastCommand=="prepare","Invalid window recipe requests last valid backend edit instead of overwriting it with defaults")
        }
        let m=make();m.recipe.white_balance="custom";m.committed=m.recipe
        let host=NSHostingView(rootView:StressControls(model:m));host.frame=NSRect(x:0,y:0,width:350,height:1000)
        let window=NSWindow(contentRect:host.frame,styleMask:[.titled,.resizable],backing:.buffered,defer:false)
        window.contentView=host;window.makeKeyAndOrderFront(nil)
        defer { window.orderOut(nil);m.close() }
        func fields(_ v:NSView)->[NSTextField] { (v as? NSTextField).map{[$0]} ?? v.subviews.flatMap(fields) }
        RunLoop.main.run(until:Date().addingTimeInterval(0.3))
        for fieldIndex in 0..<9 {
            for input in ["999999","-999999","1e309","NaN","中文","","0","0.25","-12.5"] {
                let field=fields(host)[fieldIndex]
                field.selectText(nil)
                if let editor=field.currentEditor() {
                    editor.selectAll(nil);editor.insertText(input)
                    RunLoop.main.run(until:Date().addingTimeInterval(0.03))
                    check(m.recipe==m.recipe.normalized(fallback:m.committed),"Field \(fieldIndex) input '\(input)' stays bounded during live preview")
                    window.makeFirstResponder(nil)
                    RunLoop.main.run(until:Date().addingTimeInterval(0.3))
                    check(m.recipe==m.recipe.normalized(),"Field \(fieldIndex) input '\(input)' is finite after commit")
                    let r=m.recipe
                    let displayed=[r.exposure_ev,r.highlight_ev*50,r.shadow_ev*50,r.white_ev*50,r.black_ev*50,
                                   Double(r.temperature_k),-r.tint,r.saturation*100,r.hdr_strength*100]
                    let expected=fieldIndex==0 || fieldIndex==6 ? (displayed[fieldIndex]*100).rounded(.toNearestOrEven)/100:displayed[fieldIndex].rounded(.toNearestOrEven)
                    check(abs(fields(host)[fieldIndex].doubleValue-expected)<0.011,"Field \(fieldIndex) input '\(input)' displays the committed bounded value")
                    actions+=1
                } else { failures.append("Could not focus field \(fieldIndex)") }
            }
        }
        var seed:UInt64=0x20261001
        func random(_ n:Int)->Int { seed=seed &* 6364136223846793005 &+ 1442695040888963407;return Int((seed>>32)%UInt64(n)) }
        let keys:[WritableKeyPath<Recipe,Double>]=[\.exposure_ev,\.highlight_ev,\.shadow_ev,\.white_ev,\.black_ev,\.tint,\.saturation,\.hdr_strength,\.sdr_exposure_ev]
        for i in 0..<3000 {
            let obsolete=m.activeID
            switch random(18) {
            case 0...4:
                m.beginDrag();let key=keys[random(keys.count)]
                for _ in 0..<5 { m.recipe[keyPath:key]=Double(random(1001)-500)/200;m.recipe=m.recipe.normalized();m.sliderChanged();actions+=1 }
                m.endDrag()
            case 5:m.undo()
            case 6:m.redo()
            case 7:m.reset()
            case 8:m.cancel();m.receive(["id":m.activeID,"event":"cancelled"])
            case 9:m.compare()
            case 10:m.fullSize();m.beginDrag();m.recipe.exposure_ev=0.1;m.endDrag()
            case 11:m.open(root.appendingPathComponent("switch-\(i%4).RAF"))
            case 12:
                m.localEditing=true;m.addLocal(at:NSPoint(x:Double(random(101))/100,y:Double(random(101))/100));m.useSoftSelection()
            case 13:if let first=m.recipe.local_adjustments.first { m.deleteLocal(first.id) }
            case 14:m.localEditing=true;m.addLocal(at:NSPoint(x:0.5,y:0.5));m.reset()
            case 15:m.recipe.white_balance=["auto","camera","custom"][random(3)];m.scheduleWhiteBalance()
            case 16:m.recipe.style=random(2)==0 ? "phone-clear":"phone-natural";m.commit()
            default:m.hdr.toggle();m.fit();m.localBypass.toggle()
            }
            actions+=1
            if obsolete != m.activeID {
                let previous=m.result?["key"] as? String
                for event in ["opened","progress","error","cancelled","result"] {
                    m.receive(["id":obsolete,"event":event,"result":["key":"stale"],"source":["sha256":"stale"]])
                }
                check(m.result?["key"] as? String==previous,"Stale events ignored at step \(i)")
            }
            check(m.undoStack.count<=100 && m.redoStack.count<=100 && m.cachedPreviews.count<=8,"History and cache bounded at step \(i)")
            check(m.recipe.local_adjustments.count<=8 && Set(m.recipe.local_adjustments.map(\.id)).count==m.recipe.local_adjustments.count,"Local regions bounded and distinct at step \(i)")
            check(m.recipe==m.recipe.normalized(),"Recipe finite and in range at step \(i)")
            if i%100==0 {
                m.receive(["id":m.activeID,"event":"result","result":["key":"current-\(i)","recipe":m.recipe.dictionary]])
                RunLoop.main.run(until:Date().addingTimeInterval(0.02))
            }
        }
        m.pending?.cancel();m.cancelLocalSelection()
        try JSONSerialization.data(withJSONObject:["seed":"0x20261001","actions":actions,"passed":checks.count,"failures":failures],options:.prettyPrinted)
            .write(to:root.appendingPathComponent("stress.json"))
        print("\(actions) actions; \(checks.count) checks passed; \(failures.count) failures")
        if !failures.isEmpty { exit(1) }
    }
}
