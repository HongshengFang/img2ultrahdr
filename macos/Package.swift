// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "Img2UltraHDR", defaultLocalization: "zh-Hans", platforms: [.macOS(.v15)],
    products: [.executable(name: "Img2UltraHDR", targets: ["Img2UltraHDR"])],
    targets: [.executableTarget(name: "Img2UltraHDR", resources: [.process("Resources")])], swiftLanguageModes: [.v5])
