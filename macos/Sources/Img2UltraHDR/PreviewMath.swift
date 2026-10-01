import Foundation
import simd

struct PreviewPacket: Decodable {
    let version: Int
    let gpu_version: String
    let pixel_format: String
    let row_bytes: Int
    let scene_color_space: String
    let sdr_color_space: String
    let hdr_color_space: String
    let reference_white_nits: Double
    let peak_nits: Double
    let width: Int
    let height: Int
    let scene: String
    let sdr: String
    let hdr: String
    let anchor_recipe: Recipe
    let tone: [String: AnyNumber]
    let exposure: [String: Double]
    let luminance_quantiles: [Double]
    var key: String { hdr }
    static func decode(_ value: Any?) -> PreviewPacket? {
        guard let value, let data = try? JSONSerialization.data(withJSONObject:value),
              let packet = try? JSONDecoder().decode(Self.self,from:data), packet.version == 1,
              packet.width > 0, packet.height > 0, max(packet.width,packet.height) <= 1536,
              packet.gpu_version == "scene-tone-1",packet.pixel_format == "rgba16FloatLE",
              packet.row_bytes == packet.width*8,packet.scene_color_space == "linear-rec2020",
              packet.hdr_color_space == "linear-rec2020",packet.sdr_color_space == "linear-display-p3",
              packet.reference_white_nits == 203,packet.peak_nits == 1000,
              packet.luminance_quantiles.count == 1025,
              packet.luminance_quantiles.allSatisfy({ $0.isFinite && $0>=0 }) else { return nil }
        return packet
    }
    func number(_ key: String, _ fallback: Double = 0) -> Double { tone[key]?.value ?? fallback }
}

// Tone records also contain nested diagnostic dictionaries. Consume just their
// numeric entries while retaining the engine's extensible JSON record.
struct AnyNumber: Decodable {
    let value: Double?
    init(from decoder: Decoder) throws { value = try? decoder.singleValueContainer().decode(Double.self) }
}

enum PreviewMath {
    static func smooth(_ x: Double) -> Double { let t = min(1,max(0,x)); return t*t*(3-2*t) }
    static func adjusted(_ y: Double, _ ev: Double, _ shadow: Double, _ highlight: Double) -> Double {
        var v = max(0,y*exp2(ev))
        v *= exp2(shadow*pow(max(1-v/0.18,0),2))
        return v*exp2(highlight*smooth((v-0.18)/0.82))
    }
    static func percentile(_ sorted: [Double], _ p: Double) -> Double {
        guard !sorted.isEmpty else { return 0 }
        let x = min(1,max(0,p/100))*Double(sorted.count-1), i = Int(x), f = x-Double(i)
        return sorted[i]*(1-f)+sorted[min(i+1,sorted.count-1)]*f
    }
    static func contrast(_ y: Double, _ black: Double, _ power: Double) -> Double {
        0.18*pow(max((y-black)/max(0.18-black,0.001),0),power)
    }
    static func midtone(_ y: Double, _ lo: Double, _ mid: Double, _ hi: Double) -> Double {
        let l = log2(max(lo,1e-6)), m = log2(max(mid,max(lo*1.01,1e-6))), h = log2(max(hi,max(mid*1.01,1e-6)))
        let logY = log2(max(y,1e-6))
        return y*exp2(min(0.38,0.4*(h-m))*smooth((logY-l)/max(m-l,1e-4))*(1-smooth((logY-m)/max(h-m,1e-4))))
    }
    static func sdr(_ source: Double, _ p: [Float], extra: Double) -> (Double,Double) {
        let a = p.map(Double.init)
        let ev = a[8]-a[33]+extra
        var x = source*exp2(ev)
        if a[31] > 0 {
            let base = log2(max(source,1e-4))
            let strength = 0.8*a[13]*(1-0.65*a[12])*smooth((a[15]-2)/4)*(1-smooth((a[14]-0.05)/0.10))
            x *= exp2(-strength*max(base+ev+2,0)-0.45*a[16]*smooth((base-log2(0.16))/2))
        }
        let above = max(x-a[9],0)
        x = x-above+above/(1+a[11]*above/max(a[10],1e-4))
        let before = (1-a[12])*(-expm1(-x))+a[12]*x/(1+x)
        var y = min(1,max(0,before))
        if a[17] > 0 && y > 0 && y < 1 {
            let safe = min(1-1e-6,max(1e-6,y))
            y = 1/(1+exp(-((1+a[17])*log(safe/(1-safe))-a[17]*log(a[18]/(1-a[18])))))
        }
        if y < 0.3 { y = 0.3*pow(max(y/0.3,0),1-0.28*a[19]) }
        let over = max(y-0.15,0), compressed = y-over+over/(1+a[20]*over)
        let keep = smooth((source-a[21])/max(a[22]-a[21],1e-4))
        return (compressed*(1-keep)+y*keep,before)
    }
    // Recompute percentile-dependent global tone from a sorted full-photo sample.
    // Sort the small transformed sample: strong negative highlights can fold
    // part of the luminance curve. No whole-photo sorting or Python is needed.
    static func parameters(_ packet: PreviewPacket, _ recipe: Recipe) -> [Float] {
        func n(_ key: String, _ fallback: Double = 0) -> Double { packet.number(key,fallback) }
        var p = [Float](repeating:0,count:40)
        func put(_ i: Int, _ v: Double) { p[i] = Float(v.isFinite ? v : 0) }
        let ev = (packet.exposure["scene_adjustment_ev"] ?? 0)+recipe.exposure_ev-packet.anchor_recipe.exposure_ev
        put(0,ev);put(1,recipe.shadow_ev);put(2,recipe.highlight_ev)
        let q = packet.luminance_quantiles.map { adjusted($0,ev,recipe.shadow_ev,recipe.highlight_ev) }.sorted()
        let black = min(0.09,0.5*percentile(q,1)), power = n("contrast",1.1)
        let contrasted = q.map { contrast($0,black,power) }
        let lo = percentile(contrasted,10), mid = percentile(contrasted,50), hi = percentile(contrasted,95)
        let mapped = contrasted.map { midtone($0,lo,mid,hi) }
        let p90 = percentile(mapped,90), p995 = percentile(mapped,99.5)
        let ceiling = 3-2*0.42, shoulder = min(p90,0.75*ceiling), span = max(p995-shoulder,0.1)
        put(3,black);put(4,power);put(5,lo);put(6,mid);put(7,hi)
        put(8,n("sdr_effective_exposure_ev")-packet.anchor_recipe.sdr_exposure_ev+recipe.sdr_exposure_ev)
        put(9,shoulder);put(10,span);put(11,span/max(ceiling-shoulder,0.1))
        let keys = ["phone_clear_daylit_dark_weight","phone_dark_weight","phone_dark_fraction"]
        for (i,k) in keys.enumerated() { put(12+i,n(k)) }
        put(15,n("phone_raw_p99")/max(n("phone_raw_p90"),0.01));put(16,n("phone_high_key_weight"))
        let rest = ["phone_sdr_contrast","phone_sdr_pivot","phone_sdr_shadow_lift","phone_sdr_dark_shoulder"]
        for (i,k) in rest.enumerated() { put(17+i,n(k)) }
        put(21,p90);put(22,percentile(mapped,n("phone_hdr_anchor_percentile",99.5)))
        put(23,n("phone_hdr_dark_restore"));put(24,n("hdr_midtone_gain",1));put(25,n("phone_hdr_peak_ratio",1))
        put(26,n("hdr_sdr_highlight_anchor",0.5));put(27,n("phone_hdr_dark_highlight_power",1))
        put(28,n("hdr_shoulder_strength",0.7));put(29,recipe.hdr_strength);put(30,n("phone_indoor_weight"))
        put(31,recipe.style == "phone-clear" ? 1 : 0);put(32,recipe.saturation);put(33,recipe.sdr_exposure_ev)
        put(36,smooth((log2(max(n("phone_raw_p90"),1e-5)/max(n("phone_raw_p10"),1e-5))-3)/2)*(1-n("phone_dark_weight"))*(1-n("phone_indoor_weight")))
        put(37,recipe.white_balance == "custom" ? recipe.tint:0)
        var temperatureOnly=recipe;temperatureOnly.tint=packet.anchor_recipe.tint
        put(38,Double((whiteBalance(anchor:packet.anchor_recipe,current:temperatureOnly)*SIMD3<Float>(repeating:1)).y))
        let samples = mapped.map { sdr($0,p,extra:0).0 }.sorted()
        put(26,max(percentile(samples,n("phone_hdr_anchor_percentile",99.5)),0.5))
        return p
    }

