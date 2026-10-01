#include <metal_stdlib>
using namespace metal;
constant float3 luma2020 = float3(.26270021,.67799807,.05930172);
constant float3 lumaP3 = float3(.22897456,.69173852,.07928691);
float ss(float v) { float t=clamp(v,0.f,1.f); return t*t*(3-2*t); }
float3 p3to2020(float3 v) { return float3(dot(v,float3(.753833,.198597,.047570)),dot(v,float3(.045744,.941777,.012479)),dot(v,float3(-.001210,.017602,.983609))); }
float3 r2020top3(float3 v) { return float3(dot(v,float3(1.343578,-.282180,-.061399)),dot(v,float3(-.065298,1.075788,-.010490)),dot(v,float3(.002822,-.019599,1.016777))); }
float3 toSRGB(float3 v) { return float3(dot(v,float3(1.660491,-.587641,-.07285)),dot(v,float3(-.124550,1.132900,-.008349)),dot(v,float3(-.018151,-.100579,1.118730))); }
float3 fromSRGB(float3 v) { return float3(dot(v,float3(.627404,.329283,.043313)),dot(v,float3(.069097,.919540,.011362)),dot(v,float3(.016391,.088013,.895595))); }
float3 lab(float3 c) {
    c=toSRGB(c);
    float3 lms=float3(dot(c,float3(.4122214708,.5363325363,.0514459929)),dot(c,float3(.2119034982,.6806995451,.1073969566)),dot(c,float3(.0883024619,.2817188376,.6299787005)));
    lms=sign(lms)*pow(abs(lms),float3(1.f/3));
    return float3(dot(lms,float3(.2104542553,.793617785,-.0040720468)),dot(lms,float3(1.9779984951,-2.428592205,.4505937099)),dot(lms,float3(.0259040371,.7827717662,-.808675766)));
}
float3 unlab(float3 c) {
    float3 v=float3(c.x+.3963377774*c.y+.2158037573*c.z,c.x-.1055613458*c.y-.0638541728*c.z,c.x-.0894841775*c.y-1.291485548*c.z);v=v*v*v;
    return fromSRGB(float3(dot(v,float3(4.0767416621,-3.3077115913,.2309699292)),dot(v,float3(-1.2684380046,2.6097574011,-.3413193965)),dot(v,float3(-.0041960863,-.7034186147,1.707614701))));
}
float oetf(float x) { return x<=.0031308 ? max(x,0.f)*12.92 : 1.055*pow(x,1.f/2.4)-.055; }
float adjusted(float y, constant float *p) {
    y=max(y*exp2(p[0]),0.f); y*=exp2(p[1]*pow(max(1-y/.18,0.f),2.f));
    y*=exp2(p[2]*ss((y-.18)/.82));
    y*=exp2(p[35]*pow(max(1-y/.045,0.f),2.f));
    return y*exp2(p[34]*pow(y/(y+.9),2.f));
}
float midtone(float y,constant float *p) {
    float lo=log2(max(p[5],1e-6f)), mid=log2(max(p[6],max(p[5]*1.01f,1e-6f))), hi=log2(max(p[7],max(p[6]*1.01f,1e-6f)));
    float v=log2(max(y,1e-6f));
    return y*exp2(min(.38f,.4f*(hi-mid))*ss((v-lo)/max(mid-lo,1e-4f))*(1-ss((v-mid)/max(hi-mid,1e-4f))));
}
float2 sdrTone(float source,constant float *p,float extra) {
    float ev=p[8]-p[33]+extra,x=source*exp2(ev);
    if(p[31]>0) {
        float base=log2(max(source,1e-4f));
        float strength=.8*p[13]*(1-.65*p[12])*ss((p[15]-2)/4)*(1-ss((p[14]-.05)/.10));
        x*=exp2(-strength*max(base+ev+2,0.f)-.45*p[16]*ss((base-log2(.16f))/2));
    }
    float above=max(x-p[9],0.f);x=x-above+above/(1+p[11]*above/max(p[10],1e-4f));
    float before=(1-p[12])*(x<.001f ? x*(1-x*.5f+x*x/6):1-exp(-x))+p[12]*x/(1+x), y=clamp(before,0.f,1.f);
    if(p[17]>0 && y>0 && y<1) { float safe=clamp(y,1e-6f,1-1e-6f);y=1/(1+exp(-((1+p[17])*log(safe/(1-safe))-p[17]*log(p[18]/(1-p[18]))))); }
    if(y<.3) y=.3*pow(max(y/.3,0.f),1-.28*p[19]);
    float over=max(y-.15,0.f),compressed=y-over+over/(1+p[20]*over),keep=ss((source-p[21])/max(p[22]-p[21],1e-4f));
    return float2(mix(compressed,y,keep),before);
}
float tone(float rawY,constant float *p,bool hdr) {
    float y=adjusted(rawY,p);
    y=.18*pow(max((y-p[3])/max(.18-p[3],.001f),0.f),p[4]);y=midtone(y,p);
    float2 s=sdrTone(y,p,hdr?0:p[33]);
    if(!hdr) return s.x;
    float ref=s.x*pow(max(s.y/max(s.x,1e-5f),1.f),p[23]);
    float base=ref*(1+(p[24]-1)*ss((ref-.015)/.085));
    float anchor=max(p[26],.5f),extra=max(p[25]-anchor*p[24],0.f);
    float expanded=base+ref*max(p[25]-p[24],0.f)*ss(ref/anchor);
    float sourceT=clamp((y-p[21])/max(p[22]-p[21],1e-4f),0.f,1.f);
    float intermediate=.12*p[13]*clamp((p[15]-4)/2,0.f,1.f)*(1-p[23]);
    float darkExpanded=base+extra*pow(sourceT,p[27]);
    if(intermediate>0) { intermediate=min(intermediate,extra);darkExpanded=base+intermediate*ss((y/max(p[21],1e-5f)-.5)/.5)+(extra-intermediate)*pow(sourceT,p[27]+.55); }
    expanded=mix(expanded,darkExpanded,p[13]);
    float above=max(expanded-p[25],0.f);
    if(p[28]>0 && above>0) expanded=p[25]+above/(1+p[28]*above/max(1000.f/203-p[25],.05f));
    float result=mix(ref,min(expanded,1000.f/203),p[29]);
    if(p[31]>0 && p[29]>0 && p[30]<1) {
        float upper=1+(1000.f/203-1)*p[29],lo=exp2(mix(log2(.20f),log2(.045f),p[12])),hi=exp2(mix(log2(1.f),log2(.24f),p[12]));
        float ev=.55*p[29]*(1-p[30])*ss(log2(max(result,1e-7f)/lo)/log2(hi/lo)),gain=exp2(ev);
        float changed=result*gain/(1+max(result,0.f)/upper*(gain-1));
        result=clamp(changed,result*exp2(-.5f),result*exp2(.5f));
    }
    return result;
}
float3 bounded(float3 c,bool hdr) {
    float limit=hdr?1000.f/203:1, y=dot(c,hdr?luma2020:lumaP3);
    float3 neutral=float3(clamp(y,0.f,limit)),diff=c-neutral;
    float scale=1;
    for(uint k=0;k<3;k++) { if(diff[k]>0) scale=min(scale,(limit-neutral[k])/max(diff[k],1e-7f));if(diff[k]<0) scale=min(scale,-neutral[k]/min(diff[k],-1e-7f)); }
    return clamp(neutral+diff*max(scale,0.f),0.f,limit);
}
kernel void editPreview(texture2d<half,access::read> scene [[texture(0)]], texture2d<half,access::read> exact [[texture(1)]], texture2d<half,access::write> output [[texture(2)]],constant float *current [[buffer(0)]],constant float *anchor [[buffer(1)]],constant float3x3 &wb [[buffer(2)]],constant uint2 &flags [[buffer(3)]],uint2 pos [[thread_position_in_grid]]) {
    if(pos.x>=output.get_width()||pos.y>=output.get_height()) return;
    float3 original=float3(exact.read(pos).rgb);
    if(flags.y==0) { output.write(half4(half3(original),1),pos);return; }
    bool hdr=flags.x!=0;
    float3 raw=max(float3(scene.read(pos).rgb),0.f),changed=max(wb*raw,0.f);
    // Keep WB preview luminance stable; precise redevelopment recomputes the
    // automatic exposure/look from the new camera-space white balance.
    float temperatureGreen=current[38];
    changed*=dot(raw,luma2020)/(max(dot(changed,luma2020),1e-8f)*max(temperatureGreen,.1f));
    float before=tone(dot(raw,luma2020),anchor,hdr),after=tone(dot(changed,luma2020),current,hdr);
    float3 color=hdr?original:p3to2020(original);
    // Transport the exact local/skin/color correction, never an earlier JPEG.
    // Near black, use the scene chromaticity instead of dividing by zero.
    if(before>1e-6) color*=after/before;
    else color=changed*(after/max(dot(changed,luma2020),1e-8f));
    if(current[31]>0) {
        // Clear's broad sun/shade request changes with exposure. Recompute this
        // monotone component; retain the exact spatial/edge residual.
        float oldSource=adjusted(dot(raw,luma2020),anchor),newSource=adjusted(dot(changed,luma2020),current);
        oldSource=midtone(.18*pow(max((oldSource-anchor[3])/max(.18-anchor[3],.001f),0.f),anchor[4]),anchor);
        newSource=midtone(.18*pow(max((newSource-current[3])/max(.18-current[3],.001f),0.f),current[4]),current);
        float a=sdrTone(oldSource,anchor,0).x,b=sdrTone(newSource,current,0).x;
        float oldField=anchor[36]*(.35*(1-ss((a-.025)/.12))-.45*ss((a-.25)/.30))-.25*anchor[30]*(1-anchor[13])*(1-ss((a-.06)/.35));
        float newField=current[36]*(.35*(1-ss((b-.025)/.12))-.45*ss((b-.25)/.30))-.25*current[30]*(1-current[13])*(1-ss((b-.06)/.35));
        float ev=clamp(newField-oldField,-.5f,.5f),gain=exp2(ev),y=dot(color,luma2020);
        float upper=hdr ? 1+(1000.f/203-1)*current[29]:1;
        color*=gain/(1+max(y,0.f)/upper*(gain-1));
    }
    float beforeWB=dot(color,luma2020);
    color*=clamp(changed/max(raw,float3(1e-5)),float3(.1),float3(10));
    color*=beforeWB/max(dot(color,luma2020),1e-8f);
    float newY=dot(color,luma2020), saturation=current[32]/max(anchor[32],1e-5f);
    if(abs(saturation-1)>1e-6) { float3 c=lab(color);c.yz*=saturation;color=unlab(c);color*=newY/max(dot(color,luma2020),1e-8f); }
    if(!hdr) color=r2020top3(color);
    output.write(half4(half3(bounded(color,hdr)),1),pos);
}

