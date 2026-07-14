// Generates assets/social-preview.png — the GitHub social-preview / README hero.
// Renders with AppKit (the same engine and system fonts the plugin uses) so the
// menu-bar chip and dropdown match the real thing. Regenerate with:
//
//   swiftc -O social-preview.swift -o /tmp/social_gen
//   /tmp/social_gen social@2x.png                            # 2560x1280
//   sips -z 640 1280 social@2x.png --out social-preview.png  # 1280x640, <1MB
//
import AppKit

// ---- Canvas ----
let S: CGFloat = 2                 // render scale (crisp), tag back to point size
let W: CGFloat = 1280, H: CGFloat = 640
let Wp = Int(W * S), Hp = Int(H * S)

func col(_ hex: String, _ a: CGFloat = 1) -> NSColor {
    var s = hex; if s.hasPrefix("#") { s.removeFirst() }
    let v = Int(s, radix: 16) ?? 0
    return NSColor(srgbRed: CGFloat((v>>16)&0xff)/255, green: CGFloat((v>>8)&0xff)/255,
                   blue: CGFloat(v&0xff)/255, alpha: a)
}
// Brand palette (matches plugin constants)
let WHITE = "#ffffff", GREEN = "#34c759", ORANGE = "#ff9500", RED = "#ff3b30", GRAY = "#8e8e93"

let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Wp, pixelsHigh: Hp,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
rep.size = NSSize(width: CGFloat(Wp), height: CGFloat(Hp))  // 1 unit = 1 pixel
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
let ctx = NSGraphicsContext.current!.cgContext

// ---- helpers (top-left coordinate system, points) ----
func P(_ v: CGFloat) -> CGFloat { v * S }
func rectTL(_ x: CGFloat, _ top: CGFloat, _ w: CGFloat, _ h: CGFloat) -> NSRect {
    NSRect(x: P(x), y: CGFloat(Hp) - P(top + h), width: P(w), height: P(h))
}
@discardableResult
func text(_ s: String, _ x: CGFloat, _ top: CGFloat, _ size: CGFloat,
          _ color: String, weight: NSFont.Weight = .regular, mono: Bool = false,
          tracking: CGFloat = 0, alpha: CGFloat = 1) -> CGFloat {
    let f = mono ? NSFont.monospacedSystemFont(ofSize: size*S, weight: weight)
                 : NSFont.systemFont(ofSize: size*S, weight: weight)
    let at: [NSAttributedString.Key: Any] = [.font: f, .foregroundColor: col(color, alpha),
                                             .kern: tracking*S]
    let a = NSAttributedString(string: s, attributes: at)
    let sz = a.size()
    a.draw(at: NSPoint(x: P(x), y: CGFloat(Hp) - P(top) - sz.height))
    return sz.width / S
}
func textW(_ s: String, _ size: CGFloat, weight: NSFont.Weight = .regular, mono: Bool = false, tracking: CGFloat = 0) -> CGFloat {
    let f = mono ? NSFont.monospacedSystemFont(ofSize: size*S, weight: weight)
                 : NSFont.systemFont(ofSize: size*S, weight: weight)
    let a = NSAttributedString(string: s, attributes: [.font: f, .kern: tracking*S])
    return a.size().width / S
}
func roundRect(_ x: CGFloat, _ top: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat,
               fill: String? = nil, fillAlpha: CGFloat = 1, stroke: String? = nil,
               strokeAlpha: CGFloat = 1, lw: CGFloat = 1) {
    let p = NSBezierPath(roundedRect: rectTL(x, top, w, h), xRadius: P(r), yRadius: P(r))
    if let fS = fill { col(fS, fillAlpha).setFill(); p.fill() }
    if let sS = stroke { col(sS, strokeAlpha).setStroke(); p.lineWidth = P(lw); p.stroke() }
}
func dot(_ cx: CGFloat, _ cyTop: CGFloat, _ r: CGFloat, _ color: String) {
    let rr = rectTL(cx - r, cyTop - r, r*2, r*2)
    col(color).setFill(); NSBezierPath(ovalIn: rr).fill()
}

// ---- background gradient ----
let g = NSGradient(colors: [col("#0d1117"), col("#05070c")])!
g.draw(in: NSRect(x: 0, y: 0, width: Wp, height: Hp), angle: -90)
// soft brand glow, upper area
ctx.saveGState()
let glow = NSGradient(colors: [col("#1f6feb", 0.18), col("#1f6feb", 0.0)])!
glow.draw(in: NSRect(x: P(360), y: CGFloat(Hp)-P(560), width: P(1000), height: P(620)),
          relativeCenterPosition: NSPoint(x: 0.15, y: 0.35))
ctx.restoreGState()

// =========================================================
// LEFT COLUMN
// =========================================================
let LX: CGFloat = 76

