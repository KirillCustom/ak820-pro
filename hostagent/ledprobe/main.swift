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
//
// Logs to ~/Library/Logs/ak820pro/ledprobe.log. Started by `open` (so TCC sees
// this app, not whatever shell started it); built and signed by
// scripts/build-led-probe.sh.
import Foundation
import IOKit
import IOKit.hid

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

func ledElements(_ dev: IOHIDDevice) -> [UInt32: IOHIDElement] {
    let match = [kIOHIDElementUsagePageKey: kHIDPage_LEDs] as CFDictionary
    let all = IOHIDDeviceCopyMatchingElements(dev, match, IOOptionBits(kIOHIDOptionsTypeNone)) as? [IOHIDElement] ?? []
    var out: [UInt32: IOHIDElement] = [:]
    for e in all where IOHIDElementGetType(e) == kIOHIDElementTypeOutput {
        out[IOHIDElementGetUsage(e)] = e
    }
    return out
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

func openTarget(_ t: Target) -> (IOHIDDevice, [UInt32: IOHIDElement])? {
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
    let desc = leds.keys.sorted().map { u -> String in
        let e = leds[u]!
        return "\(ledNames[u] ?? "led\(u)")=\(lastValue(dev, e)) (report \(IOHIDElementGetReportID(e)))"
    }.joined(separator: " ")
    Probe.note("\(t.name): opened \(product) over \(transport); LED outputs: \(desc.isEmpty ? "none" : desc)")
    return (dev, leds)
}

func setScroll(_ t: Target, _ dev: IOHIDDevice, _ e: IOHIDElement, _ on: Bool) {
    let v = IOHIDValueCreateWithIntegerValue(kCFAllocatorDefault, e, mach_absolute_time(), on ? 1 : 0)
    let t0 = Date().timeIntervalSince1970
    let r = IOHIDDeviceSetValue(dev, e, v)
    let ms = (Date().timeIntervalSince1970 - t0) * 1000
    Probe.note(String(format: "%@: scroll %@ -> 0x%08x in %.1f ms", t.name, on ? "ON" : "off", r, ms))
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
    guard opened.count == 2, opened.allSatisfy({ $0.2[3] != nil }) else {
        Probe.note("run: need both devices open with a Scroll Lock element; stopping")
        exit(3)
    }
    let (usbT, usbDev, usbLeds) = opened[0]
    let (bleT, bleDev, bleLeds) = opened[1]
    Probe.note(String(format: "run: starting in %.0f s", delay))
    Thread.sleep(forTimeInterval: delay)
    setScroll(usbT, usbDev, usbLeds[3]!, true)
    Thread.sleep(forTimeInterval: 20)
    setScroll(usbT, usbDev, usbLeds[3]!, false)
    Thread.sleep(forTimeInterval: 10)
    for _ in 0..<6 {
        setScroll(bleT, bleDev, bleLeds[3]!, true)
        Thread.sleep(forTimeInterval: 5)
        setScroll(bleT, bleDev, bleLeds[3]!, false)
        Thread.sleep(forTimeInterval: 5)
    }
    for (_, dev, _) in opened { IOHIDDeviceClose(dev, IOOptionBits(kIOHIDOptionsTypeNone)) }
    Probe.note("run: done")
default:
    Probe.note("unknown mode \(mode); use check or run [delay_s]")
    exit(64)
}
