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

// ---------------------------------------------------------------------------
// The device
// ---------------------------------------------------------------------------

use std::time::Duration;

use windows::core::{HRESULT, PCWSTR, PWSTR};
use windows::Win32::Devices::DeviceAndDriverInstallation::{
    CM_GET_DEVICE_INTERFACE_LIST_PRESENT, CM_Get_Device_Interface_ListW,
    CM_Get_Device_Interface_List_SizeW, CR_BUFFER_SMALL, CR_SUCCESS,
};
use windows::Win32::Devices::HumanInterfaceDevice::{
    GUID_DEVINTERFACE_KEYBOARD, IOCTL_KEYBOARD_QUERY_INDICATORS, IOCTL_KEYBOARD_SET_INDICATORS,
    KEYBOARD_INDICATOR_PARAMETERS,
};
use windows::Win32::Foundation::{
    CloseHandle, ERROR_IO_PENDING, ERROR_OPERATION_ABORTED, HANDLE, WAIT_TIMEOUT,
};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_FLAG_OVERLAPPED, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
};
use windows::Win32::System::IO::{CancelIoEx, DeviceIoControl, GetOverlappedResultEx, OVERLAPPED};
use windows::Win32::System::RemoteDesktop::{
    WTSFreeMemory, WTSQuerySessionInformationW, WTSActive, WTSINFOEXW, WTSSessionInfoEx,
    WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, WTS_SESSIONSTATE_UNLOCK,
};
use windows::Win32::System::StationsAndDesktops::{
    CloseDesktop, GetThreadDesktop, GetUserObjectInformationW, OpenInputDesktop, DESKTOP_CONTROL_FLAGS,
    DESKTOP_READOBJECTS, HDESK, UOI_NAME,
};
use windows::Win32::System::Threading::{CreateEventW, GetCurrentThreadId, ResetEvent};
use windows::Win32::UI::Input::KeyboardAndMouse::{GetKeyState, VK_CAPITAL, VK_NUMLOCK, VK_SCROLL};

use super::path::split_multi_sz;
use crate::lockchan::frame::{LockLights, WriteError};
use crate::lockchan::Profile;

/// Each IOCTL's deadline, then the cancel's grace (R3-3).
const IOCTL_MS: u32 = 1000;
const CANCEL_GRACE_MS: u32 = 1000;
/// From one completion to the next write: 16 ms over BLE (`GREMLIN-RESULTS`
/// section 8: 6/6 down to 10 ms, the cliff at 7-10).
const SPACING: Duration = Duration::from_millis(16);