// eyebrow
text("MACOS  ·  SWIFTBAR PLUGIN", LX, 118, 15, GRAY, weight: .semibold, tracking: 3)

// title (providers line auto-sized to fit the left column)
var titleSize: CGFloat = 52
while textW("Claude + Codex + Antigravity", titleSize, weight: .bold, tracking: -0.5) > 596 { titleSize -= 1 }
text("Claude + Codex + Antigravity", LX, 150 + (52 - titleSize)/2, titleSize, WHITE, weight: .bold, tracking: -0.5)
// second line with accent on "Usage"
let uw = text("Usage", LX, 214, 52, "#58a6ff", weight: .bold, tracking: -0.5)
text(" in your menu bar", LX + uw, 214, 52, WHITE, weight: .bold, tracking: -0.5)

// tagline (two lines)
text("Claude Code, OpenAI Codex, and Google Antigravity", LX, 300, 19.5, "#adbac7")
text("limits, reset countdowns, and rate-limit status — at a glance.", LX, 330, 19.5, "#adbac7")

// feature bullets
let feats: [(String, String)] = [
    (GREEN,  "One 2x2 menu-bar grid: Claude, Codex + Antigravity"),
    (ORANGE, "5-hour & weekly windows with live reset countdowns"),
    (RED,    "Color-coded percent + timer so rate-limit risk is obvious"),
    (WHITE,  "Per-provider show/hide, refresh now + 1-minute boost"),
]
var fy: CGFloat = 392
for (c, s) in feats {
    dot(LX + 5, fy + 11, 5, c)
    text(s, LX + 24, fy, 17.5, "#c9d1d9")
    fy += 39
}

// footer line
text("github.com/CharlesW1/claude-codex-usage-swiftbar", LX, 560, 15.5, GRAY, weight: .medium)

// =========================================================
// RIGHT: menu-bar + dropdown mock card
// =========================================================
let cardX: CGFloat = 704, cardTop: CGFloat = 96, cardW: CGFloat = 500, cardH: CGFloat = 404
let cardR: CGFloat = 18
// drop shadow
ctx.saveGState()
let sh = NSShadow(); sh.shadowColor = col("#000000", 0.55)
sh.shadowOffset = NSSize(width: 0, height: -P(10)); sh.shadowBlurRadius = P(30); sh.set()
roundRect(cardX, cardTop, cardW, cardH, cardR, fill: "#161b22")
ctx.restoreGState()
roundRect(cardX, cardTop, cardW, cardH, cardR, stroke: "#30363d", lw: 1)

// --- menu bar strip (top of card) ---
let stripH: CGFloat = 62
roundRect(cardX, cardTop, cardW, stripH, cardR, fill: "#1c2128")
// square off bottom corners of strip with a thin divider
col("#30363d").setStroke()
let div = NSBezierPath()
div.move(to: NSPoint(x: P(cardX), y: CGFloat(Hp) - P(cardTop + stripH)))
div.line(to: NSPoint(x: P(cardX + cardW), y: CGFloat(Hp) - P(cardTop + stripH)))
div.lineWidth = P(1); div.stroke()

// faint system glyphs on the left of the strip (vector: signal bars + battery)
let vc = cardTop + stripH/2            // vertical center of strip (top-coord)
col(GRAY, 0.65).setFill()
var bx = cardX + 26
let barBottom = vc + 6
for i in 0..<4 {                        // signal bars, bottom-aligned, increasing
    let bh = 4 + CGFloat(i) * 3
    NSBezierPath(roundedRect: rectTL(bx, barBottom - bh, 3, bh),
                 xRadius: P(0.8), yRadius: P(0.8)).fill()
    bx += 6
}
// battery
let batX = bx + 12, batTop = vc - 5.5, batW: CGFloat = 22, batH: CGFloat = 11
col(GRAY, 0.5).setStroke()
let batBody = NSBezierPath(roundedRect: rectTL(batX, batTop, batW, batH),
                           xRadius: P(2.5), yRadius: P(2.5))
batBody.lineWidth = P(1); batBody.stroke()
col(GRAY, 0.55).setFill()               // battery terminal
NSBezierPath(roundedRect: rectTL(batX + batW + 1, vc - 2.5, 2, 5),
             xRadius: P(1), yRadius: P(1)).fill()
NSBezierPath(roundedRect: rectTL(batX + 2, batTop + 2, batW*0.62, batH - 4),
             xRadius: P(1.2), yRadius: P(1.2)).fill()  // charge level
// clock on the far right
let clock = "6:34 PM"
let clockW = textW(clock, 14, weight: .medium)
text(clock, cardX + cardW - 20 - clockW, cardTop + 23, 14, "#adbac7", weight: .medium)

