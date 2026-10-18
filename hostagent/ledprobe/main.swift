// AK820 LED Probe: can this Mac set one Bluetooth keyboard's Scroll Lock light,
// and does the board receive it? The first step of the lock-LED data channel
// (plans/BACKLOG.md, "Community", quill4gen7 item 1): over the air the only
// thing the CH582F forwards from host to board is the LED bitmap (`5A <leds>`).
//
// The receiver is the board's own LCD: the lock band shows "SCR" while the host
// reports Scroll Lock, and macOS never sets it on its own. No firmware change.
//
// It opens exactly two HID devices, each matched on vendor, product, transport
// and the keyboard collection (usage page 1, usage 6), never the raw-HID one
// (0xFF60), which the bench's hidapi tools hold exclusively; and it never
// seizes them, so typing is untouched. It writes only the Scroll Lock element.
//
//   check            (the default) wait for Input Monitoring, then log each
//                    target's LED elements and their last values; writes nothing
//   run [delay_s]    after the delay: the USB keyboard gets Scroll Lock held ON
//                    for 20 s (the control: in BT mode the board takes its lock
//                    lights from the radio only, so this should show nothing),
//                    then a 10 s gap, then the Bluetooth keyboard gets Scroll
//                    Lock blinking 5 s on / 5 s off, six times. A steady SCR
//                    means the USB report got through; a blinking one, the
//                    Bluetooth report.
//   pace [n] [delay_s]  n back-to-back Scroll Lock writes over BLE (default
//                    40), each timed; the summary says how fast they complete
//   hold [s] [delay_s]  Scroll Lock on over BLE for s seconds (default 30), then off
//   night [delay_s]  unattended, over BLE: repeated values, the two unnamed
//                    LED bits, and 200 writes issued without waiting, all timed
//   seq gap_ms v...  the LED bytes v (decimal or 0x hex, 0-255) over BLE in
//                    order, each waiting for the last and then gap_ms more (0:
//                    back to back); for scripts/ledchannel_test.py, which reads
//                    what the board received (flash 2g's 5A trace)
//   aseq v...        the same bytes issued at once without waiting
//                    (IOHIDDeviceSetReportWithCallback): the fastest this Mac
//                    can send, since a synchronous write waits ~30 ms for the link
//   capsmix          bits 3-4 set over BLE (0x18), then this Mac's own Caps Lock
//                    switched on and off in software, as a key press would, then
//                    0x00: does the LED report the Mac sends for Caps keep bits
//                    3-4? Refuses if Caps Lock is already on; leaves it off
//
// Logs to ~/Library/Logs/ak820pro/ledprobe.log. Started by `open` (so TCC sees
// this app, not whatever shell started it); built and signed by
// scripts/build-led-probe.sh.
import Foundation
import IOKit
import IOKit.hid
import IOKit.hidsystem

enum Probe {
    static let out: FileHandle = {
        let dir = (NSHomeDirectory() as NSString).appendingPathComponent("Library/Logs/ak820pro")
        let path = (dir as NSString).appendingPathComponent("ledprobe.log")
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil)
        }
        guard let h = FileHandle(forWritingAtPath: path) else { exit(1) }
        h.seekToEndOfFile()
        return h
    }()

    static func note(_ s: String) {
        let line = String(format: "%.3f ", Date().timeIntervalSince1970) + s + "\n"
        out.write(line.data(using: .utf8)!)
    }
}

struct Target {
    let name: String
    let props: [String: Any]
}

let targets = [
    Target(name: "usb", props: [kIOHIDVendorIDKey: 0x0C45, kIOHIDProductIDKey: 0x8009,
                                kIOHIDTransportKey: "USB",
                                kIOHIDPrimaryUsagePageKey: 1, kIOHIDPrimaryUsageKey: 6]),
    Target(name: "ble", props: [kIOHIDVendorIDKey: 0x0C45, kIOHIDProductIDKey: 0xFEFE,
                                kIOHIDTransportKey: "Bluetooth Low Energy",
                                kIOHIDPrimaryUsagePageKey: 1, kIOHIDPrimaryUsageKey: 6]),
]

let ledNames: [UInt32: String] = [1: "num", 2: "caps", 3: "scroll", 4: "compose", 5: "kana"]

