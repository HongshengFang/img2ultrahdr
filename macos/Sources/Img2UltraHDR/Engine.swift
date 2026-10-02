import Foundation
import AppKit
import QuartzCore
import Darwin

struct Recipe: Codable, Equatable {
    var schema_version = 2
    var style = "phone-clear"
    var white_balance = "auto"
    var temperature_k = 5600
    var tint = 0.0
    var exposure_ev = 0.0
    var highlight_ev = 0.0
    var shadow_ev = 0.0
    var white_ev = 0.0
    var black_ev = 0.0
    var saturation = 1.0
    var hdr_strength = 1.0
    var sdr_exposure_ev = 0.0
    var local_adjustments: [LocalAdjustment] = []
    init() {}
    enum CodingKeys: String, CodingKey {
        case schema_version, style, white_balance, temperature_k, tint, exposure_ev, highlight_ev, shadow_ev
        case white_ev, black_ev, saturation, hdr_strength, sdr_exposure_ev
        case local_adjustments
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let version = try c.decodeIfPresent(Int.self, forKey:.schema_version) ?? 1
        guard version == 1 || version == 2 else { throw DecodingError.dataCorruptedError(forKey:.schema_version,in:c,debugDescription:"Unsupported editing version") }
        schema_version = 2
        style = try c.decodeIfPresent(String.self, forKey:.style) ?? "phone-clear"
        white_balance = try c.decodeIfPresent(String.self, forKey:.white_balance) ?? "auto"
        guard ["phone-clear","phone-natural"].contains(style),["auto","camera","custom"].contains(white_balance) else {
            throw DecodingError.dataCorruptedError(forKey:.style,in:c,debugDescription:"Invalid style or white balance")
        }
        temperature_k = try c.decodeIfPresent(Int.self, forKey:.temperature_k) ?? 5600
        tint = try c.decodeIfPresent(Double.self, forKey:.tint) ?? 0
        exposure_ev = try c.decodeIfPresent(Double.self, forKey:.exposure_ev) ?? 0
        highlight_ev = try c.decodeIfPresent(Double.self, forKey:.highlight_ev) ?? 0
        shadow_ev = try c.decodeIfPresent(Double.self, forKey:.shadow_ev) ?? 0
        white_ev = try c.decodeIfPresent(Double.self, forKey:.white_ev) ?? 0
        black_ev = try c.decodeIfPresent(Double.self, forKey:.black_ev) ?? 0
        saturation = try c.decodeIfPresent(Double.self, forKey:.saturation) ?? 1
        hdr_strength = try c.decodeIfPresent(Double.self, forKey:.hdr_strength) ?? 1
        sdr_exposure_ev = try c.decodeIfPresent(Double.self, forKey:.sdr_exposure_ev) ?? 0
        local_adjustments = try c.decodeIfPresent([LocalAdjustment].self,forKey:.local_adjustments) ?? []
        guard local_adjustments.count <= 8,Set(local_adjustments.map(\.id)).count == local_adjustments.count else {
            throw DecodingError.dataCorruptedError(forKey:.local_adjustments,in:c,debugDescription:"Invalid local regions")
        }
    }
    var previewKey: String {
        var value = self
        if white_balance != "custom" { value.temperature_k = 5600; value.tint = 0 }
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return String(data: (try? encoder.encode(value)) ?? Data(), encoding:.utf8) ?? ""
    }
    var dictionary: [String: Any] { (try? JSONSerialization.jsonObject(with: JSONEncoder().encode(self))) as? [String: Any] ?? [:] }
    static func decode(_ value: Any?) -> Recipe? {
        guard let value, let data = try? JSONSerialization.data(withJSONObject: value) else { return nil }
        return try? JSONDecoder().decode(Recipe.self, from: data)
    }
    func normalized(fallback: Recipe = Recipe()) -> Recipe {
        var value = self
        value.temperature_k = min(15000, max(2000, value.temperature_k))
        for (key, low, high) in [(\Recipe.exposure_ev, -3.0, 3.0), (\Recipe.highlight_ev, -2, 2),
                                (\Recipe.shadow_ev, -2, 2), (\Recipe.tint, -100, 100),
                                (\Recipe.white_ev, -2, 2), (\Recipe.black_ev, -2, 2),
                                (\Recipe.saturation, 0.8, 1.2), (\Recipe.hdr_strength, 0, 1),
                                (\Recipe.sdr_exposure_ev, -2, 2)] {
            let number = value[keyPath:key]
            value[keyPath:key] = number.isFinite ? min(high, max(low, number)) : fallback[keyPath:key]
        }
        return value
    }
}