uint binFor(float v,bool hdr) {
    if(!isfinite(v)||v<=0) return 0;
    if(!hdr) return uint(round(clamp(oetf(v)*255,0.f,255.f)));
    return v<=1 ? uint(round(clamp(oetf(v)*191,0.f,191.f))) : uint(round(clamp(192+log2(v)/log2(1000.f/203)*63,192.f,255.f)));
}
kernel void histogram(texture2d<half,access::read> image [[texture(0)]],device atomic_uint *bins [[buffer(0)]],constant uint &hdr [[buffer(1)]],uint2 pos [[thread_position_in_grid]],uint local [[thread_index_in_threadgroup]]) {
    threadgroup atomic_uint counts[1024];
    for(uint i=local;i<1024;i+=256) atomic_store_explicit(&counts[i],0,memory_order_relaxed);
    threadgroup_barrier(mem_flags::mem_threadgroup);
    if(pos.x<image.get_width()&&pos.y<image.get_height()) {
        float3 c=float3(image.read(pos).rgb);
        float4 vals=float4(c,dot(c,hdr?luma2020:lumaP3));
        for(uint k=0;k<4;k++) atomic_fetch_add_explicit(&counts[k*256+binFor(vals[k],hdr!=0)],1,memory_order_relaxed);
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    for(uint i=local;i<1024;i+=256) atomic_fetch_add_explicit(&bins[i],atomic_load_explicit(&counts[i],memory_order_relaxed),memory_order_relaxed);
}
struct VertexOut { float4 position [[position]];float2 uv; };
vertex VertexOut imageVertex(uint id [[vertex_id]],constant float2 &scale [[buffer(0)]]) {
    constexpr float2 coords[6]={float2(0,0),float2(1,0),float2(0,1),float2(1,0),float2(1,1),float2(0,1)};
    float2 uv=coords[id];
    return {float4((uv*2-1)*scale,0,1),float2(uv.x,1-uv.y)};
}
fragment half4 imageFragment(VertexOut in [[stage_in]],texture2d<half> image [[texture(0)]],constant uint2 &flags [[buffer(0)]]) {
    constexpr sampler s(filter::linear,address::clamp_to_edge);
    float3 c=float3(image.sample(s,in.uv).rgb);bool hdr=flags.x!=0;
    float y=dot(c,hdr?luma2020:lumaP3);
    bool low=hdr?y<=1e-5f:all(c<=float3(.5f/255/12.92));
    bool high=hdr?any(c>=float3((1000.f/203)*.999)):any(c>=float3(pow((254.5f/255+.055)/1.055,2.4f)));
    if((flags.y&1)&&low) return half4(.04,.16,.9,1);
    if((flags.y&2)&&high) return half4(1,.05,.025,1);
    return half4(half3(c),1);
}
