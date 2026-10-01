import SwiftUI
import AppKit
import simd

final class AppPreferences: ObservableObject {
    static let shared=AppPreferences()
    @Published var language:String { didSet { UserDefaults.standard.set(language,forKey:"language");applyMenus() } }
    @Published var appearance:String { didSet { UserDefaults.standard.set(appearance,forKey:"appearance");applyAppearance() } }
    @Published var histogramRGB=false
    @Published var showBlacks=false
    @Published var showHighlights=false
    var clipping:UInt32 { (showBlacks ? 1:0)+(showHighlights ? 2:0) }
    var scheme:ColorScheme? { appearance=="light" ? .light:(appearance=="dark" ? .dark:nil) }
    var locale:Locale { Locale(identifier:language=="en" ? "en_US":"zh_Hans_CN") }
    init() {
        let defaults=UserDefaults.standard
        language=defaults.string(forKey:"language")=="en" ? "en":"zh-Hans"
        let stored=defaults.string(forKey:"appearance") ?? "system"
        appearance=["light","dark","system"].contains(stored) ? stored:"system"
    }
    func applyAppearance() { NSApp?.appearance=appearance=="system" ? nil:NSAppearance(named:appearance=="dark" ? .darkAqua:.aqua) }
    func applyMenus() {
        // SwiftUI's built-in menu headings follow the process locale. Update
        // those AppKit titles too when the in-app language changes at runtime.
        DispatchQueue.main.asyncAfter(deadline:.now()+0.1) {
            let pairs=[("File","文件"),("Edit","编辑"),("View","显示"),("Window","窗口"),("Help","帮助"),
                ("About Img2UltraHDR","关于 Img2UltraHDR"),("Hide Img2UltraHDR","隐藏 Img2UltraHDR"),
                ("Hide Others","隐藏其他"),("Show All","全部显示"),("Quit Img2UltraHDR","退出 Img2UltraHDR"),
                ("Services","服务"),("Close Window","关闭窗口"),("Minimize","最小化"),("Zoom","缩放"),
                ("Bring All to Front","前置全部窗口"),("Enter Full Screen","进入全屏幕"),("Exit Full Screen","退出全屏幕"),
                ("Cut","剪切"),("Copy","拷贝"),("Paste","粘贴"),("Select All","全选"),("Settings","设置")]
            func translate(_ menu:NSMenu) {
                for item in menu.items {
                    if let pair=pairs.first(where:{$0.0==item.title || $0.1==item.title}) { item.title=self.language=="en" ? pair.0:pair.1 }
                    if let submenu=item.submenu { translate(submenu) }
                }
            }
            if let menu=NSApp?.mainMenu { translate(menu) }
            for window in NSApp?.windows ?? [] where window.title=="Settings" || window.title=="设置" { window.title=self.language=="en" ? "Settings":"设置" }
        }
    }
    func toggleClipping() { let on = !(showBlacks && showHighlights);showBlacks=on;showHighlights=on }
}

func L(_ key:String) -> String {
    let language=AppPreferences.shared.language
    guard language=="en",let bundle=AppResources.english else { return key }
    let translated=bundle.localizedString(forKey:key,value:key,table:nil)
    if translated != key { return translated }
    if key.hasSuffix("× 显示余量") { return key.replacingOccurrences(of:"显示余量",with:"display headroom") }
    for prefix in ["已导出：","调整暂时无法保存："] where key.hasPrefix(prefix) {
        return bundle.localizedString(forKey:prefix,value:prefix,table:nil)+key.dropFirst(prefix.count)
    }
    return key
}

struct PreferencesView:View {
    @ObservedObject var preferences=AppPreferences.shared
    var body:some View {
        Form {
            Picker(L("语言"),selection:$preferences.language) { Text("中文").tag("zh-Hans");Text("English").tag("en") }
            Picker(L("外观"),selection:$preferences.appearance) { Text(L("跟随系统")).tag("system");Text(L("浅色")).tag("light");Text(L("深色")).tag("dark") }
            Text(L("语言和外观设置立即生效，不会改变照片。")).font(.caption).foregroundStyle(.secondary)
        }.padding(24).frame(width:380).preferredColorScheme(preferences.scheme).onAppear { preferences.applyMenus() }
    }
}

