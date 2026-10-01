import Foundation

enum AppVersion {
    private struct Metadata: Decodable { let version: String; let build: String }
    private static let resource: Metadata? = {
        guard let url = AppResources.bundle.url(forResource: "AppVersion", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Metadata.self, from: data)
    }()
    // Installed apps report their own bundle version; SwiftPM and native
    // development checks use the same resource that generates Info.plist.
    static let number = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? resource?.version
    static let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? resource?.build
    static var badge: String { number.map { "v" + $0 } ?? L("开发版本") }
    static var windowTitle: String { "Img2UltraHDR · " + badge }
    static var details: String {
        guard let number, let build else { return L("开发版本") }
        return "\(L("版本")) \(number) · \(L("构建")) \(build)"
    }
}
