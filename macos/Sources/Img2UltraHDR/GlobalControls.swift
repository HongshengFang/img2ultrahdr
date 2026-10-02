import SwiftUI

/// Global controls keep their layout while only a local region is being edited.
struct GlobalControls:View,Equatable {
    let model:EditorModel
    let recipe:Recipe
    let tone:Bool
    let customReady:Bool
    let language:String
    static func ==(lhs:Self,rhs:Self)->Bool {
        lhs.model === rhs.model && lhs.recipe==rhs.recipe && lhs.tone==rhs.tone && lhs.customReady==rhs.customReady && lhs.language==rhs.language
    }
    var body:some View {
        VStack(alignment:.leading,spacing:18) {
            if tone {
                Picker(L("风格"),selection:Binding(get:{model.recipe.style},set:{model.recipe.style=$0})) {
                    Text(L("Clear")).tag("phone-clear");Text(L("Natural")).tag("phone-natural")
                }.pickerStyle(.segmented).onChange(of:recipe.style) { _,_ in model.commit() }
                Text(L(recipe.style == "phone-clear" ? "Clear V8 R5 · 明快 HDR":"Natural V8 · 自然人像")).font(.caption).foregroundStyle(.secondary)
                Divider()
                adjustment("曝光",\.exposure_ev,-3...3,unit:" EV")
                adjustment("高光",\.highlight_ev,-2...2,multiplier:50)
                adjustment("阴影",\.shadow_ev,-2...2,multiplier:50)
                adjustment("白色色阶",\.white_ev,-2...2,multiplier:50)
                adjustment("黑色色阶",\.black_ev,-2...2,multiplier:50)
                Text(L("0 表示保留自动效果；曝光调整整体亮度，其余四项调整不同明暗范围。")).font(.caption).foregroundStyle(.secondary)
            } else {
                Picker(L("白平衡"),selection:Binding(get:{model.recipe.white_balance},set:{model.recipe.white_balance=$0})) {
                    Text(L("自动")).tag("auto");Text(L("相机")).tag("camera");Text(L("自定义")).tag("custom")
                }.onChange(of:recipe.white_balance) { _,_ in model.scheduleWhiteBalance() }
                if recipe.white_balance=="custom" {
                    VStack(alignment:.leading,spacing:5) {
                        HStack {
                            Text(L("色温")).onTapGesture(count:2) { model.recipe.temperature_k=5600;model.commit() }.help(L("双击名称重置此项"));Spacer()
                            TextField("K",value:Binding(get:{model.recipe.temperature_k},set:{model.recipe.temperature_k=$0;model.scheduleEdit()}),format:.number.grouping(.never)).frame(width:70).multilineTextAlignment(.trailing);Text(L("K"))
                        }
                        Slider(value:Binding(get:{Double(model.recipe.temperature_k)},set:{model.recipe.temperature_k=Int($0);model.sliderChanged()}),in:2000...15000,step:50,onEditingChanged:{ $0 ? model.beginDrag():model.endDrag() }).accessibilityLabel(L("色温"))
                    }.disabled(!customReady)
                    adjustment("色调 · 绿 ↔ 洋红",\.tint,-100...100,multiplier:-1,flipSlider:true).disabled(!customReady)
                } else { Text(L(recipe.white_balance=="auto" ? "由 RAW 引擎自动判断光源":"使用相机记录的白平衡")).font(.caption).foregroundStyle(.secondary) }
                Divider()
                adjustment("饱和度",\.saturation,0.8...1.2,multiplier:100,unit:"%")
                adjustment("HDR 强度",\.hdr_strength,0...1,multiplier:100,unit:"%")
                DisclosureGroup(L("更多")) { adjustment("SDR 亮度",\.sdr_exposure_ev,-2...2,unit:" EV").padding(.top,12) }
            }
        }
    }
    private func adjustment(_ title:String,_ key:WritableKeyPath<Recipe,Double>,_ range:ClosedRange<Double>,multiplier:Double=1,unit:String="",flipSlider:Bool=false)->some View {
        AdjustmentControl(model:model,title:title,key:key,value:recipe[keyPath:key],range:range,multiplier:multiplier,unit:unit,flipSlider:flipSlider)
    }
}

struct AdjustmentControl:View {
    let model:EditorModel
    let title:String
    let key:WritableKeyPath<Recipe,Double>
    // A value snapshot participates in SwiftUI's view comparison. Reading only
    // a reference inside Binding leaves the numeric field frozen during drags.
    let value:Double
    let range:ClosedRange<Double>
    var multiplier:Double=1
    var unit:String=""
    var flipSlider=false
    var body:some View {
        VStack(alignment:.leading,spacing:5) {
            HStack {
                Text(L(title)).onTapGesture(count:2) { model.resetAdjustment(key) }.help(L("双击名称重置此项"));Spacer()
                TextField(L(title),value:Binding(get:{model.recipe[keyPath:key]*multiplier},set:{model.recipe[keyPath:key]=$0/multiplier;model.scheduleEdit()}),format:.number.precision(.fractionLength(abs(multiplier)>=50 ? 0:2)))
                    .textFieldStyle(.plain).multilineTextAlignment(.trailing).frame(width:58)
                Text(unit).foregroundStyle(.secondary)
            }.font(.callout)
            Slider(value:Binding(get:{model.recipe[keyPath:key]*(flipSlider ? -1:1)},set:{model.recipe[keyPath:key]=$0*(flipSlider ? -1:1);model.sliderChanged()}),in:range,onEditingChanged:{ $0 ? model.beginDrag():model.endDrag() }).accessibilityLabel(L(title))
        }
    }
}
