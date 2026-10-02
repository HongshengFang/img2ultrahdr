import AppKit

struct LocalInteraction {
    var enabled=false
    var adding=false
    var regions:[LocalAdjustment]=[]
    var selected:String?
    var masks:[String:LocalMaskPacket]=[:]
    var add:((NSPoint)->Void)?
    var select:((String)->Void)?
    var begin:(()->Void)?
    var change:((LocalAdjustment)->Void)?
    var end:(()->Void)?
    var reselect:((LocalAdjustment)->Void)?
    func visuallyMatches(_ other:LocalInteraction)->Bool {
        guard enabled==other.enabled,selected==other.selected,regions.count==other.regions.count else { return false }
        for (a,b) in zip(regions,other.regions) {
            if a.id != b.id || a.enabled != b.enabled || a.mode != b.mode || a.shape != b.shape ||
                a.center_x != b.center_x || a.center_y != b.center_y || a.radius_x != b.radius_x || a.radius_y != b.radius_y || a.rotation != b.rotation { return false }
        }
        return masks[selected ?? ""]?.path==other.masks[other.selected ?? ""]?.path
    }
}

extension EditorModel {
    var localInteraction:LocalInteraction {
        var visible=recipe.local_adjustments
        if let pendingLocal {
            if let index=visible.firstIndex(where:{$0.id==pendingLocal.id}) { visible[index]=pendingLocal }
            else { visible.append(pendingLocal) }
        }
        return LocalInteraction(enabled:localEditing && !showInitial && !localBypass && !exporting,
            adding:addingLocal,regions:visible,
            selected:selectedLocalID,masks:resultPacket?.local_masks ?? [:],
            add:{ self.addLocal(at:$0) },select:{ self.selectedLocalID=$0;self.addingLocal=false },
            begin:{ self.beginDrag() },change:{ r in self.updateLocal({$0=r},commitNow:false) },
            end:{ self.endDrag() },reselect:{ r in self.addLocal(at:NSPoint(x:r.center_x,y:r.center_y),replacing:r) })
    }
}

