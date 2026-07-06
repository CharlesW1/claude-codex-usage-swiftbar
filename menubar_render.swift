// Renders a two-line, per-line-colored PNG for the SwiftBar menu bar and prints
// it as base64. Usage: menubar_render "line1" "#rrggbb" "line2" "#rrggbb"
// SwiftBar scales the image to the menu bar height, so we render large for crisp
// Retina output.
import AppKit

func color(_ hex: String) -> NSColor {
    var s = hex
    if s.hasPrefix("#") { s.removeFirst() }
    guard s.count == 6, let v = Int(s, radix: 16) else { return .labelColor }
    return NSColor(
        srgbRed: CGFloat((v >> 16) & 0xff) / 255.0,
        green: CGFloat((v >> 8) & 0xff) / 255.0,
        blue: CGFloat(v & 0xff) / 255.0,
        alpha: 1.0)
}

let args = CommandLine.arguments
guard args.count >= 5 else { FileHandle.standardError.write("need 4 args\n".data(using: .utf8)!); exit(1) }
let (t1, c1, t2, c2) = (args[1], args[2], args[3], args[4])

let font = NSFont.systemFont(ofSize: 17, weight: .semibold)
func line(_ s: String, _ hex: String) -> NSAttributedString {
    NSAttributedString(string: s, attributes: [.font: font, .foregroundColor: color(hex)])
}
let a1 = line(t1, c1)
let a2 = line(t2, c2)

let padX: CGFloat = 3
let lh = ceil(max(a1.size().height, a2.size().height))
let w = ceil(max(a1.size().width, a2.size().width)) + padX * 2
let h = lh * 2

guard let rep = NSBitmapImageRep(
    bitmapDataPlanes: nil, pixelsWide: Int(w), pixelsHigh: Int(h),
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0) else { exit(1) }
rep.size = NSSize(width: w, height: h)

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
// AppKit origin is bottom-left: line 2 sits on the bottom row, line 1 on top.
a2.draw(at: NSPoint(x: padX, y: 0))
a1.draw(at: NSPoint(x: padX, y: lh))
NSGraphicsContext.restoreGraphicsState()

guard let png = rep.representation(using: .png, properties: [:]) else { exit(1) }
print(png.base64EncodedString())
