//! The lock-LED channel's Windows half: which keyboard-class interface to
//! write the board's LED report through (`plans/LOCK-LED-CHANNEL-PLAN.md`,
//! "The daemon's sender: design").
//!
//! The route, measured on this machine (`plans/GREMLIN-RESULTS.md`, sections 2,
//! 3 and 8): `IOCTL_KEYBOARD_SET_INDICATORS` on the keyboard-class interface,
//! opened with access 0, unprivileged, Num and Scroll only (P0), and Windows'
//! own lock state never moves. The interface comes from the Configuration
//! Manager's `GUID_DEVINTERFACE_KEYBOARD` list, which opens nothing: never HID
//! enumeration (the UPS incident, `path.rs`).
//!
//! Only the **Bluetooth** keyboard: over 2.4G the daemon carries now-playing
//! on raw HID already (`radio.rs`), and over the cable on raw HID.

/// The BLE keyboard's device id, the path's second field lower-cased, up to
/// the radio's address: `{00001812-...}` is the HID-over-GATT service, then the
/// AK820 Pro's Bluetooth VID and PID (`0C45:FEFE`, the source byte `01`).
const BLE_PREFIX: &str = "{00001812-0000-1000-8000-00805f9b34fb}_dev_vid&010c45_pid&fefe_";

/// The collection whose LEDs the board reads: `Col01` (measured 10-20; `Col05`
/// is the other keyboard-class collection of the same device).
const BLE_COLLECTION: &str = "&col01";

/// The class GUID every keyboard-class interface path ends with.
const KEYBOARD_CLASS: &str = "{884b96c3-56ef-11d1-bc8c-00a0c91405dd}";

#[derive(Debug, PartialEq, Eq)]
pub enum Pick {
    /// Exactly one: the board, paired over Bluetooth and present.
    One(String),
    /// Not present: not paired, or Windows has it disconnected.
    None,
    /// More than one, which a strict match should never see (two boards
    /// paired?): refused rather than guessed.
    Ambiguous(Vec<String>),
}

/// The board's BLE keyboard among the keyboard-class interface paths.
///
/// Strict, as `path.rs` is: the `\\?\HID` enumerator, four `#` fields, the
/// keyboard class, and the device id's exact prefix and collection.
pub fn pick(listed: &[String]) -> Pick {
    let mine: Vec<String> = listed
        .iter()
        .filter(|p| {
            let lower = p.to_ascii_lowercase();
            let f: Vec<&str> = lower.split('#').collect();
            f.len() == 4
                && f[0] == r"\\?\hid"
                && f[3] == KEYBOARD_CLASS
                && f[1].starts_with(BLE_PREFIX)
                && f[1].ends_with(BLE_COLLECTION)
        })
        .cloned()
        .collect();
    match mine.len() {
        0 => Pick::None,
        1 => Pick::One(mine.into_iter().next().unwrap()),
        _ => Pick::Ambiguous(mine),
    }
}

/// HID LED bits (Num `0x01`, Caps `0x02`, Scroll `0x04`) as the class
/// driver's `LedFlags` (Scroll `1`, Num `2`, Caps `4`).
pub fn led_flags(leds: u8) -> u16 {
    (((leds & 0x01) << 1) | ((leds & 0x02) << 1) | ((leds & 0x04) >> 2)) as u16
}

#[cfg(test)]
mod tests {
    use super::*;

    /// This machine's keyboard-class list, 10-20 (the dongle, a Saitek, a
    /// Logitech receiver, the board over BLE, the board over USB).
    fn gremlin() -> Vec<String> {
        [
            r"\\?\HID#VID_0C45&PID_FDFD&MI_00#e&3281215c&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#VID_0C45&PID_FDFD&MI_02&Col03#e&c20e8e0&0&0002#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#SaitekKeyboard#2&343dacb3&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#VID_046D&PID_C548&MI_00#e&ce1b8ac&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&010c45_PID&fefe_REV&0138_cc405fca52bc&Col01#f&e7e6082&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&010c45_PID&fefe_REV&0138_cc405fca52bc&Col05#f&e7e6082&0&0004#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#VID_0C45&PID_8009&MI_00#e&89883b4&0&0000#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
            r"\\?\HID#VID_0C45&PID_8009&MI_02&Col03#e&38836cba&0&0002#{884b96c3-56ef-11d1-bc8c-00a0c91405dd}",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect()
    }

    #[test]
    fn picks_the_ble_keyboards_col01_and_nothing_else() {
        let l = gremlin();
        assert_eq!(pick(&l), Pick::One(l[4].clone()));
    }

    #[test]
    fn absent_and_ambiguous() {
        let mut l = gremlin();
        l.remove(4);
        assert_eq!(pick(&l), Pick::None);
        let mut l = gremlin();
        let twin = l[4].replace("cc405fca52bc", "cc405fca52bd");
        l.push(twin);
        assert!(matches!(pick(&l), Pick::Ambiguous(v) if v.len() == 2));
    }

    #[test]
    fn a_wrong_class_or_shape_is_refused() {
        let ble = &gremlin()[4];
        let hid_class = ble.replace("{884b96c3-56ef-11d1-bc8c-00a0c91405dd}", "{4d1e55b2-f16f-11cf-88cb-001111000030}");
        let extra = format!("{ble}#x");
        assert_eq!(pick(&[hid_class, extra]), Pick::None);
    }

    #[test]
    fn led_flags_map_as_measured() {
        assert_eq!(led_flags(0x01), 2); // Num
        assert_eq!(led_flags(0x02), 4); // Caps
        assert_eq!(led_flags(0x04), 1); // Scroll
        assert_eq!(led_flags(0x07), 7);
        assert_eq!(led_flags(0xF8), 0); // bits 3-7 cannot be expressed
    }
}
