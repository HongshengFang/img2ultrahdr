import SwiftUI
import AppKit
import UniformTypeIdentifiers

final class AppDelegate: NSObject, NSApplicationDelegate {
    var editor: EditorModel?
    var pendingURLs: [URL] = []
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { MainActor.assumeIsolated { editor?.close() } }
    func application(_ application: NSApplication, open urls: [URL]) { MainActor.assumeIsolated { if let editor { editor.drop(urls) } else { pendingURLs = urls } } }
}

#if !EDITOR_MODEL_CHECK
@MainActor final class EditorSession:ObservableObject { let model=EditorModel() }
@main
struct Img2UltraHDRApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    // EditorView observes the photo model. Observing it again at the Scene
    // level rebuilds the window and menu hierarchy on every slider sample.
    @StateObject private var session = EditorSession()
    private var model:EditorModel { session.model }
    @ObservedObject private var preferences = AppPreferences.shared
    var body: some Scene {
        Window(AppVersion.windowTitle, id: "editor") {
            EditorView(model: model)
                .onAppear { delegate.editor = model; if !delegate.pendingURLs.isEmpty { model.drop(delegate.pendingURLs); delegate.pendingURLs = [] } }
                .frame(minWidth:900, minHeight:620)
                .preferredColorScheme(preferences.scheme)
                .environment(\.locale,preferences.locale)
                .onAppear { preferences.applyAppearance();preferences.applyMenus();model.startPreviewBenchmark() }
        }
        .defaultSize(width:1200,height:800)
        .commands { EditorCommands(model:model) }
        Settings { PreferencesView() }
    }
}

struct EditorCommands:Commands {
    @ObservedObject var model:EditorModel
    @ObservedObject var preferences=AppPreferences.shared
    var body:some Commands {
            CommandGroup(replacing:.appSettings) { SettingsLink { Text(L("设置")+"…") }.keyboardShortcut(",") }
            CommandGroup(after:.toolbar) { Button(L("输出边界提示"), action:preferences.toggleClipping).keyboardShortcut("j",modifiers:[]) }
            CommandGroup(replacing:.newItem) { Button(L("打开 RAW…"), action:model.openPanel).keyboardShortcut("o") }
            CommandGroup(replacing:.help) { Button(L("Img2UltraHDR 使用说明"),action:model.help) }
            CommandGroup(replacing:.undoRedo) {
                Button(L("撤销"), action:model.undo).keyboardShortcut("z").disabled(model.undoStack.isEmpty || model.exporting)
                Button(L("重做"), action:model.redo).keyboardShortcut("z",modifiers:[.command,.shift]).disabled(model.redoStack.isEmpty || model.exporting)
            }
    }
}

#endif