// The registry, not an IOHIDManager: a property match finds the one service
// without opening anything else on the machine.
func findDevice(_ t: Target) -> IOHIDDevice? {
    let m = IOServiceMatching(kIOHIDDeviceKey) as NSMutableDictionary
    m[kIOPropertyMatchKey] = t.props
    var it: io_iterator_t = 0
    guard IOServiceGetMatchingServices(kIOMainPortDefault, m, &it) == KERN_SUCCESS else { return nil }
    defer { IOObjectRelease(it) }
    var found: [io_service_t] = []
    while case let s = IOIteratorNext(it), s != 0 { found.append(s) }
    defer { found.forEach { IOObjectRelease($0) } }
    guard found.count == 1 else {
        Probe.note("\(t.name): \(found.count) matching devices, expected 1; not touching any")
        return nil
    }
    return IOHIDDeviceCreate(kCFAllocatorDefault, found[0])
}

// Every LED output element, in descriptor order. Not keyed by usage: the BLE
// descriptor declares five bits against usages 1-3, so the last usage
// (Scroll Lock) repeats, and a dictionary would keep the wrong bit.
func ledElements(_ dev: IOHIDDevice) -> [IOHIDElement] {
    let match = [kIOHIDElementUsagePageKey: kHIDPage_LEDs] as CFDictionary
    let all = IOHIDDeviceCopyMatchingElements(dev, match, IOOptionBits(kIOHIDOptionsTypeNone)) as? [IOHIDElement] ?? []
    return all.filter { IOHIDElementGetType($0) == kIOHIDElementTypeOutput }
              .sorted { IOHIDElementGetCookie($0) < IOHIDElementGetCookie($1) }
}

// The last value set, from the cache: kIOHIDDeviceGetValueWithoutUpdate
// (0x00040000; IOHIDDevice.h, not imported into Swift) never sends the device a request.
func lastValue(_ dev: IOHIDDevice, _ e: IOHIDElement) -> String {
    let placeholder = IOHIDValueCreateWithIntegerValue(kCFAllocatorDefault, e, 0, 0)
    var v = Unmanaged.passUnretained(placeholder)
    let r = IOHIDDeviceGetValueWithOptions(dev, e, &v, 0x00040000)
    return r == kIOReturnSuccess ? String(IOHIDValueGetIntegerValue(v.takeUnretainedValue()))
                                 : String(format: "? (0x%08x)", r)
}

func openTarget(_ t: Target) -> (IOHIDDevice, Int)? {
    guard let dev = findDevice(t) else {
        Probe.note("\(t.name): device not found")
        return nil
    }
    let product = IOHIDDeviceGetProperty(dev, kIOHIDProductKey as CFString) as? String ?? "?"
    let transport = IOHIDDeviceGetProperty(dev, kIOHIDTransportKey as CFString) as? String ?? "?"
    let r = IOHIDDeviceOpen(dev, IOOptionBits(kIOHIDOptionsTypeNone))
    guard r == kIOReturnSuccess else {
        Probe.note(String(format: "%@: open failed 0x%08x (%@, %@)", t.name, r, product, transport))
        return nil
    }
    let leds = ledElements(dev)
    let desc = leds.map { e -> String in
        let u = IOHIDElementGetUsage(e)
        return "\(ledNames[u] ?? "led\(u)")#\(IOHIDElementGetCookie(e))=\(lastValue(dev, e))"
    }.joined(separator: " ")
    let reportID = leds.first.map { Int(IOHIDElementGetReportID($0)) } ?? -1
    Probe.note("\(t.name): opened \(product) over \(transport); LED outputs (report \(reportID)): \(desc.isEmpty ? "none" : desc)")
    return (dev, reportID)
}

// The whole LED byte as a raw output report, so the bit is exact: Scroll Lock
// is bit 2 (0x04) in both descriptors. Caps goes out as 0, so leave Caps off.
// With a report ID, macOS takes the ID as the buffer's first byte.
@discardableResult
func setLeds(_ t: Target, _ dev: IOHIDDevice, _ reportID: Int, _ leds: UInt8, _ what: String) -> Double {
    let buf: [UInt8] = reportID == 0 ? [leds] : [UInt8(reportID), leds]
    let t0 = Date().timeIntervalSince1970
    let r = IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, CFIndex(reportID), buf, buf.count)
    let ms = (Date().timeIntervalSince1970 - t0) * 1000
    Probe.note(String(format: "%@: %@ (report %d, 0x%02x) -> 0x%08x in %.1f ms",
                      t.name, what, reportID, leds, r, ms))
    return ms
}

