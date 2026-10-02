import Foundation
import AppKit
import SwiftUI

struct LocalAdjustment: Codable, Equatable, Identifiable {
    var id = UUID().uuidString
    var enabled = true
    var mode = "soft"
    var shape = "circle"
    var center_x = 0.5
    var center_y = 0.5
    var radius_x = 0.2
    var radius_y = 0.2
    var rotation = 0.0
    var direction = "brighten"
    var direction_chosen = false
    var amount = 0.0
    var mask_ref: String?
    enum CodingKeys:String,CodingKey { case id,enabled,mode,shape,center_x,center_y,radius_x,radius_y,rotation,direction,direction_chosen,amount,mask_ref }
    init() {}
    init(from decoder:Decoder) throws {
        let c=try decoder.container(keyedBy:CodingKeys.self)
        id=try c.decode(String.self,forKey:.id)
        enabled=try c.decodeIfPresent(Bool.self,forKey:.enabled) ?? true
        mode=try c.decodeIfPresent(String.self,forKey:.mode) ?? "soft"
        shape=try c.decodeIfPresent(String.self,forKey:.shape) ?? "circle"
        center_x=try c.decodeIfPresent(Double.self,forKey:.center_x) ?? 0.5
        center_y=try c.decodeIfPresent(Double.self,forKey:.center_y) ?? 0.5
        radius_x=try c.decodeIfPresent(Double.self,forKey:.radius_x) ?? 0.2
        radius_y=try c.decodeIfPresent(Double.self,forKey:.radius_y) ?? 0.2
        rotation=try c.decodeIfPresent(Double.self,forKey:.rotation) ?? 0
        direction=try c.decodeIfPresent(String.self,forKey:.direction) ?? "brighten"
        direction_chosen=try c.decodeIfPresent(Bool.self,forKey:.direction_chosen) ?? false
        amount=try c.decodeIfPresent(Double.self,forKey:.amount) ?? 0
        mask_ref=try c.decodeIfPresent(String.self,forKey:.mask_ref)
        let numbers=[(center_x,0.0,1.0),(center_y,0,1),(radius_x,0.00001,4),(radius_y,0.00001,4),(rotation,-180,180),(amount,0,1)]
        guard !id.isEmpty,id.count<=64,id.range(of:"^[A-Za-z0-9-]+$",options:.regularExpression) != nil,
              ["smart","soft"].contains(mode),["circle","ellipse"].contains(shape),["brighten","darken"].contains(direction),
              numbers.allSatisfy({$0.0.isFinite && $0.0 >= $0.1 && $0.0 <= $0.2}),
              mask_ref == nil || mask_ref!.range(of:"^[0-9a-f]{64}$",options:.regularExpression) != nil,
              mode != "smart" || mask_ref != nil else {
            throw DecodingError.dataCorruptedError(forKey:.id,in:c,debugDescription:"Invalid local adjustment")
        }
    }
    var ev: Double { enabled ? 0.5*amount*(direction == "darken" ? -1:1):0 }
    static func make(point: NSPoint, size: NSSize) -> LocalAdjustment {
        var r=LocalAdjustment();r.center_x=min(1,max(0,point.x));r.center_y=min(1,max(0,point.y))
        let short=max(1,min(size.width,size.height))
        r.radius_x=short*0.2/max(size.width,1);r.radius_y=short*0.2/max(size.height,1);return r
    }
}

extension Recipe {
    var withoutLocal:Recipe { var copy=self;copy.local_adjustments=[];return copy }
}