struct EditorView: View {
    @ObservedObject var model: EditorModel
    @ObservedObject var preferences = AppPreferences.shared
    @Environment(\.openSettings) private var openSettings
    var body: some View {
        VStack(spacing:0) {
            HStack(spacing:10) {
                Button(action:model.openPanel) { Label(L("打开"),systemImage:"folder") }.disabled(model.exporting)
                Divider().frame(height:20)
                Picker(L("显示"),selection:$model.hdr) { Text(L("HDR")).tag(true); Text(L("SDR")).tag(false) }
                    .pickerStyle(.segmented).labelsHidden().frame(width:130)
                Button(L(model.showInitial ? "返回调整" : "初始效果"),action:model.compare).disabled(model.source == nil || model.busy)
                Spacer()
                Button { openSettings() } label: { Image(systemName:"gearshape") }.help(L("设置")).accessibilityLabel(L("设置"))
                Button(L("适应窗口"),action:model.fit)
                Button(L("100% 检查"),action:model.fullSize).disabled(model.source == nil || model.busy)
                Button { model.exportSheet = true } label: { Label(L("导出"),systemImage:"square.and.arrow.up") }
                    .buttonStyle(.borderedProminent).disabled(model.source == nil || model.exporting || !model.ready)
            }.padding(14)
            Divider()
            HStack(spacing:0) {
                ZStack {
                    Color(white:0.09)
                    if let value = model.displayedResult {
                        if model.accelerationFailed {
                            if let packet=PreviewPacket.decode(value["preview_packet"]) {
                                PhotoView(path:model.hdr ? packet.hdr:packet.sdr,packet:packet,hdr:model.hdr,nativeSize:model.nativeSize,viewReset:model.viewReset,status:$model.displayStatus,failure:$model.error)
                            } else if let path=model.imagePath {
                                PhotoView(path:path,hdr:model.hdr,nativeSize:model.nativeSize,viewReset:model.viewReset,status:$model.displayStatus,failure:$model.error)
                            }
                        } else {
                            InteractivePhotoView(result:value, recipe:model.showInitial ? (Recipe.decode(value["recipe"]) ?? model.recipe) : model.recipe,
                                hdr:model.hdr,nativeSize:model.nativeSize,viewReset:model.viewReset,clipping:preferences.clipping,readouts:model.readouts,inputTime:model.visualInputTime)
                        }
                    } else {
                        VStack(spacing:16) {
                            Image(systemName:"photo.badge.plus").font(.system(size:48,weight:.light)).foregroundStyle(.gray)
                            Text(model.source?.lastPathComponent ?? L("拖入 CR2 或 RAF")).font(.title2)
                            Text(L(model.source == nil ? "也可以打开文件 · 原片始终保留" : model.status)).foregroundStyle(.secondary)
                            if model.busy { ProgressView().controlSize(.small) }
                            else { Button(L("打开文件…"),action:model.openPanel) }
                        }.foregroundStyle(.white)
                    }
                    if model.showInitial {
                        VStack { HStack { Text(L("当前风格 · 初始效果")).font(.caption).padding(8).background(.black.opacity(0.7)).clipShape(RoundedRectangle(cornerRadius:6)); Spacer() }; Spacer() }.padding()
                    }
                    if model.targeted { RoundedRectangle(cornerRadius:8).stroke(Color.accentColor,lineWidth:3).padding(10).allowsHitTesting(false) }
                }
                .onDrop(of:[UTType.fileURL],isTargeted:$model.targeted) { providers in
                    guard providers.count == 1 else { model.error = "请只拖入一张 RAW 照片。"; return false }
                    providers[0].loadDataRepresentation(forTypeIdentifier:UTType.fileURL.identifier) { data,_ in
                        if let data, let url = URL(dataRepresentation:data,relativeTo:nil) {
                            DispatchQueue.main.async { model.open(url) }
                        }
                    }
                    return true
                }
                Divider()
                VStack(spacing:0) {
                    if model.accelerationFailed {
                        Text(L("实时加速不可用；精确预览仍可使用。统计和取色暂不可用。")).font(.caption).foregroundStyle(.secondary).padding(18)
                    } else { HistogramPanel(readouts:model.readouts).padding(18) }
                    Divider()
                    controls.disabled(model.source == nil || !model.ready || model.exporting || model.result == nil)
                }.frame(width:320)
            }
            Divider()
            VStack(alignment:.leading,spacing:5) {
                if let error = model.error {
                    HStack { Image(systemName:"exclamationmark.triangle").foregroundStyle(.orange); Text(L(error)).textSelection(.enabled); Spacer(); Button(L("使用说明"),action:model.help); Button(L("重试"),action:model.retry) }
                }
                if let detail = model.errorDetail { DisclosureGroup(L("技术详情")) { Text(detail).textSelection(.enabled) } }
                if let notice = model.notice { Text(L(notice)).foregroundStyle(.secondary) }
                HStack {
                    if model.busy { ProgressView().controlSize(.small) }
                    Text(L(model.status)).lineLimit(2)
                    Spacer()
                    Text(L(model.displayStatus)).foregroundStyle(.secondary)
                    Text(AppVersion.badge).foregroundStyle(.secondary).help(AppVersion.details)
                        .accessibilityLabel(AppVersion.details)
                    if model.busy { Button(L("取消"),action:model.cancel) }
                    else if (model.status.contains("取消") || !model.ready) && model.error == nil { Button(L("重试"),action:model.retry) }
                }
            }.font(.caption).padding(12)
        }
        .background(Color(nsColor:.windowBackgroundColor))
        .onReceive(model.readouts.$failure) { failure in
            if let failure { model.accelerationFailed=true;model.notice="实时预览已回退为精确更新";model.errorDetail=failure }
        }
        .onReceive(model.readouts.$displaySupportsHDR) { supported in
            model.displayStatus=supported ? "":"当前屏幕为 SDR 显示"
        }
        .sheet(isPresented:$model.exportSheet) {
            VStack(alignment:.leading,spacing:18) {
                Text(L("导出 Ultra HDR JPEG")).font(.title2)
                Text(L("使用原尺寸 RAW 底稿生成，包含普通屏幕可显示的 SDR 底图。")).foregroundStyle(.secondary)
                Toggle(L("额外保存一张 SDR JPEG"),isOn:$model.includeSDR)
                Toggle(L("移除拍摄元数据"),isOn:$model.stripMetadata)
                HStack { Spacer(); Button(L("取消")) { model.exportSheet=false }; Button(L("选择保存位置…")) { model.exportSheet=false; model.chooseExport() }.buttonStyle(.borderedProminent) }
            }.padding(24).frame(width:450)
        }
    }
    var controls: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:18) {
                Text(L("调整")).font(.headline)
                Picker(L("风格"),selection:$model.recipe.style) { Text(L("Clear")).tag("phone-clear"); Text(L("Natural")).tag("phone-natural") }
                    .pickerStyle(.segmented).onChange(of:model.recipe.style) { _,_ in model.commit() }
                Text(L(model.recipe.style == "phone-clear" ? "Clear V8 R5 · 明快 HDR" : "Natural V8 · 自然人像")).font(.caption).foregroundStyle(.secondary)
                Divider()
                adjustment("曝光",key:\.exposure_ev,range:-3...3,unit:" EV")
                adjustment("高光",key:\.highlight_ev,range:-2...2,unit:" EV")
                adjustment("阴影",key:\.shadow_ev,range:-2...2,unit:" EV")
                Divider()
                Picker(L("白平衡"),selection:$model.recipe.white_balance) { Text(L("自动")).tag("auto");Text(L("相机")).tag("camera");Text(L("自定义")).tag("custom") }
                    .onChange(of:model.recipe.white_balance) { _,_ in model.commit() }
                if model.recipe.white_balance == "custom" {
                    VStack(alignment:.leading,spacing:5) {
                        HStack { Text(L("色温"));Spacer();TextField("K",value:Binding(get:{model.recipe.temperature_k},set:{model.recipe.temperature_k=$0;model.scheduleEdit()}),format:.number.grouping(.never)).frame(width:70).multilineTextAlignment(.trailing);Text(L("K")) }
                        Slider(value:Binding(get:{Double(model.recipe.temperature_k)},set:{model.recipe.temperature_k=Int($0);model.sliderChanged()}),in:2000...15000,step:50,onEditingChanged:{ $0 ? model.beginDrag() : model.endDrag() }).accessibilityLabel(L("色温"))
                    }.disabled(!model.customPreviewReady)
                    adjustment("色调 · 绿 ↔ 洋红",key:\.tint,range:-100...100,multiplier:-1,flipSlider:true).disabled(!model.customPreviewReady)
                } else { Text(L(model.recipe.white_balance == "auto" ? "由 RAW 引擎自动判断光源" : "使用相机记录的白平衡")).font(.caption).foregroundStyle(.secondary) }
                Divider()
                adjustment("饱和度",key:\.saturation,range:0.8...1.2,multiplier:100,unit:"%")
                adjustment("HDR 强度",key:\.hdr_strength,range:0...1,multiplier:100,unit:"%")
                DisclosureGroup(L("更多")) { adjustment("SDR 亮度",key:\.sdr_exposure_ev,range:-2...2,unit:" EV").padding(.top,12) }
                Divider()
                HStack {
                    Button(action:model.undo) { Image(systemName:"arrow.uturn.backward") }.disabled(model.undoStack.isEmpty).help(L("撤销")).accessibilityLabel(L("撤销"))
                    Button(action:model.redo) { Image(systemName:"arrow.uturn.forward") }.disabled(model.redoStack.isEmpty).help(L("重做")).accessibilityLabel(L("重做"))
                    Spacer(); Button(L("重置调整"),action:model.reset)
                }
                Text(L("调整会自动保存。白平衡更新与全尺寸检查可能需要更长时间。")).font(.caption).foregroundStyle(.secondary)
            }.padding(18)
        }
    }
    func adjustment(_ title:String,key:WritableKeyPath<Recipe,Double>,range:ClosedRange<Double>,multiplier:Double=1,unit:String="",flipSlider:Bool=false) -> some View {
        VStack(alignment:.leading,spacing:5) {
            HStack {
                Text(L(title));Spacer()
                TextField(L(title),value:Binding(get:{model.recipe[keyPath:key]*multiplier},set:{model.recipe[keyPath:key]=$0/multiplier;model.scheduleEdit()}),format:.number.precision(.fractionLength(multiplier == 100 ? 0 : 2)))
                    .textFieldStyle(.plain).multilineTextAlignment(.trailing).frame(width:58)
                Text(unit).foregroundStyle(.secondary)
            }.font(.callout)
            Slider(value:Binding(get:{model.recipe[keyPath:key]*(flipSlider ? -1:1)},set:{model.recipe[keyPath:key]=$0*(flipSlider ? -1:1);model.sliderChanged()}),in:range,onEditingChanged:{ $0 ? model.beginDrag() : model.endDrag() }).accessibilityLabel(L(title))
        }
    }
}