// ---- the chip (two colored rows), placed left of the clock ----
struct Row { let label, value, vcol, timer, tcol: String }
// 2x2 grid: left column Claude/Codex, right column Antigravity Gemini/External
let cols: [[Row]] = [
    [ Row(label: "Cld", value: "82%", vcol: ORANGE, timer: "1h 48m", tcol: ORANGE),
      Row(label: "Cdx", value: "55%", vcol: GREEN,  timer: "36m",    tcol: GREEN) ],
    [ Row(label: "AgG", value: "12%", vcol: GREEN,  timer: "1d 21h", tcol: GREEN),
      Row(label: "AgX", value: "71%", vcol: ORANGE, timer: "3d 1h",  tcol: ORANGE) ],
]
let cf: CGFloat = 13.5           // chip font pt
let spc: CGFloat = cf * 0.34
let colGap: CGFloat = cf * 0.9
let sepW = textW("·", cf, mono: true)
struct ColM { let lbl, val, tmr, w: CGFloat }
let metrics: [ColM] = cols.map { rows in
    let l = rows.map { textW($0.label, cf, weight: .medium, mono: true) }.max()!
    let v = rows.map { textW($0.value, cf, weight: .medium, mono: true) }.max()!
    let t = rows.map { textW($0.timer, cf, weight: .medium, mono: true) }.max()!
    return ColM(lbl: l, val: v, tmr: t, w: l + spc + v + spc + sepW + spc + t)
}
let chipW = metrics[0].w + colGap + metrics[1].w
let chipRight = cardX + cardW - 40 - clockW - 22
let chipX = chipRight - chipW
let rowH = cf + 4
let chipTop = cardTop + (stripH - rowH*2)/2
// selected-item highlight behind chip
roundRect(chipX - 10, chipTop - 4, chipW + 20, rowH*2 + 8, 6, fill: "#ffffff", fillAlpha: 0.08)
var colX = chipX
for (ci, rows) in cols.enumerated() {
    let m = metrics[ci]
    for (i, r) in rows.enumerated() {
        let ry = chipTop + CGFloat(i) * rowH
        let lx = colX
        let vx = lx + m.lbl + spc
        let sx = vx + m.val + spc
        let tx = sx + sepW + spc
        text(r.label, lx, ry, cf, WHITE, weight: .medium, mono: true)
        text(r.value, vx, ry, cf, r.vcol, weight: .medium, mono: true)
        text("·",     sx, ry, cf, GRAY,  weight: .medium, mono: true)
        text(r.timer, tx, ry, cf, r.tcol, weight: .medium, mono: true)
    }
    colX += m.w + colGap
}

// --- dropdown content (default text color; matches real design) ---
var dy = cardTop + stripH + 22
let dLX = cardX + 26
func windowLine(_ label: String, _ pct: String, _ reset: String, _ y: CGFloat, labelW: CGFloat = 74) {
    let mono = true
    let lw = text(label, dLX, y, 15.5, "#e6edf3", mono: mono)
    _ = lw
    let pctX = dLX + labelW
    text(pct, pctX, y, 15.5, "#e6edf3", weight: .medium, mono: mono)
    text("·  resets in " + reset, pctX + 52, y, 15.5, "#8b949e", mono: mono)
}
text("Claude", dLX, dy, 13.5, GRAY, weight: .semibold, tracking: 0.5); dy += 25
windowLine("5-hour", "82%", "1h 48m", dy); dy += 26
windowLine("Weekly", "44%", "4d 10h", dy); dy += 31
text("Codex", dLX, dy, 13.5, GRAY, weight: .semibold, tracking: 0.5); dy += 25
windowLine("5-hour", "55%", "36m", dy); dy += 26
windowLine("Weekly", "32%", "6d 3h", dy); dy += 31
text("Antigravity", dLX, dy, 13.5, GRAY, weight: .semibold, tracking: 0.5); dy += 25
windowLine("Gemini weekly", "12%", "1d 21h", dy, labelW: 150); dy += 26
windowLine("External weekly", "71%", "3d 1h", dy, labelW: 150); dy += 31
// separator
col("#30363d").setStroke()
let ln = NSBezierPath()
ln.move(to: NSPoint(x: P(dLX), y: CGFloat(Hp) - P(dy)))
ln.line(to: NSPoint(x: P(cardX + cardW - 26), y: CGFloat(Hp) - P(dy)))
ln.lineWidth = P(1); ln.stroke(); dy += 15
text("↻ 6:39:00 PM · next check (every 5m)", dLX, dy, 14, "#8b949e")

// =========================================================
NSGraphicsContext.restoreGraphicsState()
// keep native pixel size (2560x1280, 2:1) for the social preview
rep.size = NSSize(width: CGFloat(Wp), height: CGFloat(Hp))
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "social.png"
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
