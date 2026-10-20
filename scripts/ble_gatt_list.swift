// The GATT services and characteristics a connected Bluetooth keyboard
// offers, by name: is there a vendor service beside HID-over-GATT (the
// question for raw HID over BLE, plans/RAWHID-24G-PLAN.md)?
//
//     scripts/build-ble-gatt.sh, then open -W --stdout LOG -a "$HOME/Applications/AK820 BLE Check.app" --args [name]
//     (default name "AK820"; run as a plain `swift` script, CoreBluetooth never answers)
//
// Read-only: it discovers services and characteristics, prints their
// properties and reads the Device Information strings (180A); it writes
// nothing and subscribes to nothing. ⚠️ Never write FEE0/FEE1: on this
// board's CH582F it is almost certainly WCH's OTA profile, the module's own
// firmware update (docs/wireless.md, "The GATT services over BLE").
// macOS asks once to allow Bluetooth for the app; it waits 3 min for that.
import CoreBluetooth
import Foundation

let want = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AK820"
// Services a keyboard's host connection is found by: HID, battery, device info.
let lookup = ["1812", "180F", "180A"].map { CBUUID(string: $0) }

func props(_ p: CBCharacteristicProperties) -> String {
    let names: [(CBCharacteristicProperties, String)] = [
        (.broadcast, "broadcast"), (.read, "read"), (.writeWithoutResponse, "write-no-rsp"),
        (.write, "write"), (.notify, "notify"), (.indicate, "indicate"),
        (.authenticatedSignedWrites, "signed-write"), (.extendedProperties, "extended"),
    ]
    return names.filter { p.contains($0.0) }.map { $0.1 }.joined(separator: ",")
}

final class Lister: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    var central: CBCentralManager!
    var target: CBPeripheral?
    var pending = 0
    var reads = 0

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn:
            let found = c.retrieveConnectedPeripherals(withServices: lookup)
            print("connected peripherals: " + found.map { $0.name ?? "(no name)" }.joined(separator: ", "))
            guard let p = found.first(where: { ($0.name ?? "").contains(want) }) else {
                print("none named *\(want)*"); exit(2)
            }
            target = p
            p.delegate = self
            c.connect(p)   // the system holds the link; this attaches the app to it
        case .unauthorized:
            print("Bluetooth not allowed for this app (System Settings > Privacy & Security > Bluetooth)"); exit(3)
        case .poweredOff:
            print("Bluetooth is off"); exit(3)
        default:
            print("Bluetooth state \(c.state.rawValue) (0 unknown, 1 resetting, 2 unsupported)")
        }
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        print("attached to \(p.name ?? "?") \(p.identifier)")
        p.discoverServices(nil)
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        print("attach failed: \(error?.localizedDescription ?? "?")"); exit(4)
    }

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        if let e = error { print("services: \(e.localizedDescription)"); exit(4) }
        let svcs = p.services ?? []
        print("\(svcs.count) services")
        pending = svcs.count
        if pending == 0 { finish() }
        for s in svcs { p.discoverCharacteristics(nil, for: s) }
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        print("service \(s.uuid.uuidString)  (\(s.uuid))\(s.isPrimary ? "" : " secondary")")
        if let e = error { print("  characteristics: \(e.localizedDescription)") }
        for ch in s.characteristics ?? [] {
            print("  char \(ch.uuid.uuidString)  (\(ch.uuid))  \(props(ch.properties))")
            if s.uuid == CBUUID(string: "180A") && ch.properties.contains(.read) {
                reads += 1
                p.readValue(for: ch)
            }
        }
        pending -= 1
        if pending == 0 && reads == 0 { finish() }
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        let v = ch.value ?? Data()
        let text = String(data: v, encoding: .utf8)?.trimmingCharacters(in: .controlCharacters) ?? ""
        let shown = !text.isEmpty && text.allSatisfy({ $0.isASCII && !$0.isNewline }) ? "\"\(text)\"" : v.map { String(format: "%02x", $0) }.joined()
        print("  value \(ch.uuid): \(error.map { "error \($0.localizedDescription)" } ?? shown)")
        reads -= 1
        if pending == 0 && reads == 0 { finish() }
    }

    func finish() {
        exit(0)   // no cancelPeripheralConnection: the keyboard's link is the system's
    }
}

// 0 not determined (a prompt is due), 1 restricted, 2 denied, 3 allowed.
print("Bluetooth authorization \(CBManager.authorization.rawValue)"); fflush(stdout)
let lister = Lister()
DispatchQueue.main.asyncAfter(deadline: .now() + 180) { print("timed out after 3 min"); exit(5) }
RunLoop.main.run()
