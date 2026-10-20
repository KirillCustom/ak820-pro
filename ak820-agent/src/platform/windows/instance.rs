//! One instance, enforced by named mutexes the kernel releases on death.
//!
//! The plan's "ownership is enforced, not intended": a second daemon is
//! refused by a named mutex, and a lock file would not do — a killed process
//! leaves a lock file behind and a mutex behind nothing.
//!
//! The daemon holds **two** names. Its own, and the Python now-playing
//! agent's (`Global\ak820pro-nowplaying`, which `nowplaying-windows.py`'s
//! `single_instance()` checks before it starts). Holding the second means that
//! re-running the old PowerShell installer cannot put two media writers on
//! the board: the Python agent exits with "already running", which is true.
//! The Python timekeeper has no such guard; the clock's ownership is a matter
//! of which task is registered, and `ak820 clock` checks that instead.

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE};
use windows::Win32::System::Threading::CreateMutexW;

/// The daemon's own name.
pub const AGENT: &str = "ak820pro-agent";
/// The Python now-playing agent's name, held to fence it out.
pub const NOWPLAYING: &str = "ak820pro-nowplaying";

/// Held for the life of the process.
#[derive(Debug)]
pub struct Instance {
    handles: Vec<HANDLE>,
}

impl Instance {
    /// Claim every name, or say which one is already held. Names are placed
    /// in the `Global\` namespace, as the Python does, so a second logon
    /// session cannot start a second copy either.
    pub fn claim(names: &[&str]) -> Result<Instance, String> {
        let mut held = Instance { handles: Vec::new() };
        for name in names {
            let wide: Vec<u16> = format!("Global\\{name}")
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            let handle = unsafe { CreateMutexW(None, true, PCWSTR(wide.as_ptr())) }
                .map_err(|e| format!("mutex {name}: {e}"))?;
            // CreateMutexW succeeds on an existing mutex and says so here.
            if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
                unsafe { CloseHandle(handle).ok() };
                return Err(format!("{name} is already running (named mutex held)"));
            }
            held.handles.push(handle);
        }
        Ok(held)
    }
}

impl Drop for Instance {
    fn drop(&mut self) {
        for h in self.handles.drain(..) {
            unsafe { CloseHandle(h).ok() };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_name_can_be_held_once_and_is_released_on_drop() {
        let name = format!("ak820pro-test-{}", std::process::id());
        let first = Instance::claim(&[&name]).unwrap();
        let err = Instance::claim(&[&name]).unwrap_err();
        assert!(err.contains("already running"), "{err}");
        drop(first);
        Instance::claim(&[&name]).unwrap();
    }

    /// A partial claim releases what it took, so a refused start leaves no
    /// name behind.
    #[test]
    fn a_refused_claim_releases_the_names_it_took() {
        let a = format!("ak820pro-test-a-{}", std::process::id());
        let b = format!("ak820pro-test-b-{}", std::process::id());
        let holder = Instance::claim(&[&b]).unwrap();
        assert!(Instance::claim(&[&a, &b]).is_err());
        Instance::claim(&[&a]).expect("a was released when b was refused");
        drop(holder);
    }
}

/// The daemon's stop request: a named manual-reset event in the same
/// `Global\` namespace. `ak820 install` and `uninstall` signal it and give the
/// daemon a few seconds to put the lock lights back and exit before they end
/// its task (codex, lock-LED implementation verify, 1). Task Scheduler's own
/// "End" still kills the process outright.
pub const STOP: &str = "ak820pro-agent-stop";

pub struct StopEvent(windows::Win32::Foundation::HANDLE);

// A kernel handle, waited on from the daemon's loop only.
unsafe impl Send for StopEvent {}
unsafe impl Sync for StopEvent {}

fn global(name: &str) -> Vec<u16> {
    format!("Global\\{name}").encode_utf16().chain(std::iter::once(0)).collect()
}

impl StopEvent {
    /// The daemon's side: create it, not signalled.
    pub fn create() -> Option<StopEvent> {
        use windows::Win32::System::Threading::CreateEventW;
        let wide = global(STOP);
        unsafe { CreateEventW(None, true, false, PCWSTR(wide.as_ptr())) }.ok().map(StopEvent)
    }

    pub fn requested(&self) -> bool {
        use windows::Win32::Foundation::WAIT_OBJECT_0;
        use windows::Win32::System::Threading::WaitForSingleObject;
        unsafe { WaitForSingleObject(self.0, 0) == WAIT_OBJECT_0 }
    }
}

impl Drop for StopEvent {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.0).ok() };
    }
}

/// The CLI's side: signal a running daemon to stop. False when there is no
/// daemon to ask (no event), or it could not be signalled.
pub fn signal_stop() -> bool {
    use windows::Win32::System::Threading::{OpenEventW, SetEvent, EVENT_MODIFY_STATE};
    let wide = global(STOP);
    let Ok(h) = (unsafe { OpenEventW(EVENT_MODIFY_STATE, false, PCWSTR(wide.as_ptr())) }) else {
        return false;
    };
    let ok = unsafe { SetEvent(h) }.is_ok();
    unsafe { CloseHandle(h).ok() };
    ok
}