final class EngineBridge {
    var process: Process?
    private var input: FileHandle?
    private var log: FileHandle?
    var onEvent: (([String: Any]) -> Void)?
    var onExit: (() -> Void)?

    func start() throws {
        guard let configURL = Bundle.main.url(forResource: "engine", withExtension: "json"),
              let config = try JSONSerialization.jsonObject(with: Data(contentsOf: configURL)) as? [String: Any],
              let pythonPath = config["python"] as? String, let rootPath = config["root"] as? String else {
            throw NSError(domain: "App", code: 1, userInfo: [NSLocalizedDescriptionKey: "缺少本机运行配置，请重新构建应用。"])
        }
        // New builds keep their engine snapshot in Resources. Absolute paths
        // remain supported for older locally configured bundles.
        let root=Self.enginePath(rootPath,resources:configURL.deletingLastPathComponent()).path
        let python=Self.enginePath(pythonPath,resources:configURL.deletingLastPathComponent()).path
        guard FileManager.default.isExecutableFile(atPath: python), FileManager.default.fileExists(atPath: root + "/src/hdrimg") else {
            throw NSError(domain: "App", code: 2, userInfo: [NSLocalizedDescriptionKey: "本机图像引擎已移动或不可用。请在原项目位置重新构建应用。"])
        }
        let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Img2UltraHDR/engine.log")
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        log = try FileHandle(forWritingTo: logURL); try log?.seekToEnd()
        let p = Process(), stdin = Pipe(), stdout = Pipe()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "hdrimg.worker"]
        p.currentDirectoryURL = URL(fileURLWithPath: root)
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPATH"] = root + "/src"
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONNOUSERSITE"] = "1"
        // Python must not create bytecode files inside the signed app bundle.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["HDRIMG_VISION_FAST_TILES"] = "1"
        for (key, value) in config["tools"] as? [String: String] ?? [:] { environment[key] = value }
        if let helper = Bundle.main.url(forResource: "phone-subject", withExtension: nil) { environment["HDRIMG_VISION_HELPER"] = helper.path }
        if let helper = Bundle.main.url(forResource:"local-selection",withExtension:nil) { environment["HDRIMG_LOCAL_SELECTION_HELPER"] = helper.path }
        if let accelerator = Bundle.main.url(forResource: "libhdreditor", withExtension: "dylib") { environment["HDRIMG_ACCELERATOR"] = accelerator.path }
        p.environment = environment
        p.standardInput = stdin; p.standardOutput = stdout; p.standardError = log
        p.terminationHandler = { [weak self] terminated in DispatchQueue.main.async {
            guard let self, self.process === terminated else { return }
            self.onExit?()
        } }
        try p.run(); process = p; input = stdin.fileHandleForWriting
        DispatchQueue.global(qos: .utility).async { [weak self] in
            var buffer = Data()
            while true {
                let next = stdout.fileHandleForReading.availableData
                if next.isEmpty { break }
                buffer.append(next)
                while let range = buffer.range(of: Data([10])) {
                    let line = buffer.subdata(in: buffer.startIndex..<range.lowerBound)
                    buffer.removeSubrange(buffer.startIndex...range.lowerBound)
                    if let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                        DispatchQueue.main.async {
                            guard let self, self.process === p else { return }
                            self.onEvent?(event)
                        }
                    }
                }
            }
        }
    }
    static func enginePath(_ value:String,resources:URL)->URL {
        value.hasPrefix("/") ? URL(fileURLWithPath:value):resources.appendingPathComponent(value)
    }
    func send(_ value: [String: Any]) {
        let owner = process
        guard let data = try? JSONSerialization.data(withJSONObject: value) else { return }
        do { try input?.write(contentsOf: data + Data([10])) }
        catch { DispatchQueue.main.async { if self.process === owner { self.onExit?() } } }
    }
    func close() { send(["command": "close"]); try? input?.close(); input = nil }
    func stopStartup() {
        let old=process;close();process=nil
        if let old,old.isRunning {
            old.terminate()
            // A process waiting for a macOS file-access decision may not
            // handle SIGTERM. Retain this exact Process until it is reaped.
            DispatchQueue.main.asyncAfter(deadline:.now()+1) {
                if old.isRunning { Darwin.kill(old.processIdentifier,SIGKILL) }
            }
        }
    }
}

