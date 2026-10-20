//! The radio's owner lock: one owner of the dongle per machine
//! (`plans/RAWHID-24G-DAEMON-PLAN.md`, R3-2).
//!
//! An exclusive `flock` on `/Users/Shared/ak820pro/raw81.lock`, a file kept
//! empty, taken without blocking. It is the lock `scripts/mac_raw81_s2.py`
//! takes (`fcntl.flock(.., LOCK_EX | LOCK_NB)`), so the daemon and a test run,
//! or two users' daemons, cannot interleave sessions. `flock`, not a POSIX
//! record lock: it belongs to the open file, so nothing else in this process
//! closing that file can drop it. The kernel releases it when the file closes,
//! including when the process dies: nothing is left behind to go stale.

use std::fs::{File, OpenOptions, TryLockError};
use std::io::ErrorKind;
use std::os::unix::fs::OpenOptionsExt;
use std::path::PathBuf;

/// Held until dropped.
pub struct RadioLock(#[allow(dead_code)] File);

/// The lock file's path: under `/Users/Shared`, which every user can reach.
pub fn path() -> PathBuf {
    PathBuf::from("/Users/Shared/ak820pro/raw81.lock")
}

/// Take it without blocking: `Ok(Some)` held, `Ok(None)` another owner holds
/// it, `Err` it could not be tried (the directory or file).
pub fn try_take() -> Result<Option<RadioLock>, String> {
    let p = path();
    if let Some(dir) = p.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    }
    // Read access is all flock needs, and all a second user has on a file
    // another user created; so open it read-only when it is there, and
    // create it (readable and writable by all, as the umask allows) only
    // when it is not. Ours never writes it.
    let file = match File::open(&p) {
        Ok(f) => f,
        Err(e) if e.kind() == ErrorKind::NotFound => OpenOptions::new()
            .read(true)
            .append(true)
            .create(true)
            .mode(0o666)
            .open(&p)
            .map_err(|e| format!("{}: {e}", p.display()))?,
        Err(e) => return Err(format!("{}: {e}", p.display())),
    };
    match file.try_lock() {
        Ok(()) => Ok(Some(RadioLock(file))),
        Err(TryLockError::WouldBlock) => Ok(None),
        Err(TryLockError::Error(e)) => Err(format!("{}: {e}", p.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Two takes in one process conflict, as two processes' do: flock is per
    /// open file, so the second open's lock is refused while the first holds.
    #[test]
    fn a_second_owner_is_refused_until_the_first_drops() {
        let Ok(Some(first)) = try_take() else {
            return; // another owner holds it on this machine (a test run, the daemon): nothing to show
        };
        assert!(matches!(try_take(), Ok(None)), "held by the first");
        drop(first);
        let again = try_take();
        assert!(matches!(again, Ok(Some(_))), "released on drop: {:?}", again.as_ref().map(|o| o.is_some()));
    }
}