extension EditorModel {
    var localImageSize:NSSize { NSSize(width:result?["width"] as? Int ?? resultPacket?.width ?? 1536,
                                       height:result?["height"] as? Int ?? resultPacket?.height ?? 1024) }
    var selectedLocal:LocalAdjustment? { pendingLocal ?? recipe.local_adjustments.first { $0.id==selectedLocalID } }
    var displayRecipe:Recipe { localBypass ? recipe.withoutLocal:recipe }
    var hasInvalidActiveLocal:Bool { recipe.local_adjustments.contains { $0.mode=="smart" && invalidLocalIDs.contains($0.id) && $0.ev != 0 } }
    func toggleLocalEditing() {
        guard !exporting else { return }
        localEditing.toggle();localBypass=false
        if localEditing {
            showInitial=false;nativeSize=false
            if let previewResult { result=previewResult }
            fit();addingLocal=recipe.local_adjustments.isEmpty
            if selectedLocalID == nil { selectedLocalID=recipe.local_adjustments.first?.id }
        } else if pendingLocal != nil { cancelLocalSelection() }
    }
    func addLocal(at point:NSPoint, replacing:LocalAdjustment? = nil) {
        guard localEditing,!exporting,pendingLocal==nil,(recipe.local_adjustments.count<8 || replacing != nil) else { return }
        var region=replacing ?? LocalAdjustment.make(point:point,size:localImageSize)
        region.center_x=point.x;region.center_y=point.y
        pendingLocal=region;selectedLocalID=region.id
        request("select_region",extras:["point":[region.center_x,region.center_y],"recipe":recipe.withoutLocal.dictionary])
        selectionTimeout?.cancel()
        let timeout=DispatchWorkItem { [weak self] in
            guard let self,self.pendingLocal?.id==region.id else { return }
            self.useSoftSelection();self.notice="选择超时，已使用柔和范围。"
        }
        selectionTimeout=timeout;DispatchQueue.main.asyncAfter(deadline:.now()+8,execute:timeout)
    }
    func cancelLocalSelection() {
        selectionTimeout?.cancel();selectionTimeout=nil
        pendingLocal=nil;activeID=UUID().uuidString;busy=false
        engine.send(["command":"cancel","id":UUID().uuidString,"session_id":session,"revision":revision])
    }
    func useSoftSelection() {
        guard var region=pendingLocal else { return };region.mode="soft"
        cancelLocalSelection();finishLocalSelection(region)
    }
    func finishLocalSelection(_ region:LocalAdjustment) {
        invalidLocalIDs.remove(region.id)
        if let index=recipe.local_adjustments.firstIndex(where:{$0.id==region.id}) { recipe.local_adjustments[index]=region }
        else { recipe.local_adjustments.append(region) }
        addingLocal=false;selectedLocalID=region.id;commit()
    }
    func updateLocal(_ change:(inout LocalAdjustment)->Void, commitNow:Bool=true) {
        guard !exporting,let index=recipe.local_adjustments.firstIndex(where:{$0.id==selectedLocalID}) else { return }
        let amount=recipe.local_adjustments[index].amount
        change(&recipe.local_adjustments[index])
        if recipe.local_adjustments[index].amount != amount { recipe.local_adjustments[index].direction_chosen=true }
        if commitNow { commit() } else { sliderChanged() }
    }
    func localDirection(_ direction:String) {
        updateLocal { r in
            r.direction=direction
            if !r.direction_chosen && r.amount==0 { r.amount=0.5 }
            r.direction_chosen=true
        }
    }
    func deleteLocal(_ id:String) {
        guard !exporting else { return }
        invalidLocalIDs.remove(id)
        recipe.local_adjustments.removeAll { $0.id==id }
        if selectedLocalID==id { selectedLocalID=recipe.local_adjustments.first?.id }
        addingLocal=recipe.local_adjustments.isEmpty;commit()
    }
    func setLocalShape(_ shape:String) {
        let size=localImageSize
        updateLocal { r in
            let radius=sqrt(r.radius_x*size.width*r.radius_y*size.height)
            r.shape=shape;r.rotation=0
            let aspect=shape=="circle" ? 1.0:1.5
            r.radius_x=radius*sqrt(aspect)/size.width;r.radius_y=radius/sqrt(aspect)/size.height
        }
    }
    func localDiameter(_ r:LocalAdjustment)->Double {
        200*sqrt(r.radius_x*localImageSize.width*r.radius_y*localImageSize.height)/min(localImageSize.width,localImageSize.height)
    }
    func localAspect(_ r:LocalAdjustment)->Double { r.radius_x*localImageSize.width/(r.radius_y*localImageSize.height) }
    func setLocalDiameter(_ value:Double) {
        let size=localImageSize
        updateLocal({ r in
            let aspect=localAspect(r),radius=min(200,max(5,value))/200*min(size.width,size.height)
            r.radius_x=radius*sqrt(aspect)/size.width;r.radius_y=radius/sqrt(aspect)/size.height
        },commitNow:false)
    }
    func setLocalAspect(_ value:Double) {
        let size=localImageSize
        updateLocal({ r in
            let radius=sqrt(r.radius_x*size.width*r.radius_y*size.height),aspect=min(4,max(0.25,value))
            r.radius_x=radius*sqrt(aspect)/size.width;r.radius_y=radius/sqrt(aspect)/size.height
        },commitNow:false)
    }
}