@discardableResult
func setScroll(_ t: Target, _ dev: IOHIDDevice, _ reportID: Int, _ on: Bool) -> Double {
    setLeds(t, dev, reportID, on ? 0x04 : 0x00, on ? "scroll ON" : "scroll off")
}

func median(_ xs: [Double]) -> Double {
    let s = xs.sorted()
    return s.isEmpty ? .nan : s.count % 2 == 1 ? s[s.count / 2] : (s[s.count / 2 - 1] + s[s.count / 2]) / 2
}

// Does macOS send a write that changes nothing? Timed against writes that do:
// ~0 ms suggests it was dropped on the Mac, ~30 ms that it crossed the link.
// Neither says what the radio chip did with it.
func repeatTest(_ dev: IOHIDDevice, _ id: Int) {
    let t = targets[1]
    setLeds(t, dev, id, 0x04, "warm-up"); setLeds(t, dev, id, 0x00, "warm-up")
    var changes: [Double] = [], repeats: [Double] = []
    for _ in 0..<3 {
        for v: UInt8 in [0x04, 0x00] {
            changes.append(setLeds(t, dev, id, v, "change"))
            for _ in 0..<3 { repeats.append(setLeds(t, dev, id, v, "repeat")) }
        }
    }
    Probe.note(String(format: "repeat: %d changes median %.1f ms, %d repeats median %.1f ms (min %.1f, max %.1f)",
                      changes.count, median(changes), repeats.count, median(repeats),
                      repeats.min() ?? .nan, repeats.max() ?? .nan))
}

// The descriptor's two unnamed LED bits (3 and 4): does macOS take and send
// them? Timed against Scroll Lock (bit 2). Each value is followed by 0x00.
func bitsTest(_ dev: IOHIDDevice, _ id: Int) {
    let t = targets[1]
    setLeds(t, dev, id, 0x04, "warm-up"); setLeds(t, dev, id, 0x00, "warm-up")
    var by: [UInt8: [Double]] = [:]
    for _ in 0..<3 {
        for v: UInt8 in [0x04, 0x08, 0x10, 0x18] {
            by[v, default: []].append(setLeds(t, dev, id, v, "bits"))
            setLeds(t, dev, id, 0x00, "bits clear")
        }
    }
    let line = by.keys.sorted().map { String(format: "0x%02x %.1f ms", $0, median(by[$0]!)) }.joined(separator: ", ")
    Probe.note("bits: medians \(line)")
}

// Writes issued without waiting for each to finish: does the link take more
// than one per ~30 ms? Completion times come from the callbacks.
enum Async {
    static var t0 = 0.0
    static var done: [Double] = []
    static var codes: [IOReturn: Int] = [:]
}
let asyncDone: IOHIDReportCallback = { _, result, _, _, _, _, _ in
    Async.done.append((Date().timeIntervalSince1970 - Async.t0) * 1000)
    Async.codes[result, default: 0] += 1
}