@MainActor
final class EditorModel: ObservableObject {
    @Published var targeted = false
    @Published var recipe = Recipe() { didSet { visualInputTime=CACurrentMediaTime() } }
    var visualInputTime=CACurrentMediaTime()
    @Published var source: URL?
    @Published var result: [String: Any]? { didSet { resultPacket=PreviewPacket.decode(result?["preview_packet"]) } }
    private(set) var resultPacket:PreviewPacket?
    @Published var comparisonResult: [String: Any]?
    @Published var previewResult: [String: Any]?
    @Published var errorDetail: String?
    @Published var accelerationFailed = false
    let readouts = PhotoReadouts()
    var floatPreview = true
    var floatPreviewV2 = true
    @Published var localEditing = false
    @Published var invalidLocalIDs:Set<String> = []
    @Published var addingLocal = false
    @Published var selectedLocalID: String?
    @Published var pendingLocal: LocalAdjustment?
    @Published var localBypass = false { didSet { visualInputTime=CACurrentMediaTime() } }
    var selectionTimeout:DispatchWorkItem?
    @Published var showInitial = false
    @Published var hdr = true { didSet { visualInputTime=CACurrentMediaTime() } }
    @Published var nativeSize = false
    @Published var viewReset = 0
    @Published var status = "正在启动本机图像后台…"
    @Published var displayStatus = ""
    @Published var error: String?
    @Published var notice: String?
    @Published var busy = true
    @Published var exporting = false
    @Published var ready = false
    @Published var includeSDR = false
    @Published var stripMetadata = false
    @Published var exportSheet = false
    @Published var undoStack: [Recipe] = []
    @Published var redoStack: [Recipe] = []
    var engine = EngineBridge()
    var session = UUID().uuidString
    var revision = 0
    var expectedSHA: String?
    var activeID = ""
    var pending: DispatchWorkItem?
    var committed = Recipe()
    var beforeDrag: Recipe?
    var cachedPreviews: [String: [String: Any]] = [:]
    var previewCacheOrder: [String] = []
    var lastCommand = "hello"
    var startupNotice:DispatchWorkItem?
    var startupTimeout:DispatchWorkItem?
    var startupProgressReceived=false
    var comparing = false
    var deferredOpen: URL?
    var closing = false
    var needsRelocation = false
    var smokeStep = 0
    var smokeEvents: [String] = []
    var benchmarkStarted = false
    var diagnosticRoot: URL? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--diagnostics"), args.count > i+1 else { return nil }
        return URL(fileURLWithPath: args[i+1])
    }
    var smokeSource: URL? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--smoke-raw"), args.count > i+1 else { return nil }
        return URL(fileURLWithPath: args[i+1])
    }
    private let stateURL: URL

    init(launchImmediately: Bool = true, stateURL: URL? = nil) {
        let arguments=ProcessInfo.processInfo.arguments
        let diagnostic=arguments.firstIndex(of:"--diagnostics").flatMap { arguments.count>$0+1 ? URL(fileURLWithPath:arguments[$0+1]):nil }
        self.stateURL = stateURL ?? diagnostic?.appendingPathComponent("support/window-session.json") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Img2UltraHDR/window-session.json")
        engine.onEvent = { [weak self] event in self?.receive(event) }
        engine.onExit = { [weak self] in
            guard let self, !self.closing else { return }
            self.clearStartupWatchdog()
            self.busy = false; self.exporting = false; self.ready = false
            self.error = "图像后台意外结束。调整已保留，请点击重试。"
            self.status = "后台已停止"
            self.diagnostic(event: "backend-exited")
        }
        if launchImmediately { launch() }
    }
    func launch() {
        if loadPreviewBenchmark() { startPreviewBenchmark();return }
        do {
            try engine.start(); request("hello")
        }
        catch { clearStartupWatchdog();self.error = error.localizedDescription; busy = false }
    }
    func clearStartupWatchdog() {
        startupNotice?.cancel();startupNotice=nil
        startupTimeout?.cancel();startupTimeout=nil
    }
    func watchStartup() {
        clearStartupWatchdog();startupProgressReceived=false
        let identity=activeID
        let reminder=DispatchWorkItem { [weak self] in
            guard let self,self.activeID==identity,!self.ready,self.busy,self.lastCommand=="hello" else { return }
            if !self.startupProgressReceived {
                self.notice="图像后台尚未启动。请取消后重试；若仍失败，请查看使用说明和后台日志。"
            }
        }
        let timeout=DispatchWorkItem { [weak self] in self?.startupExpired(identity) }
        startupNotice=reminder;startupTimeout=timeout
        DispatchQueue.main.asyncAfter(deadline:.now()+10,execute:reminder)
        DispatchQueue.main.asyncAfter(deadline:.now()+60,execute:timeout)
    }
    func startupExpired(_ identity:String) {
        guard activeID==identity,!ready,busy,lastCommand=="hello" else { return }
        clearStartupWatchdog();activeID=UUID().uuidString
        engine.stopStartup();busy=false;exporting=false;notice=nil
        if startupProgressReceived {
            status="工具检查超时"
            error="本机图像工具检查超过 60 秒，请查看日志或点击重试。"
        } else {
            status="图像后台启动超时"
            error="图像后台未能在 60 秒内启动，请查看后台日志或点击重试。"
        }
        diagnostic(event:"startup-timeout")
    }
    var imagePath: String? { displayedResult?[hdr ? "ultrahdr" : "sdr"] as? String }
    var hasImage: Bool { imagePath != nil }
    var displayedResult: [String:Any]? {
        guard var frame=showInitial ? comparisonResult:result else { return nil }
        if let exported=frame["exported"] as? String {
            // Both modes must decode the delivered file, including its actual
            // encoded SDR base, not the separate pre-encoding SDR JPEG.
            frame["ultrahdr"]=exported;frame["sdr"]=exported
        }
        if localBypass,accelerationFailed,var packet=frame["preview_packet"] as? [String:Any] {
            packet["sdr"]=packet["base_sdr"] ?? packet["sdr"];packet["hdr"]=packet["base_hdr"] ?? packet["hdr"]
            frame["preview_packet"]=packet
        }
        return frame
    }
    var customPreviewReady: Bool { resultPacket?.anchor_recipe.white_balance == "custom" }
    private var leaseURL:URL {
        let cache=diagnosticRoot?.appendingPathComponent("cache") ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Caches/Img2UltraHDR")
        return cache.appendingPathComponent("leases/\(ProcessInfo.processInfo.processIdentifier).json")
    }
    func leaseFrames() {
        let values = [result,previewResult,comparisonResult].compactMap { $0 } + Array(cachedPreviews.values)
        var paths=Set<String>()
        for value in values {
            if let packet=PreviewPacket.decode(value["preview_packet"]) { paths.insert(URL(fileURLWithPath:packet.hdr).deletingLastPathComponent().path) }
            for key in ["sdr","ultrahdr"] { if let path=value[key] as? String { paths.insert(URL(fileURLWithPath:path).deletingLastPathComponent().path) } }
        }
        try? FileManager.default.createDirectory(at:leaseURL.deletingLastPathComponent(),withIntermediateDirectories:true)
        if let data=try? JSONSerialization.data(withJSONObject:["pid":ProcessInfo.processInfo.processIdentifier,"paths":Array(paths)]) { try? data.write(to:leaseURL,options:.atomic) }
    }
    func request(_ command: String, extras: [String: Any] = [:]) {
        if command=="export",pendingLocal != nil { error="请先完成局部选择。";return }
        if command != "select_region",pendingLocal != nil {
            selectionTimeout?.cancel();selectionTimeout=nil;pendingLocal=nil
        }
        comparing = command == "preview" && extras["comparison"] as? Bool == true
        revision += 1; activeID = UUID().uuidString; lastCommand = command
        var message: [String: Any] = ["command": command, "id": activeID, "session_id": session, "revision": revision]
        if let root=diagnosticRoot { message["cache"]=root.appendingPathComponent("cache").path;message["support"]=root.appendingPathComponent("support").path }
        if floatPreview { message["preview_format"] = floatPreviewV2 ? "float_v2":"float_v1" }
        if let source { message["source"] = source.path; message["recipe"] = recipe.dictionary }
        if let expectedSHA { message["expected_sha"] = expectedSHA }
        for (k,v) in extras { message[k] = v }
        busy = true; error = nil
        if command == "hello" {
            ready=false;notice=nil;errorDetail=nil;status="正在启动本机图像后台…";watchStartup()
        } else { status = command == "export" ? "正在导出…" : "正在更新…" }
        engine.send(message)
    }
    func receive(_ event: [String: Any]) {
        guard event["id"] as? String == activeID else { return }
        if lastCommand=="hello" {
            if event["event"] as? String == "progress" { startupProgressReceived=true;notice=nil }
            if ["result","error","cancelled"].contains(event["event"] as? String ?? "") { clearStartupWatchdog() }
        }
        defer { diagnostic(event: event["event"] as? String ?? "unknown") }
        switch event["event"] as? String {
        case "progress":
            let stages=["develop":"正在显影 RAW","analyze":"正在分析照片与建立预览缓存","refine":"正在精确更新","encode":"正在编码 Ultra HDR","validate":"正在检查导出图像","dependencies":"正在检查本机图像工具…","full_render":"正在生成全尺寸图像","color":"正在准备色彩信息","metadata":"正在处理拍摄信息"]
            status = stages[event["phase_code"] as? String ?? ""] ?? event["phase"] as? String ?? "正在处理…"
        case "opened":
            if let info = event["source"] as? [String: Any] { expectedSHA = info["sha256"] as? String }
            needsRelocation = false
            if lastCommand == "prepare", let restored = Recipe.decode(event["recipe"]) {
                recipe = restored; committed = restored
            }
            if event["engine_changed"] as? Bool == true { notice = "图像引擎已更新，正在按保存的调整重新生成。" }
            persist()
        case "result":
            busy = false; exporting = false
            if lastCommand == "hello" {
                floatPreview = (event["protocol"] as? Int ?? 1) >= 2 && (event["capabilities"] as? [String] ?? []).contains("float_preview_v1")
                floatPreviewV2 = (event["capabilities"] as? [String] ?? []).contains("float_preview_v2")
                ready = true; notice = nil; status = "拖入 RAW 开始编辑"
                if let url = smokeSource { open(url) } else if let url = deferredOpen { deferredOpen = nil; open(url) } else if source != nil { request("preview") } else { restoreWindow() }
                return
            }
            guard let value = event["result"] as? [String: Any] else { return }
            if lastCommand == "select_region" {
                selectionTimeout?.cancel();selectionTimeout=nil
                if var region=pendingLocal {
                    if value["mode"] as? String == "smart",let ref=value["mask_ref"] as? String {
                        region.mode="smart";region.mask_ref=ref
                    } else { region.mode="soft" }
                    pendingLocal=nil;finishLocalSelection(region)
                    notice=region.mode == "smart" ? nil:"已使用柔和范围，可调整位置、大小和形状。"
                }
                return
            }
            if comparing { comparisonResult = value; showInitial = true; comparing = false }
            else {
                result = value
                if value["full"] as? Bool != true {
                    previewResult = value
                    if let rendered = Recipe.decode(value["recipe"]) {
                        let key = rendered.previewKey
                        cachedPreviews[key] = value
                        previewCacheOrder.removeAll { $0 == key }; previewCacheOrder.append(key)
                        while previewCacheOrder.count > 8 { cachedPreviews.removeValue(forKey:previewCacheOrder.removeFirst()) }
                    }
                }
            }
            leaseFrames()
            if let packet=PreviewPacket.decode(value["preview_packet"]),packet.version==2,
               let rendered=Recipe.decode(value["recipe"]) {
                invalidLocalIDs=Set(rendered.local_adjustments.filter { $0.mode=="smart" && packet.local_masks?[$0.id]==nil }.map(\.id))
                if invalidLocalIDs.isEmpty,notice=="已保存的选区不可用，请重新选择、关闭或删除该区域。" { notice=nil }
            }
            if lastCommand == "full_render" { nativeSize = true; viewReset += 1 }
            status = value["full"] as? Bool == true ? "全尺寸图像已就绪" : "精确预览"
            if let path = value["exported"] as? String {
                status = "已导出：" + URL(fileURLWithPath: path).lastPathComponent
                result?["ultrahdr"] = path
                if diagnosticRoot == nil { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
            }
            if let warning = value["warning"] as? String { notice = warning }
            let selected = hdr ? "ultrahdr" : "sdr"
            let alternate = hdr ? "sdr" : "ultrahdr"
            let currentHDR = hdr
            let displayed = displayedResult
            if accelerationFailed,let currentPath = displayed?[selected] as? String, let alternatePath = displayed?[alternate] as? String {
                DispatchQueue.global(qos:.utility).async {
                    _ = try? HDRImageLoader.shared.load(path:currentPath,hdr:currentHDR)
                    _ = try? HDRImageLoader.shared.load(path:alternatePath,hdr:!currentHDR)
                }
            }
            persist()
            advanceSmoke()
        case "cancelled": busy = false; exporting = false; comparing = false; pendingLocal=nil; status = lastCommand == "preview" ? "精确更新已取消 · 调整已保留":"已取消 · 调整已保留"; advanceSmoke()
        case "error":
            busy = false; exporting = false; comparing = false
            if lastCommand == "select_region",pendingLocal != nil { useSoftSelection();return }
            errorDetail = event["message"] as? String
            if event["error_code"] as? String == "local_asset_missing",let region=event["region_id"] as? String {
                invalidLocalIDs.insert(region);selectedLocalID=region;localEditing=true;addingLocal=false
                let message="已保存的选区不可用，请重新选择、关闭或删除该区域。"
                error=message;status="处理未完成"
                if result==nil {
                    // Recover a canvas without changing the saved recipe. Every
                    // unavailable region stays marked and cannot be exported.
                    var recovery=recipe
                    for i in recovery.local_adjustments.indices where invalidLocalIDs.contains(recovery.local_adjustments[i].id) {
                        recovery.local_adjustments[i].enabled=false
                    }
                    request("preview",extras:["recipe":recovery.dictionary,"comparison":true]);comparing=false;notice=message
                }
                return
            }
            let errors=["missing_dependency":"图像工具缺失或检查失败，请参照使用说明修复。","disk_full":"磁盘空间不足，请释放空间后重试。","permission_denied":"没有读写权限，请检查文件和保存位置。","invalid_input":"原片或参数无效，请检查原片或重新打开。","cancel_failed":"旧任务暂未结束，后台仍在运行。请稍后点击重试。"]
            error = errors[event["error_code"] as? String ?? ""] ?? "处理失败，请查看详情或重试。"; status = "处理未完成"
        default: break
        }
    }
    func help() { if let url=Bundle.main.url(forResource:AppPreferences.shared.language == "en" ? "help-en":"help",withExtension:"html") { NSWorkspace.shared.open(url) } }
    func openPanel() {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false; panel.message = L(needsRelocation ? "重新定位保存的原始 RAW" : "选择 CR2 或 RAF 原片")
        if panel.runModal() == .OK, let url = panel.url { open(url, relocating: needsRelocation) }
    }
    func open(_ url: URL, relocating: Bool = false) {
        guard ["cr2", "raf"].contains(url.pathExtension.lowercased()) else { error = "第一版支持 Canon CR2 和 Fujifilm RAF。"; return }
        guard !exporting else { error = "请先完成或取消当前导出。"; return }
        if !ready { deferredOpen = url; return }
        pending?.cancel(); beforeDrag=nil; persist()
        selectionTimeout?.cancel();selectionTimeout=nil
        let oldSHA = expectedSHA
        source = url; expectedSHA = relocating ? oldSHA : nil; needsRelocation = relocating
        session = UUID().uuidString
        if !relocating { recipe = Recipe() }
        committed = recipe
        undoStack = []; redoStack = []; result = nil; previewResult = nil; comparisonResult = nil; showInitial = false; nativeSize = false
        localEditing=false;addingLocal=false;pendingLocal=nil;selectedLocalID=nil;localBypass=false;invalidLocalIDs=[]
        readouts.photoEpoch+=1
        cachedPreviews.removeAll(); previewCacheOrder.removeAll()
        readouts.pixel=nil;readouts.bins=[UInt32](repeating:0,count:1024);readouts.failure=nil
        readouts.frameID="";readouts.histogramFrameID="";readouts.kind="preview";readouts.state="等待精确结果";leaseFrames()
        // Omit recipe so an existing saved edit is restored by source fingerprint.
        request("prepare", extras: ["recipe": NSNull()])
    }
    func drop(_ urls: [URL]) {
        guard urls.count == 1 else { error = "首版一次编辑一张照片，请只拖入一个 RAW 文件。"; return }
        open(urls[0])
    }
    func beginDrag() {
        pending?.cancel();beforeDrag=committed;interactiveChanged()
    }
    func interactiveChanged() {
        guard !exporting else { return }
        let bounded=recipe.normalized(fallback:committed)
        if bounded != recipe { recipe=bounded }
        comparing=false
        if pendingLocal != nil { cancelLocalSelection() }
        if busy {
            activeID=UUID().uuidString
            engine.send(["command":"cancel","id":UUID().uuidString,"session_id":session,"revision":revision])
            busy=false
        }
        showInitial=false
        if nativeSize || result?["full"] as? Bool == true { nativeSize=false;result=previewResult;viewReset+=1 }
        status=accelerationFailed ? "等待精确结果":"实时预览";error=nil;errorDetail=nil
    }
    func endDrag() {
        let unchanged=recipe==committed
        commit(before:beforeDrag ?? committed);beforeDrag=nil
        if unchanged,let anchor=resultPacket?.anchor_recipe,anchor != recipe,!busy { request("preview") }
    }
    func scheduleEdit() {
        interactiveChanged()
        pending?.cancel()
        let action = DispatchWorkItem { [weak self] in self?.commit() }
        pending = action; DispatchQueue.main.asyncAfter(deadline: .now()+0.25, execute: action)
    }
    func sliderChanged() { interactiveChanged();if beforeDrag == nil { scheduleEdit() } }
    func reusePreview() {
        guard let value = cachedPreviews[recipe.previewKey], let packet = PreviewPacket.decode(value["preview_packet"]),
              [packet.scene, packet.sdr, packet.hdr].allSatisfy({ FileManager.default.fileExists(atPath:$0) }) else { return }
        result = value; previewResult = value; leaseFrames()
    }
    func scheduleWhiteBalance() {
        guard ready, !exporting else { return }
        interactiveChanged(); reusePreview(); status = "正在准备白平衡…"
        pending?.cancel()
        let action = DispatchWorkItem { [weak self] in
            guard let self else { return }
            if self.recipe != self.committed { self.commit() }
            else if self.resultPacket?.anchor_recipe.previewKey != self.recipe.previewKey { self.request("preview") }
            else { self.status = "精确预览" }
        }
        pending = action; DispatchQueue.main.asyncAfter(deadline:.now()+0.25, execute:action)
    }
    func resetAdjustment(_ key: WritableKeyPath<Recipe,Double>) {
        guard ready, !exporting else { return }
        recipe[keyPath:key] = Recipe()[keyPath:key]; commit()
    }
    func commit(before: Recipe? = nil) {
        pending?.cancel()
        if pendingLocal != nil { cancelLocalSelection() }
        recipe = recipe.normalized(fallback: committed)
        guard source != nil, ready, !exporting, recipe != committed else { return }
        undoStack.append(before ?? committed); if undoStack.count > 100 { undoStack.removeFirst() }
        redoStack = []; committed = recipe; showInitial = false; nativeSize = false; comparisonResult = nil
        reusePreview(); persist(); leaseFrames(); request("preview")
    }
    func undo() {
        guard ready,!exporting,let previous = undoStack.popLast() else { return }
        redoStack.append(recipe); recipe = previous; committed = previous; afterHistory()
    }
    func redo() {
        guard ready,!exporting,let next = redoStack.popLast() else { return }
        undoStack.append(recipe); recipe = next; committed = next; afterHistory()
    }
    func afterHistory() { if pendingLocal != nil { cancelLocalSelection() };pending?.cancel();beforeDrag=nil; showInitial = false; nativeSize = false; comparisonResult = nil; if let previewResult { result=previewResult }; persist(); request("preview") }
    func reset() { guard ready,!exporting else { return };let style = recipe.style; recipe = Recipe(); recipe.style = style; commit() }
    func compare() {
        guard ready,!exporting,source != nil else { return }
        if showInitial { showInitial = false; return }
        fit()
        if comparisonResult != nil { showInitial = true; return }
        var initial = Recipe(); initial.style = recipe.style
        request("preview", extras: ["recipe": initial.dictionary, "comparison": true])
    }
    func fullSize() { guard ready,!exporting,source != nil else { return };localEditing=false;localBypass=false; showInitial = false; comparing = false; request("full_render") }
    func fit() { nativeSize = false; viewReset += 1 }
    func cancel() {
        selectionTimeout?.cancel();selectionTimeout=nil;pendingLocal=nil
        pending?.cancel(); revision += 1; activeID = UUID().uuidString
        if !ready {
            clearStartupWatchdog();notice=nil
            engine.stopStartup();busy=false;exporting=false;status="工具检查已取消，可点击重试继续";return
        }
        engine.send(["command":"cancel", "id":activeID, "session_id":session, "revision":revision])
        status = "正在取消…"; persist()
    }
    func retry() { guard !exporting else { return };if !ready { if engine.process?.isRunning == true { request("hello") } else { launch() } } else if source != nil { request("preview") } }
    func chooseExport() {
        guard let source else { return }
        let panel = NSSavePanel(); panel.nameFieldStringValue = source.deletingPathExtension().lastPathComponent + "_ultrahdr.jpg"
        panel.allowedContentTypes = [.jpeg]; panel.canCreateDirectories = true
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        if includeSDR {
            let stem = destination.deletingPathExtension().lastPathComponent
            let base = stem.hasSuffix("_ultrahdr") ? String(stem.dropLast(9)) : stem
            let companion = destination.deletingLastPathComponent().appendingPathComponent(base + "_sdr.jpg")
            if FileManager.default.fileExists(atPath: companion.path) {
                let alert = NSAlert(); alert.messageText = L("替换已有的 SDR 文件？"); alert.informativeText = companion.lastPathComponent
                alert.addButton(withTitle: L("替换")); alert.addButton(withTitle: L("取消"))
                guard alert.runModal() == .alertFirstButtonReturn else { return }
            }
        }
        pending?.cancel(); showInitial = false; comparing = false; exporting = true
        request("export", extras: ["destination":destination.path, "include_sdr":includeSDR,
                                    "strip_metadata":stripMetadata, "overwrite":true])
    }
    func persist() {
        guard let source else { return }
        let record: [String: Any] = ["source":source.path, "sha256":expectedSHA ?? "", "recipe":recipe.normalized(fallback: committed).dictionary, "updated":Date().timeIntervalSince1970]
        do {
            try FileManager.default.createDirectory(at: stateURL.deletingLastPathComponent(), withIntermediateDirectories:true)
            let data = try JSONSerialization.data(withJSONObject: record)
            try data.write(to:stateURL, options:.atomic)
            if let sha = expectedSHA, !sha.isEmpty {
                let drafts = stateURL.deletingLastPathComponent().appendingPathComponent("drafts")
                try FileManager.default.createDirectory(at:drafts, withIntermediateDirectories:true)
                try data.write(to:drafts.appendingPathComponent(sha + ".json"), options:.atomic)
            }
        } catch { notice = "调整暂时无法保存：" + error.localizedDescription }
    }
    func restoreWindow() {
        guard let data = try? Data(contentsOf:stateURL), let saved = try? JSONSerialization.jsonObject(with:data) as? [String:Any],
              let path = saved["source"] as? String else { return }
        source = URL(fileURLWithPath:path); expectedSHA = saved["sha256"] as? String
        if expectedSHA == "" { expectedSHA = nil }
        let restored=Recipe.decode(saved["recipe"])
        recipe = (restored ?? Recipe()).normalized(); committed = recipe
        guard FileManager.default.fileExists(atPath:path) else {
            needsRelocation = true; status = "原片已移动，请点击打开重新定位"; return
        }
        if restored == nil { request("prepare",extras:["recipe":NSNull()]) }
        else { request("preview") }
    }
    func diagnostic(event: String) {
        guard let root = diagnosticRoot else { return }
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories:true)
        let report: [String:Any] = ["event":event,"ready":ready,"busy":busy,"status":status,
            "display_status":displayStatus,"source":source?.path ?? "","recipe":recipe.dictionary,
            "error":error ?? "","steps":smokeEvents,"complete":smokeStep==7,
            "edit_menu":NSApp?.mainMenu?.items.first(where:{["Edit","编辑"].contains($0.title)})?.submenu?.items.filter({!$0.isSeparatorItem}).map({$0.title}) ?? [],
            "result":result ?? [:]]
        if let data=try? JSONSerialization.data(withJSONObject:report,options:[.prettyPrinted,.sortedKeys]) {
            try? data.write(to:root.appendingPathComponent("state.json"),options:.atomic)
        }
    }
    func captureWindow() {
        guard let root=diagnosticRoot,let view=NSApp.windows.first(where:{$0.isVisible})?.contentView,
              let rep=view.bitmapImageRepForCachingDisplay(in:view.bounds) else { return }
        view.cacheDisplay(in:view.bounds,to:rep)
        if let data=rep.representation(using:.png,properties:[:]) { try? data.write(to:root.appendingPathComponent("window.png")) }
    }
    func advanceSmoke() {
        guard smokeSource != nil, diagnosticRoot != nil, error == nil else { return }
        switch smokeStep {
        case 0:
            smokeEvents.append("RAW imported and HDR preview generated");smokeStep=1
            DispatchQueue.main.asyncAfter(deadline:.now()+1) { self.recipe.exposure_ev=0.25; self.recipe.shadow_ev=0.5; self.commit() }
        case 1:
            smokeEvents.append("Exposure and shadows applied");smokeStep=2
            DispatchQueue.main.asyncAfter(deadline:.now()+0.5) { self.undo() }
        case 2:
            smokeEvents.append("Undo restored the prior render");smokeStep=3
            DispatchQueue.main.asyncAfter(deadline:.now()+0.5) { self.redo() }
        case 3:
            smokeEvents.append("Redo restored the edited render");smokeStep=4
            DispatchQueue.main.asyncAfter(deadline:.now()+0.5) { self.fullSize(); DispatchQueue.main.asyncAfter(deadline:.now()+0.2) { self.cancel() } }
        case 4:
            smokeEvents.append("Full-size job cancelled");smokeStep=5
            DispatchQueue.main.asyncAfter(deadline:.now()+0.5) { self.request("preview") }
        case 5:
            smokeEvents.append("Preview recovered after cancellation");smokeStep=6
            DispatchQueue.main.asyncAfter(deadline:.now()+0.5) {
                self.exporting=true
                self.request("export",extras:["destination":self.diagnosticRoot!.appendingPathComponent("smoke_ultrahdr.jpg").path,
                    "include_sdr":true,"strip_metadata":false,"overwrite":false])
            }
        case 6:
            smokeEvents.append("Full-resolution Ultra HDR exported and validated");smokeStep=7
            DispatchQueue.main.asyncAfter(deadline:.now()+3) { self.captureWindow();self.diagnostic(event:"smoke-complete") }
        default:break
        }
    }
    func close() { closing = true; clearStartupWatchdog();pending?.cancel(); persist(); if ready { engine.close() } else { engine.stopStartup() };try? FileManager.default.removeItem(at:leaseURL) }
}