    static let toXYZ = simd_float3x3(rows:[SIMD3(0.63695805,0.14461690,0.16888098),SIMD3(0.26270021,0.67799807,0.05930172),SIMD3(0,0.02807269,1.06098506)])
    static let srgbToXYZ = simd_float3x3(rows:[SIMD3(0.4123908,0.35758434,0.18048079),SIMD3(0.21263901,0.71516868,0.07219232),SIMD3(0.01933082,0.11919478,0.95053215)])
    static let bradford = simd_float3x3(rows:[SIMD3(0.8951,0.2664,-0.1614),SIMD3(-0.7502,1.7135,0.0367),SIMD3(0.0389,-0.0685,1.0296)])
    static func white(_ kelvin: Int) -> SIMD3<Float> {
        let t = Double(kelvin)
        let x = t <= 4000 ? -0.2661239e9/pow(t,3)-0.234358e6/(t*t)+0.8776956e3/t+0.179910 : -3.0258469e9/pow(t,3)+2.1070379e6/(t*t)+0.2226347e3/t+0.240390
        let y: Double
        if t <= 2222 { y = -1.1063814*pow(x,3)-1.3481102*x*x+2.18555832*x-0.20219683 }
        else if t <= 4000 { y = -0.9549476*pow(x,3)-1.37418593*x*x+2.09137015*x-0.16748867 }
        else { y = 3.081758*pow(x,3)-5.8733867*x*x+3.75112997*x-0.37001483 }
        return SIMD3(Float(x/y),1,Float((1-x-y)/y))
    }
    static func whiteBalance(anchor: Recipe, current: Recipe) -> simd_float3x3 {
        guard anchor.white_balance == "custom",current.white_balance == "custom" else { return matrix_identity_float3x3 }
        let from = bradford*white(anchor.temperature_k), to = bradford*white(current.temperature_k)
        let adaptation = toXYZ.inverse*bradford.inverse*simd_float3x3(diagonal:from/to)*bradford*toXYZ
        let tint = Float(exp2((current.tint-anchor.tint)/100))
        // RawTherapee defines Green against its sRGB illuminant multipliers.
        // Applying that multiplier directly to Rec.2020 green exaggerates tint.
        let toSRGB=srgbToXYZ.inverse*toXYZ
        return toSRGB.inverse*simd_float3x3(diagonal:SIMD3(1,tint,1))*toSRGB*adaptation
    }
}