func asyncTest(_ dev: IOHIDDevice, _ id: Int, _ n: Int) {
    let t = targets[1]
    let n = n % 2 == 0 ? n : n + 1   // even, so the last write is 0x00
    setLeds(t, dev, id, 0x04, "warm-up"); setLeds(t, dev, id, 0x00, "warm-up")
    IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
    var bufs: [UnsafeMutablePointer<UInt8>] = []
    var issued = 0
    var refused: [IOReturn: Int] = [:]
    Async.t0 = Date().timeIntervalSince1970
    for i in 0..<n {
        let b = UnsafeMutablePointer<UInt8>.allocate(capacity: 2)   // must outlive the write
        b[0] = UInt8(id); b[1] = i % 2 == 0 ? 0x04 : 0x00
        bufs.append(b)
        let r = IOHIDDeviceSetReportWithCallback(dev, kIOHIDReportTypeOutput, CFIndex(id), b, 2, 5.0, asyncDone, nil)
        if r == kIOReturnSuccess { issued += 1 } else { refused[r, default: 0] += 1 }
    }
    let issueMs = (Date().timeIntervalSince1970 - Async.t0) * 1000
    let deadline = Date().addingTimeInterval(20)
    while Async.done.count < issued && Date() < deadline {
        CFRunLoopRunInMode(.defaultMode, 0.05, true)
    }
    IOHIDDeviceUnscheduleFromRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
    let last = Async.done.max() ?? .nan
    let codes = Async.codes.map { String(format: "0x%08x x%d", $0.key, $0.value) }.joined(separator: ", ")
    let refusedText = refused.map { String(format: "0x%08x x%d", $0.key, $0.value) }.joined(separator: ", ")
    Probe.note(String(format: "async: %d issued in %.1f ms (refused: %@); %d completed, the last at %.1f ms (%.1f/s); results: %@",
                      issued, issueMs, refusedText.isEmpty ? "none" : refusedText, Async.done.count, last,
                      Double(Async.done.count) / (last / 1000), codes.isEmpty ? "none" : codes))
    if Async.done.count < issued { setLeds(t, dev, id, 0x00, "clear after timeout") }
    // Freed only once every callback is in: a late one would read freed memory.
    if Async.done.count == issued { bufs.forEach { $0.deallocate() } }
}

func waitForAccess() {
    if IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) == kIOHIDAccessTypeGranted { return }
    _ = IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)
    Probe.note("waiting for Input Monitoring: grant it to AK820 LED Probe (pid \(getpid()))")
    for _ in 0..<(8 * 3600 / 2) {
        Thread.sleep(forTimeInterval: 2)
        if IOHIDCheckAccess(kIOHIDRequestTypeListenEvent) == kIOHIDAccessTypeGranted {
            Probe.note("Input Monitoring granted")
            return
        }
    }
    Probe.note("no Input Monitoring after 8 h; giving up")
    exit(2)
}

let args = Array(CommandLine.arguments.dropFirst())
let mode = args.first ?? "check"
Probe.note("start: \(mode) (pid \(getpid()))")
waitForAccess()

switch mode {
case "check":
    for t in targets {
        if let (dev, _) = openTarget(t) { IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone)) }
    }
case "run":
    let delay = args.count > 1 ? Double(args[1]) ?? 0 : 0
    let opened = targets.compactMap { t in openTarget(t).map { (t, $0.0, $0.1) } }
    guard opened.count == 2, opened[0].2 == 0, opened[1].2 == 2 else {
        Probe.note("run: need both devices open, USB LEDs in report 0 and BLE in report 2; stopping")
        exit(3)
    }
    let (usbT, usbDev, usbID) = opened[0]
    let (bleT, bleDev, bleID) = opened[1]
    Probe.note(String(format: "run: starting in %.0f s", delay))
    Thread.sleep(forTimeInterval: delay)
    setScroll(usbT, usbDev, usbID, true)
    Thread.sleep(forTimeInterval: 20)
    setScroll(usbT, usbDev, usbID, false)
    Thread.sleep(forTimeInterval: 10)
    for _ in 0..<6 {
        setScroll(bleT, bleDev, bleID, true)
        Thread.sleep(forTimeInterval: 5)
        setScroll(bleT, bleDev, bleID, false)
        Thread.sleep(forTimeInterval: 5)
    }
    for (_, dev, _) in opened { IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone)) }
    Probe.note("run: done")