struct HistogramPanel:View {
    @ObservedObject var readouts:PhotoReadouts
    @ObservedObject var preferences=AppPreferences.shared
    var body:some View {
        VStack(alignment:.leading,spacing:7) {
            HStack {
                Button { preferences.showBlacks.toggle() } label: { Image(systemName:"triangle.fill").foregroundStyle(preferences.showBlacks ? Color.blue:.secondary) }.help(L("显示接近黑位的区域")).accessibilityLabel(L("显示接近黑位的区域"))
                Picker(L("直方图"),selection:$preferences.histogramRGB) { Text(L("亮度")).tag(false);Text("RGB").tag(true) }.pickerStyle(.segmented).labelsHidden()
                Button { preferences.showHighlights.toggle() } label: { Image(systemName:"triangle.fill").foregroundStyle(preferences.showHighlights ? Color.red:.secondary) }.help(L("显示接近输出上限的区域")).accessibilityLabel(L("显示接近输出上限的区域"))
            }.buttonStyle(.plain)
            Canvas { context,size in
                let channels=preferences.histogramRGB ? [0,1,2]:[3]
                let colors:[Color]=[.red,.green,.blue,.primary]
                let maximum=max(1,channels.flatMap { c in Array(readouts.bins[(c*256)..<(c*256+256)]) }.max() ?? 1)
                for c in channels {
                    var path=Path();path.move(to:CGPoint(x:0,y:size.height))
                    for i in 0..<256 {
                        let x=Double(i)/255*size.width
                        let y=size.height*(1-sqrt(Double(readouts.bins[c*256+i])/Double(maximum)))
                        path.addLine(to:CGPoint(x:x,y:y))
                    }
                    path.addLine(to:CGPoint(x:size.width,y:size.height));path.closeSubpath()
                    context.fill(path,with:.color(colors[c].opacity(preferences.histogramRGB ? 0.38:0.5)))
                }
                if readouts.hdr {
                    for value in [1.0,2.0,4.0] {
                        let x=(value==1 ? 191:192+log2(value)/log2(1000.0/203)*63)/255*size.width
                        var marker=Path();marker.move(to:CGPoint(x:x,y:0));marker.addLine(to:CGPoint(x:x,y:size.height))
                        context.stroke(marker,with:.color(.secondary.opacity(0.7)),style:StrokeStyle(lineWidth:1,dash:value==1 ? []:[3,3]))
                    }
                    let headroom=min(1000.0/203,max(1,readouts.headroom))
                    let x=(192+log2(headroom)/log2(1000.0/203)*63)/255*size.width
                    var line=Path();line.move(to:CGPoint(x:x,y:0));line.addLine(to:CGPoint(x:x,y:size.height))
                    context.stroke(line,with:.color(.orange),lineWidth:2)
                }
            }.frame(height:100).background(Color.primary.opacity(0.04)).clipShape(RoundedRectangle(cornerRadius:4))
            HStack { Text("0");Spacer();Text(readouts.hdr ? L("SDR 白  |  +1  +2 EV"):"255") }.font(.system(size:10)).foregroundStyle(.secondary)
            if readouts.hdr { Text(L("输出上限：1000 nit（+2.30 EV）")).font(.caption2).foregroundStyle(.secondary) }
            HStack { Text(L(readouts.kind=="full" ? "全尺寸采样":readouts.kind=="export" ? "导出文件采样":"预览采样"));Spacer();Text(L(readouts.state)) }.font(.caption2).foregroundStyle(.secondary)
            Text(readouts.hdr ? L("RGB · 线性 Rec.2020 (%)"):"RGB · Display P3 (0–255)").font(.caption2).foregroundStyle(.secondary)
            Text(rgbText).font(.system(.caption,design:.monospaced)).monospacedDigit()
            HStack { Text(L("亮度"));Spacer();Text(luminanceText).monospacedDigit() }.font(.caption)
            HStack { Text(L("屏幕 HDR 余量"));Spacer();Text(String(format:"%.1f× / +%.1f EV",readouts.headroom,log2(max(readouts.headroom,1)))).monospacedDigit() }.font(.caption)
            if readouts.hdr { Text(L("橙线：屏幕范围 · nit 为内容参考亮度")).font(.caption2).foregroundStyle(.secondary) }
        }.accessibilityElement(children:.contain).accessibilityLabel(L("直方图和像素读数"))
    }
    var rgbText:String {
        guard let p=readouts.pixel else { return "R —     G —     B —" }
        func encoded(_ v:Float)->Double { let x=Double(v);return (x<=0.0031308 ? x*12.92:1.055*pow(max(x,0),1/2.4)-0.055)*255 }
        return readouts.hdr ? String(format:"R %.1f  G %.1f  B %.1f",p.x*100,p.y*100,p.z*100):String(format:"R %.0f  G %.0f  B %.0f",encoded(p.x),encoded(p.y),encoded(p.z))
    }
    var luminanceText:String {
        guard let p=readouts.pixel else { return "—" }
        let y=Double(simd_dot(p,readouts.hdr ? SIMD3(0.26270021,0.67799807,0.05930172):SIMD3(0.22897456,0.69173852,0.07928691)))
        if !readouts.hdr { return String(format:"%.1f%%",y*100) }
        return y>0 ? String(format:"%.1f nit / %+.2f EV",y*203,log2(y)):"0 nit / −∞ EV"
    }
}
