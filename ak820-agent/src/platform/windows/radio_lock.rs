//! The radio's owner lock: one owner of the dongle per machine
//! (`plans/RAWHID-24G-DAEMON-PLAN.md`, R3-2).
//!
//! A byte-range lock on byte 0 of `%ProgramData%\ak820pro\raw81.lock`, a file
//! kept empty, taken without blocking. It is the lock `scripts/win_raw81_s2.py`
//! takes (`msvcrt.locking(.., LK_NBLCK, 1)` is `LockFile` on byte 0, and the
//! two conflict), so the daemon and a test run, or two users' daemons, cannot
//! interleave sessions. The kernel releases it when the handle closes,
//! including when the process dies: nothing is left behind to go stale.

use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, ERROR_LOCK_VIOLATION, GENERIC_READ, GENERIC_WRITE, HANDLE};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, LockFileEx, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
    LOCKFILE_EXCLUSIVE_LOCK, LOCKFILE_FAIL_IMMEDIATELY, OPEN_ALWAYS,
};
use windows::Win32::System::IO::OVERLAPPED;

/// Held until dropped.
pub struct RadioLock(HANDLE);

impl Drop for RadioLock {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.0).ok() };
    }
}

/// The lock file's path.
pub fn path() -> std::path::PathBuf {
    std::env::var_os("ProgramData")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| r"C:\ProgramData".into())
        .join("ak820pro")
        .join("raw81.lock")
}

/// Take it without blocking: `Ok(Some)` held, `Ok(None)` another owner holds
/// it, `Err` it could not be tried (the directory or file).
pub fn try_take() -> Result<Option<RadioLock>, String> {
    let p = path();
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    let wide: Vec<u16> = p.as_os_str().encode_wide_nul();
    let handle = unsafe {
        CreateFileW(
            PCWSTR(wide.as_ptr()),
            (GENERIC_READ | GENERIC_WRITE).0,
            // Shared: the Python opens it too (and truncates it, which is
            // harmless: the lock is on byte 0 whatever the length).
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
    }
    .map_err(|e| format!("{}: {e}", p.display()))?;
    let lock = RadioLock(handle);
    let mut ov = OVERLAPPED::default();
    match unsafe { LockFileEx(handle, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, None, 1, 0, &mut ov) } {
        Ok(()) => Ok(Some(lock)),
        Err(e) if e.code() == ERROR_LOCK_VIOLATION.to_hresult() => Ok(None),
        Err(e) => Err(format!("{}: {e}", p.display())),
    }
}

trait WideNul {
    fn encode_wide_nul(&self) -> Vec<u16>;
}

impl WideNul for std::ffi::OsStr {
    fn encode_wide_nul(&self) -> Vec<u16> {
        use std::os::windows::ffi::OsStrExt;
        self.encode_wide().chain(std::iter::once(0)).collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Two takes in one process: the second is refused while the first is
    /// held, and succeeds once it is dropped. (Byte-range locks are per
    /// handle, so this is the cross-process behaviour too.)
    #[test]
    fn the_lock_excludes_a_second_owner_until_dropped() {
        let Ok(Some(first)) = try_take() else {
            // Another owner (a test script, the daemon) holds it on this
            // machine right now: nothing to test without disturbing it.
            return;
        };
        assert!(matches!(try_take(), Ok(None)));
        drop(first);
        assert!(matches!(try_take(), Ok(Some(_))));
    }
}