case "pace":
    // How fast back-to-back writes complete: N writes over BLE, Scroll Lock
    // alternating, each waiting for the last. 5 s apart (run) each one may
    // pay the idle link's wake-up; back to back, only the first should.
    let n = args.count > 1 ? Int(args[1]) ?? 40 : 40
    let delay = args.count > 2 ? Double(args[2]) ?? 0 : 0
    guard let (dev, id) = openTarget(targets[1]), id == 2 else {
        Probe.note("pace: need the BLE device open with LEDs in report 2; stopping")
        exit(3)
    }
    Probe.note(String(format: "pace: %d writes, starting in %.0f s", n, delay))
    Thread.sleep(forTimeInterval: delay)
    let t0 = Date().timeIntervalSince1970
    var ms: [Double] = []
    for i in 0..<n { ms.append(setScroll(targets[1], dev, id, i % 2 == 0)) }
    if n % 2 == 1 { setScroll(targets[1], dev, id, false) }
    let total = Date().timeIntervalSince1970 - t0
    let sorted = ms.sorted()
    Probe.note(String(format: "pace: %d writes in %.2f s (%.1f/s): first %.1f, min %.1f, median %.1f, max %.1f ms",
                      n, total, Double(n) / total, ms[0], sorted[0], sorted[n / 2], sorted[n - 1]))
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
case "hold":
    // Scroll Lock on over BLE for a while, then off: does the Mac's own LED
    // report on a Caps Lock press wipe it? (JD presses Caps twice meanwhile.)
    let secs = args.count > 1 ? Double(args[1]) ?? 30 : 30
    let delay = args.count > 2 ? Double(args[2]) ?? 0 : 0
    guard let (dev, id) = openTarget(targets[1]), id == 2 else {
        Probe.note("hold: need the BLE device open with LEDs in report 2; stopping")
        exit(3)
    }
    Probe.note(String(format: "hold: %.0f s, starting in %.0f s", secs, delay))
    Thread.sleep(forTimeInterval: delay)
    setScroll(targets[1], dev, id, true)
    Thread.sleep(forTimeInterval: secs)
    setScroll(targets[1], dev, id, false)
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
    Probe.note("hold: done")
case "night":
    // The three tests that need nobody at the board, 10 s apart.
    let delay = args.count > 1 ? Double(args[1]) ?? 0 : 0
    guard let (dev, id) = openTarget(targets[1]), id == 2 else {
        Probe.note("night: need the BLE device open with LEDs in report 2; stopping")
        exit(3)
    }
    Probe.note(String(format: "night: starting in %.0f s", delay))
    Thread.sleep(forTimeInterval: delay)
    repeatTest(dev, id)
    Thread.sleep(forTimeInterval: 10)
    bitsTest(dev, id)
    Thread.sleep(forTimeInterval: 10)
    asyncTest(dev, id, 200)
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
    Probe.note("night: done")