/// A separate AppKit overlay: never rasterized into the HDR image or readouts.
final class LocalOverlay:NSView {
    var interaction=LocalInteraction() { didSet { if !interaction.visuallyMatches(oldValue) { needsDisplay=true;loadMask() } } }
    var photoRect=NSRect.zero { didSet { if photoRect != oldValue { needsDisplay=true } } }
    var photoSize=NSSize(width:1,height:1)
    weak var panOwner:NSView?
    private var spaceDown=false
    private var panning=false
    private var moving:LocalAdjustment?
    private var handle = -1
    private var maskImage:NSImage?
    private var maskKey=""
    override var isFlipped:Bool { true }
    override var acceptsFirstResponder:Bool { true }
    override func hitTest(_ point:NSPoint)->NSView? { interaction.enabled ? super.hitTest(point):nil }
    func imagePoint(_ point:NSPoint)->NSPoint? {
        guard photoRect.width>0,photoRect.height>0,photoRect.contains(point) else { return nil }
        return NSPoint(x:(point.x-photoRect.minX)/photoRect.width,y:(point.y-photoRect.minY)/photoRect.height)
    }
    func screenPoint(_ r:LocalAdjustment)->NSPoint {
        NSPoint(x:photoRect.minX+r.center_x*photoRect.width,y:photoRect.minY+r.center_y*photoRect.height)
    }
    func handlePoints(_ r:LocalAdjustment)->[NSPoint] {
        let center=screenPoint(r),angle=r.rotation * .pi/180,c=cos(angle),s=sin(angle)
        let a=r.radius_x*photoRect.width,b=r.radius_y*photoRect.height
        return [NSPoint(x:center.x+c*a,y:center.y+s*a),NSPoint(x:center.x-s*b,y:center.y+c*b),
                NSPoint(x:center.x-c*a,y:center.y-s*a),NSPoint(x:center.x+s*b,y:center.y-c*b),
                NSPoint(x:center.x+s*(b+24),y:center.y-c*(b+24))]
    }
    func loadMask() {
        guard let id=interaction.selected,let region=interaction.regions.first(where:{$0.id==id}),region.mode=="smart",
              let descriptor=interaction.masks[id] else { maskKey="";maskImage=nil;return }
        guard descriptor.path != maskKey else { return };maskKey=descriptor.path;maskImage=nil
        let key=maskKey
        DispatchQueue.global(qos:.userInitiated).async { [weak self] in
            guard descriptor.width>0,descriptor.height>0,max(descriptor.width,descriptor.height)<=1536,
                  let data=try? Data(contentsOf:URL(fileURLWithPath:key)),data.count==descriptor.width*descriptor.height*4 else { return }
            var rgba=[UInt8](repeating:0,count:descriptor.width*descriptor.height*4)
            data.withUnsafeBytes { raw in
                let values=raw.bindMemory(to:Float.self)
                for i in 0..<values.count { rgba[i*4]=40;rgba[i*4+1]=155;rgba[i*4+2]=240;rgba[i*4+3]=UInt8(min(45,max(0,values[i]*45))) }
            }
            let provider=CGDataProvider(data:Data(rgba) as CFData)!
            guard let image=CGImage(width:descriptor.width,height:descriptor.height,bitsPerComponent:8,bitsPerPixel:32,bytesPerRow:descriptor.width*4,
                                   space:CGColorSpaceCreateDeviceRGB(),bitmapInfo:CGBitmapInfo(rawValue:CGImageAlphaInfo.last.rawValue),
                                   provider:provider,decode:nil,shouldInterpolate:true,intent:.defaultIntent) else { return }
            DispatchQueue.main.async { if self?.maskKey==key { self?.maskImage=NSImage(cgImage:image,size:.zero);self?.needsDisplay=true } }
        }
    }
    override func draw(_ dirtyRect:NSRect) {
        guard interaction.enabled else { return }
        NSGraphicsContext.saveGraphicsState();NSBezierPath(rect:photoRect).addClip()
        maskImage?.draw(in:photoRect,from:.zero,operation:.sourceOver,fraction:1,respectFlipped:true,hints:nil)
        for (index,r) in interaction.regions.enumerated() {
            let selected=r.id==interaction.selected,center=screenPoint(r)
            NSColor.white.withAlphaComponent(r.enabled ? 1:0.5).setStroke()
            if selected && r.mode=="soft" {
                let path=NSBezierPath(ovalIn:NSRect(x:-r.radius_x*photoRect.width,y:-r.radius_y*photoRect.height,width:r.radius_x*photoRect.width*2,height:r.radius_y*photoRect.height*2))
                let transform=AffineTransform(translationByX:center.x,byY:center.y)
                var rotate=AffineTransform();rotate.rotate(byDegrees:CGFloat(r.rotation));path.transform(using:rotate);path.transform(using:transform)
                path.lineWidth=1.5;path.stroke()
                for (i,p) in handlePoints(r).enumerated() where i<4 || r.shape=="ellipse" {
                    NSColor.controlAccentColor.setFill();NSBezierPath(ovalIn:NSRect(x:p.x-4,y:p.y-4,width:8,height:8)).fill()
                }
            }
            NSColor.black.withAlphaComponent(0.7).setFill()
            let pin=NSBezierPath(ovalIn:NSRect(x:center.x-11,y:center.y-11,width:22,height:22));pin.fill();pin.lineWidth=selected ? 2:1;pin.stroke()
            let text="\(index+1)" as NSString
            let attrs:[NSAttributedString.Key:Any]=[.font:NSFont.systemFont(ofSize:12,weight:.medium),.foregroundColor:NSColor.white]
            let size=text.size(withAttributes:attrs);text.draw(at:NSPoint(x:center.x-size.width/2,y:center.y-size.height/2),withAttributes:attrs)
        }
        NSGraphicsContext.restoreGraphicsState()
    }
    override func keyDown(with event:NSEvent) { if event.keyCode==49 { spaceDown=true } else { super.keyDown(with:event) } }
    override func keyUp(with event:NSEvent) { if event.keyCode==49 { spaceDown=false } else { super.keyUp(with:event) } }
    override func mouseDown(with event:NSEvent) {
        window?.makeFirstResponder(self)
        // Space may have gone to a slider or numeric field before the canvas
        // receives the mouse event. Honor the held key across those controls.
        if spaceDown || CGEventSource.keyState(.combinedSessionState,key:49) { panning=true;panOwner?.mouseDown(with:event);return }
        let point=convert(event.locationInWindow,from:nil)
        if let selected=interaction.regions.first(where:{$0.id==interaction.selected}),selected.mode=="soft",
           let hit=handlePoints(selected).enumerated().first(where:{ ($0.offset<4 || selected.shape=="ellipse") && hypot($0.element.x-point.x,$0.element.y-point.y)<10 }) {
            handle=hit.offset;moving=selected;interaction.begin?();return
        }
        if let region=interaction.regions.last(where:{hypot(screenPoint($0).x-point.x,screenPoint($0).y-point.y)<14}) {
            interaction.select?(region.id);moving=region;handle = -1
            if region.mode=="soft" { interaction.begin?() };return
        }
        if interaction.adding,let p=imagePoint(point) { interaction.add?(p) }
    }
    override func mouseDragged(with event:NSEvent) {
        if panning { panOwner?.mouseDragged(with:event);return }
        guard var r=moving else { return }
        let p=convert(event.locationInWindow,from:nil)
        if handle<0 {
            r.center_x=min(1,max(0,(p.x-photoRect.minX)/photoRect.width));r.center_y=min(1,max(0,(p.y-photoRect.minY)/photoRect.height))
        } else {
            let center=screenPoint(r),dx=(p.x-center.x)*photoSize.width/photoRect.width,dy=(p.y-center.y)*photoSize.height/photoRect.height
            if handle==4 {
                let angle=atan2(dy,dx)*180 / .pi+90
                r.rotation=angle>180 ? angle-360:angle
            }
            else {
                let angle=r.rotation * .pi/180,c=cos(angle),s=sin(angle)
                var a=r.radius_x*photoSize.width,b=r.radius_y*photoSize.height
                if handle%2==0 { a=max(1,abs(c*dx+s*dy));if r.shape=="circle" { b=a } }
                else { b=max(1,abs(-s*dx+c*dy));if r.shape=="circle" { a=b } }
                let aspect=min(4,max(0.25,a/b)),radius=min(min(photoSize.width,photoSize.height),max(min(photoSize.width,photoSize.height)*0.025,sqrt(a*b)))
                r.radius_x=radius*sqrt(aspect)/photoSize.width;r.radius_y=radius/sqrt(aspect)/photoSize.height
            }
        }
        if r.mode=="soft" { interaction.change?(r) };moving=r
    }
    override func mouseUp(with event:NSEvent) {
        if panning { panOwner?.mouseUp(with:event);panning=false;return }
        if let r=moving {
            if r.mode=="soft" { interaction.end?() }
            else if let original=interaction.regions.first(where:{$0.id==r.id}),original.center_x != r.center_x || original.center_y != r.center_y { interaction.reselect?(r) }
        }
        moving=nil;handle = -1
    }
}