struct LocalControls:View {
    @ObservedObject var model:EditorModel
    func range(_ title:String,value:Binding<Double>,limits:ClosedRange<Double>,suffix:String="") -> some View {
        VStack(alignment:.leading,spacing:5) {
            HStack { Text(L(title));Spacer();Text(String(format:"%.0f",value.wrappedValue)+suffix).monospacedDigit().frame(width:65,alignment:.trailing) }
            Slider(value:value,in:limits,onEditingChanged: { $0 ? model.beginDrag():model.endDrag() }).accessibilityLabel(L(title))
        }
    }
    var body:some View {
        VStack(alignment:.leading,spacing:12) {
            HStack {
                Text(L("局部调光")).font(.headline);Spacer()
                Button(L(model.localEditing ? "完成":"编辑"),action:model.toggleLocalEditing)
            }
            if model.localEditing {
                Text(L(model.addingLocal || model.recipe.local_adjustments.isEmpty ? "点击照片中想调整的位置":"点击标记重新调整 · 空格拖动平移"))
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(Array(model.recipe.local_adjustments.enumerated()),id:\.element.id) { index,r in
                    LocalRegionRow(model:model,id:r.id,number:index+1,enabled:r.enabled,selected:model.selectedLocalID==r.id,
                                   invalid:model.invalidLocalIDs.contains(r.id),language:AppPreferences.shared.language).equatable()
                }
                Button(L("添加区域")) { model.addingLocal=true }.disabled(model.recipe.local_adjustments.count>=8 || model.pendingLocal != nil)
                if model.pendingLocal != nil {
                    HStack { ProgressView().controlSize(.small);Text(L("正在选择局部范围")) }
                    Button(L("使用柔和范围"),action:model.useSoftSelection)
                } else if let region=model.selectedLocal {
                    LocalModeControls(model:model,id:region.id,mode:region.mode,direction:region.direction,
                                      chosen:region.direction_chosen,language:AppPreferences.shared.language).equatable()
                    range("力度",value:Binding(get:{region.amount*100},set:{ v in model.updateLocal({$0.amount=v/100},commitNow:false) }),limits:0...100,suffix:"%")
                    if region.mode=="soft" {
                        LocalRangeControls(model:model,region:region,size:model.localImageSize,
                                           language:AppPreferences.shared.language).equatable()
                    }
                }
                Text(L("按住查看局部调整前")).padding(8).frame(maxWidth:.infinity).background(.quaternary).clipShape(RoundedRectangle(cornerRadius:6))
                    .gesture(DragGesture(minimumDistance:0).onChanged { _ in model.localBypass=true }.onEnded { _ in model.localBypass=false })
                    .accessibilityAddTraits(.isButton)
                    .accessibilityAction { model.localBypass.toggle() }
            } else {
                Text(L("点选位置，提亮或压暗 · 最多 8 个区域")).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// Snapshot only the values these controls display. Strength changes should
/// not rebuild AppKit's segmented control or lay out the collapsed range.
struct LocalModeControls:View,Equatable {
    let model:EditorModel
    let id:String
    let mode:String
    let direction:String
    let chosen:Bool
    let language:String
    static func ==(lhs:Self,rhs:Self)->Bool {
        lhs.model === rhs.model && lhs.id==rhs.id && lhs.mode==rhs.mode && lhs.direction==rhs.direction && lhs.chosen==rhs.chosen && lhs.language==rhs.language
    }
    var body:some View {
        VStack(alignment:.leading,spacing:12) {
            Picker(L("范围方式"),selection:Binding(get:{mode},set:{ value in
                if value=="soft" { model.updateLocal { $0.mode="soft" } }
                else if let region=model.recipe.local_adjustments.first(where:{$0.id==id}) {
                    model.addLocal(at:NSPoint(x:region.center_x,y:region.center_y),replacing:region)
                }
            })) { Text(L("智能选择")).tag("smart");Text(L("柔和范围")).tag("soft") }.pickerStyle(.segmented).labelsHidden().accessibilityLabel(L("范围方式"))
            if mode=="smart" {
                Button(L("重新选择范围")) {
                    if let region=model.recipe.local_adjustments.first(where:{$0.id==id}) {
                        model.addLocal(at:NSPoint(x:region.center_x,y:region.center_y),replacing:region)
                    }
                }
            }
            HStack {
                Button(L("提亮")) { model.localDirection("brighten") }
                    .buttonStyle(.bordered).tint(chosen && direction=="brighten" ? .accentColor:.secondary)
                Button(L("压暗")) { model.localDirection("darken") }
                    .buttonStyle(.bordered).tint(chosen && direction=="darken" ? .accentColor:.secondary)
            }
        }
    }
}

// SDK 26 also exports a State macro whose compiler plugin is absent from
// Command Line Tools. Resolve the macOS-15 property wrapper as a type.
private typealias LocalRangeState = SwiftUI.State<Bool>
struct LocalRangeControls:View,Equatable {
    let model:EditorModel
    let region:LocalAdjustment
    let size:NSSize
    let language:String
    @LocalRangeState private var expanded=false
    static func ==(lhs:Self,rhs:Self)->Bool {
        let a=lhs.region,b=rhs.region
        return lhs.model === rhs.model && lhs.language==rhs.language && lhs.size==rhs.size && a.id==b.id && a.shape==b.shape &&
            a.center_x==b.center_x && a.center_y==b.center_y && a.radius_x==b.radius_x && a.radius_y==b.radius_y && a.rotation==b.rotation
    }
    var body:some View {
        DisclosureGroup(L("范围"),isExpanded:$expanded) {
            if expanded {
                VStack(alignment:.leading,spacing:12) {
                    Picker(L("形状"),selection:Binding(get:{region.shape},set:model.setLocalShape)) {
                        Text(L("圆形")).tag("circle");Text(L("椭圆形")).tag("ellipse")
                    }.pickerStyle(.segmented)
                    controls.range("大小",value:Binding(get:{model.localDiameter(region)},set:model.setLocalDiameter),limits:5...200,suffix:"%")
                    if region.shape=="ellipse" {
                        HStack { Text(L("长宽比"));Spacer();Text(String(format:"%.2f",model.localAspect(region))).frame(width:65,alignment:.trailing) }
                        Slider(value:Binding(get:{model.localAspect(region)},set:model.setLocalAspect),in:0.25...4,onEditingChanged: { $0 ? model.beginDrag():model.endDrag() }).accessibilityLabel(L("长宽比"))
                        controls.range("角度",value:Binding(get:{region.rotation},set:{ v in model.updateLocal({$0.rotation=v},commitNow:false) }),limits:-180...180,suffix:"°")
                    }
                    controls.range("水平位置",value:Binding(get:{region.center_x*100},set:{v in model.updateLocal({$0.center_x=v/100},commitNow:false)}),limits:0...100,suffix:"%")
                    controls.range("垂直位置",value:Binding(get:{region.center_y*100},set:{v in model.updateLocal({$0.center_y=v/100},commitNow:false)}),limits:0...100,suffix:"%")
                }.padding(.top,10)
            }
        }
    }
    private var controls:LocalControls { LocalControls(model:model) }
}

struct LocalRegionRow:View,Equatable {
    let model:EditorModel
    let id:String
    let number:Int
    let enabled:Bool
    let selected:Bool
    let invalid:Bool
    let language:String
    static func ==(lhs:Self,rhs:Self)->Bool {
        lhs.model === rhs.model && lhs.id==rhs.id && lhs.number==rhs.number && lhs.enabled==rhs.enabled && lhs.selected==rhs.selected && lhs.invalid==rhs.invalid && lhs.language==rhs.language
    }
    var body:some View {
        HStack {
            Button { model.selectedLocalID=id;model.addingLocal=false } label: {
                Text(L("区域")+" \(number)").fontWeight(selected ? .semibold:.regular)
            }
            if invalid { Image(systemName:"exclamationmark.triangle.fill").foregroundStyle(.orange).help(L("选区文件不可用，请重新选择或使用柔和范围。")) }
            Spacer()
            Toggle(L("启用区域"),isOn:Binding(get:{enabled},set:{ value in
                model.selectedLocalID=id;model.updateLocal { $0.enabled=value }
            })).labelsHidden().toggleStyle(.checkbox)
            Button { model.deleteLocal(id) } label: { Image(systemName:"trash") }.help(L("删除区域"))
        }
    }
}