case "seq":
    // A sequence, for comparison against the board's 5A trace. Every write is
    // logged ("seq: ... -> result in ms") with its completion time.
    let gap = args.count > 1 ? Double(args[1]) ?? -1 : -1
    let vals: [UInt8] = args.dropFirst(2).compactMap { a in
        let v = a.hasPrefix("0x") ? Int(a.dropFirst(2), radix: 16) : Int(a)
        return v.flatMap { (0...255).contains($0) ? UInt8($0) : nil }
    }
    guard gap >= 0, !vals.isEmpty, vals.count == args.count - 2 else {
        Probe.note("seq: usage: seq gap_ms v... (each v 0-255, decimal or 0x hex); stopping")
        exit(64)
    }
    guard let (dev, id) = openTarget(targets[1]), id == 2 else {
        Probe.note("seq: need the BLE device open with LEDs in report 2; stopping")
        exit(3)
    }
    Probe.note(String(format: "seq: %d writes, gap %.0f ms", vals.count, gap))
    let t0 = Date().timeIntervalSince1970
    var ms: [Double] = []
    for (i, v) in vals.enumerated() {
        ms.append(setLeds(targets[1], dev, id, v, "seq \(i)"))
        if gap > 0 { Thread.sleep(forTimeInterval: gap / 1000) }
    }
    let total = Date().timeIntervalSince1970 - t0
    Probe.note(String(format: "seq: done, %d writes in %.2f s, median %.1f ms each", vals.count, total, median(ms)))
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
case "aseq":
    // Issued in order without waiting; each completion is logged with its
    // index ("aseq i (0xVV) -> result at T ms" from the first issue).
    let vals: [UInt8] = args.dropFirst().compactMap { a in
        let v = a.hasPrefix("0x") ? Int(a.dropFirst(2), radix: 16) : Int(a)
        return v.flatMap { (0...255).contains($0) ? UInt8($0) : nil }
    }
    guard !vals.isEmpty, vals.count == args.count - 1 else {
        Probe.note("aseq: usage: aseq v... (each v 0-255, decimal or 0x hex); stopping")
        exit(64)
    }
    guard let (dev, id) = openTarget(targets[1]), id == 2 else {
        Probe.note("aseq: need the BLE device open with LEDs in report 2; stopping")
        exit(3)
    }
    let cb: IOHIDReportCallback = { ctx, result, _, _, _, report, _ in
        let i = Int(bitPattern: ctx) - 1
        let at = (Date().timeIntervalSince1970 - Async.t0) * 1000
        Async.done.append(at)
        Probe.note(String(format: "aseq %d (0x%02x) -> 0x%08x at %.1f ms", i, report[1], result, at))
    }
    IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
    var bufs: [UnsafeMutablePointer<UInt8>] = []
    var issued = 0
    Probe.note("aseq: \(vals.count) writes")
    Async.t0 = Date().timeIntervalSince1970
    for (i, v) in vals.enumerated() {
        let b = UnsafeMutablePointer<UInt8>.allocate(capacity: 2)   // must outlive the write
        b[0] = UInt8(id); b[1] = v
        bufs.append(b)
        let r = IOHIDDeviceSetReportWithCallback(dev, kIOHIDReportTypeOutput, CFIndex(id), b, 2, 5.0, cb,
                                                 UnsafeMutableRawPointer(bitPattern: i + 1))
        if r == kIOReturnSuccess { issued += 1 } else { Probe.note(String(format: "aseq %d refused: 0x%08x", i, r)) }
    }
    let deadline = Date().addingTimeInterval(20)
    while Async.done.count < issued && Date() < deadline { CFRunLoopRunInMode(.defaultMode, 0.05, true) }
    IOHIDDeviceUnscheduleFromRunLoop(dev, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
    Probe.note(String(format: "aseq: done, %d of %d issued, %d completed, the last at %.1f ms",
                      issued, vals.count, Async.done.count, Async.done.max() ?? .nan))
    // Freed only once every callback is in: a late one would read freed memory.
    if Async.done.count == issued { bufs.forEach { $0.deallocate() } }
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
case "capsmix":
    // The Mac's own Caps Lock, set through the HID system as a key press sets
    // it: the Mac then sends the keyboard its LED report. Does it carry bits
    // 3-4, which it has no elements for, or zero them?
    var conn: io_connect_t = 0
    let svc = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching(kIOHIDSystemClass))
    guard IOServiceOpen(svc, mach_task_self_, UInt32(kIOHIDParamConnectType), &conn) == kIOReturnSuccess else {
        Probe.note("capsmix: cannot open the HID system; stopping")
        exit(3)
    }
    var caps = false
    guard IOHIDGetModifierLockState(conn, Int32(kIOHIDCapsLockState), &caps) == kIOReturnSuccess, !caps else {
        Probe.note("capsmix: Caps Lock is on, or unreadable; stopping")
        exit(3)
    }
    guard let (dev, id) = openTarget(targets[1]), id == 2 else {
        Probe.note("capsmix: need the BLE device open with LEDs in report 2; stopping")
        exit(3)
    }
    func setCaps(_ on: Bool) {
        let r = IOHIDSetModifierLockState(conn, Int32(kIOHIDCapsLockState), on)
        Probe.note(String(format: "capsmix: Mac Caps Lock %@ -> 0x%08x", on ? "ON" : "off", r))
    }
    setLeds(targets[1], dev, id, 0x18, "capsmix bits 3-4")
    Thread.sleep(forTimeInterval: 0.4)
    setCaps(true)
    Thread.sleep(forTimeInterval: 0.6)
    setCaps(false)
    Thread.sleep(forTimeInterval: 0.6)
    setLeds(targets[1], dev, id, 0x00, "capsmix clear")
    if IOHIDGetModifierLockState(conn, Int32(kIOHIDCapsLockState), &caps) != kIOReturnSuccess || caps { setCaps(false) }
    IOServiceClose(conn)
    IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone))
    Probe.note("capsmix: done")
default:
    Probe.note("unknown mode \(mode); use check, run, pace, hold, night, seq, aseq or capsmix")
    exit(64)
}