struct PhotoView: NSViewRepresentable {
    let path:String
    var packet:PreviewPacket? = nil
    let hdr:Bool
    let nativeSize:Bool
    let viewReset:Int
    @Binding var status:String
    @Binding var failure:String?
    class Coordinator {
        var key = ""
        var canvas = HDRCanvas(frame:.zero)
        var generation = 0
        var native = false
        var viewReset = 0
        var centerOnLoad = false
        var image:DisplayImage?
        var observer:NSObjectProtocol?
        var timer:Timer?
        deinit { if let observer { NotificationCenter.default.removeObserver(observer) }; timer?.invalidate() }
        func layout(_ scroll:NSScrollView) {
            guard let image else { return }
            let bounds = scroll.contentView.bounds.size
            let scale = scroll.window?.backingScaleFactor ?? 2
            let size = native ? NSSize(width:max(bounds.width,CGFloat(image.pixels.width)/scale),height:max(bounds.height,CGFloat(image.pixels.height)/scale)) : scroll.contentView.frame.size
            if canvas.frame.size != size { canvas.frame.size = size }
            if native { canvas.layer?.contentsGravity = .center } else { canvas.layer?.contentsGravity = .resizeAspect }
        }
        func center(_ scroll:NSScrollView) {
            guard centerOnLoad else { return }
            let clip=scroll.contentView
            clip.scroll(to:NSPoint(x:max(0,(canvas.frame.width-clip.bounds.width)/2),y:max(0,(canvas.frame.height-clip.bounds.height)/2)))
            scroll.reflectScrolledClipView(clip);centerOnLoad=false
        }
    }
    func makeCoordinator() -> Coordinator { Coordinator() }
    func makeNSView(context:Context) -> NSScrollView {
        let scroll=NSScrollView();scroll.hasHorizontalScroller=true;scroll.hasVerticalScroller=true
        scroll.drawsBackground=true;scroll.backgroundColor=NSColor(calibratedWhite:0.09,alpha:1)
        scroll.allowsMagnification=true;scroll.minMagnification=0.25;scroll.maxMagnification=4
        scroll.documentView=context.coordinator.canvas
        scroll.contentView.postsBoundsChangedNotifications=true
        context.coordinator.observer=NotificationCenter.default.addObserver(forName:NSView.boundsDidChangeNotification,object:scroll.contentView,queue:.main) { [weak coordinator=context.coordinator,weak scroll] _ in if let scroll { coordinator?.layout(scroll) } }
        context.coordinator.timer=Timer.scheduledTimer(withTimeInterval:1,repeats:true) { [weak coordinator=context.coordinator,weak scroll] _ in
            if let scroll, let coordinator { updateStatus(scroll,coordinator.image) }
        }
        return scroll
    }
    func updateNSView(_ scroll:NSScrollView,context:Context) {
        let c=context.coordinator
        if c.native != nativeSize || c.viewReset != viewReset { scroll.magnification=1;c.viewReset=viewReset;c.centerOnLoad=true }
        c.native=nativeSize;c.canvas.nativeSize=nativeSize;c.layout(scroll)
        let key=HDRImageLoader.cacheKey(path:path,hdr:hdr)
        guard c.key != key else { c.center(scroll);updateStatus(scroll,c.image);return }
        c.key=key;c.generation+=1;let generation=c.generation
        DispatchQueue.global(qos:.userInitiated).async {
            do {
                let image=try packet.map { try HDRImageLoader.shared.load(packet:$0,hdr:hdr) } ?? HDRImageLoader.shared.load(path:path,hdr:hdr)
                DispatchQueue.main.async {
                    guard c.generation == generation else { return }
                    c.image=image;c.canvas.displayImage=image;c.layout(scroll);c.center(scroll);updateStatus(scroll,image)
                }
            } catch { DispatchQueue.main.async { if c.generation == generation { failure=error.localizedDescription } } }
        }
    }
    func updateStatus(_ scroll:NSScrollView,_ image:DisplayImage?) {
        guard let image else { return }
        let headroom=scroll.window?.screen?.maximumExtendedDynamicRangeColorComponentValue ?? 1
        let label = image.hdr && image.headroom>1 ? (headroom>1 ? "HDR · \(String(format:"%.1f",headroom))× 显示余量" : "当前屏幕为 SDR 显示") : "SDR"
        if status != label { DispatchQueue.main.async { status=label } }
    }
}