/// The Configuration Manager's keyboard-class list. Opens nothing.
pub fn list_keyboards() -> Result<Vec<String>, String> {
    for _ in 0..4 {
        let mut len: u32 = 0;
        let cr = unsafe {
            CM_Get_Device_Interface_List_SizeW(&mut len, &GUID_DEVINTERFACE_KEYBOARD, PCWSTR::null(), CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
        };
        if cr != CR_SUCCESS {
            return Err(format!("sizing the keyboard list failed (CONFIGRET {})", cr.0));
        }
        let mut buf = vec![0u16; len as usize];
        let cr = unsafe {
            CM_Get_Device_Interface_ListW(&GUID_DEVINTERFACE_KEYBOARD, PCWSTR::null(), &mut buf, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
        };
        if cr == CR_BUFFER_SMALL {
            continue;
        }
        if cr != CR_SUCCESS {
            return Err(format!("listing keyboards failed (CONFIGRET {})", cr.0));
        }
        return Ok(split_multi_sz(&buf));
    }
    Err("the keyboard list kept changing while it was being read".into())
}

/// The board's BLE keyboard, open for `SET_INDICATORS` (access 0: read is
/// refused, measured 10-19). Not `Sync`: one sender thread owns it.
pub struct BleLights {
    handle: HANDLE,
    event: HANDLE,
    unit: u16,
    stuck: bool,
}

// The handle and event move to the sender thread once and stay there.
unsafe impl Send for BleLights {}

/// An IOCTL's buffers, on the heap so a cancel that never lands can leave
/// them to the kernel for good (`device.rs`'s rule, finding 4 of 09-06).
struct Pending {
    ov: OVERLAPPED,
    params: KEYBOARD_INDICATOR_PARAMETERS,
}

impl BleLights {
    /// Open the board's BLE keyboard: `Ok(None)` when it is not listed.
    pub fn open() -> Result<Option<BleLights>, String> {
        let path = match pick(&list_keyboards()?) {
            Pick::One(p) => p,
            Pick::None => return Ok(None),
            Pick::Ambiguous(v) => return Err(format!("{} BLE AK820 keyboards listed: {v:?}", v.len())),
        };
        let wide: Vec<u16> = path.encode_utf16().chain(std::iter::once(0)).collect();
        let handle = unsafe {
            CreateFileW(
                PCWSTR(wide.as_ptr()),
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED,
                None,
            )
        }
        .map_err(|e| format!("open {path}: {e}"))?;
        let event = match unsafe { CreateEventW(None, true, false, PCWSTR::null()) } {
            Ok(e) => e,
            Err(e) => {
                unsafe { CloseHandle(handle).ok() };
                return Err(format!("event: {e}"));
            }
        };
        let mut me = BleLights { handle, event, unit: 0, stuck: false };
        // The unit id only: the flags it answers are stale (0 with Num on,
        // measured 10-19), so the user's state never comes from here.
        let q = me.ioctl(IOCTL_KEYBOARD_QUERY_INDICATORS, None).map_err(|e| format!("query: {e:?}"))?;
        me.unit = q.UnitId;
        Ok(Some(me))
    }

    /// One IOCTL with its deadline; `params` in for SET, the answer out for
    /// QUERY.
    fn ioctl(&mut self, code: u32, params: Option<KEYBOARD_INDICATOR_PARAMETERS>) -> Result<KEYBOARD_INDICATOR_PARAMETERS, WriteError> {
        if self.stuck {
            return Err(WriteError::Stuck);
        }
        let mut p = Box::new(Pending {
            ov: OVERLAPPED { hEvent: self.event, ..Default::default() },
            params: params.unwrap_or_default(),
        });
        unsafe {
            ResetEvent(self.event).map_err(|e| WriteError::Failed(format!("event reset: {e}")))?;
            let pp = &mut p.params as *mut KEYBOARD_INDICATOR_PARAMETERS as *mut core::ffi::c_void;
            let size = std::mem::size_of::<KEYBOARD_INDICATOR_PARAMETERS>() as u32;
            let (inp, outp) = if params.is_some() { (Some(pp as *const _), None) } else { (None, Some(pp)) };
            let started = DeviceIoControl(
                self.handle,
                code,
                inp,
                if params.is_some() { size } else { 0 },
                outp,
                if params.is_some() { 0 } else { size },
                None,
                Some(&mut p.ov),
            );
            if let Err(e) = started {
                if e.code() != HRESULT::from_win32(ERROR_IO_PENDING.0) {
                    return Err(WriteError::Failed(format!("{e}")));
                }
            }
            let mut moved = 0u32;
            match GetOverlappedResultEx(self.handle, &p.ov, &mut moved, IOCTL_MS, false) {
                Ok(()) => return Ok(p.params),
                Err(e) if e.code() == HRESULT::from_win32(WAIT_TIMEOUT.0) => {}
                Err(e) => return Err(WriteError::Failed(format!("{e}"))),
            }
            let _ = CancelIoEx(self.handle, Some(&p.ov));
            match GetOverlappedResultEx(self.handle, &p.ov, &mut moved, CANCEL_GRACE_MS, false) {
                Ok(()) => Ok(p.params),
                Err(e) if e.code() == HRESULT::from_win32(ERROR_OPERATION_ABORTED.0) => {
                    Err(WriteError::Failed("timed out (cancelled)".into()))
                }
                Err(e) if e.code() == HRESULT::from_win32(WAIT_TIMEOUT.0) => {
                    // The kernel still owns the buffers: theirs for good.
                    self.stuck = true;
                    Box::leak(p);
                    Err(WriteError::Stuck)
                }
                Err(e) => Err(WriteError::Failed(format!("{e}"))),
            }
        }
    }
}

impl Drop for BleLights {
    fn drop(&mut self) {
        // A stuck IOCTL still points at the event: leak both (device.rs).
        if self.stuck {
            return;
        }
        unsafe {
            CloseHandle(self.event).ok();
            CloseHandle(self.handle).ok();
        }
    }
}

/// The session is active and unlocked (R4-1).
fn session_ok() -> bool {
    let mut buf = PWSTR::null();
    let mut len = 0u32;
    let ok = unsafe {
        WTSQuerySessionInformationW(Some(WTS_CURRENT_SERVER_HANDLE), WTS_CURRENT_SESSION, WTSSessionInfoEx, &mut buf, &mut len)
    }
    .is_ok();
    if !ok || buf.is_null() {
        return false;
    }
    let result = if (len as usize) >= std::mem::size_of::<WTSINFOEXW>() {
        let info = unsafe { &*(buf.0 as *const WTSINFOEXW) };
        info.Level == 1 && {
            let l1 = unsafe { info.Data.WTSInfoExLevel1 };
            l1.SessionState == WTSActive && l1.SessionFlags as u32 == WTS_SESSIONSTATE_UNLOCK
        }
    } else {
        false
    };
    unsafe { WTSFreeMemory(buf.0 as *mut _) };
    result
}

fn desktop_name(d: HDESK) -> Option<String> {
    let mut buf = [0u16; 64];
    let mut need = 0u32;
    unsafe {
        GetUserObjectInformationW(
            HANDLE(d.0),
            UOI_NAME,
            Some(buf.as_mut_ptr() as *mut _),
            (buf.len() * 2) as u32,
            Some(&mut need),
        )
    }
    .ok()?;
    let n = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    Some(String::from_utf16_lossy(&buf[..n]))
}

/// The input desktop is this thread's (R4-1): `Winlogon` while locked or at
/// a secure prompt fails it.
fn desktop_ok() -> bool {
    let Ok(input) = (unsafe { OpenInputDesktop(DESKTOP_CONTROL_FLAGS(0), false, DESKTOP_READOBJECTS) }) else {
        return false;
    };
    let mine = unsafe { GetThreadDesktop(GetCurrentThreadId()) }.ok().and_then(desktop_name);
    let theirs = desktop_name(input);
    unsafe { CloseDesktop(input).ok() };
    matches!((mine, theirs), (Some(a), Some(b)) if a == b)
}

/// The user's locks as HID LED bits, if they can be known (R4-1).
pub fn user_locks() -> Option<u8> {
    if !session_ok() || !desktop_ok() {
        return None;
    }
    let on = |vk: windows::Win32::UI::Input::KeyboardAndMouse::VIRTUAL_KEY| (unsafe { GetKeyState(vk.0 as i32) } & 1) as u8;
    Some(on(VK_NUMLOCK) | on(VK_CAPITAL) << 1 | on(VK_SCROLL) << 2)
}

impl LockLights for BleLights {
    fn profile(&self) -> Profile {
        Profile::P0
    }
    fn spacing(&self) -> Duration {
        SPACING
    }
    fn user(&mut self) -> Option<u8> {
        user_locks()
    }
    fn write(&mut self, leds: u8) -> Result<(), WriteError> {
        if leds & !0x07 != 0 {
            return Err(WriteError::Failed(format!("0x{leds:02x}: Windows carries Num, Caps and Scroll only")));
        }
        let p = KEYBOARD_INDICATOR_PARAMETERS { UnitId: self.unit, LedFlags: led_flags(leds) };
        self.ioctl(IOCTL_KEYBOARD_SET_INDICATORS, Some(p)).map(|_| ())
    }
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
