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
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
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
        // Every user's: reachable whatever the creator's umask, and sticky,
        // so no other user can delete or replace the file, whose inode IS
        // the lock (flock does not stop a rename, as Windows' sharing does).
        // Its owner, the directory's owner and root still can: the Rust and
        // the Python never do, and every holder must keep it so. Only the
        // owner can set this; anyone else's daemon finds it set or not.
        let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o1777));
    }
    let file = open_or_create(&p).map_err(|e| format!("{}: {e}", p.display()))?;
    match file.try_lock() {
        Ok(()) => Ok(Some(RadioLock(file))),
        Err(TryLockError::WouldBlock) => Ok(None),
        Err(TryLockError::Error(e)) => Err(format!("{}: {e}", p.display())),
    }
}

/// Read-only when it is there: all flock needs, and all a second user has
/// on another's file. Created exclusively when it is not, so two openers
/// racing at its creation both end on the one file: the loser finds it
/// existing and opens it read-only (codex, the macOS radio review, 3).
fn open_or_create(p: &std::path::Path) -> std::io::Result<File> {
    for _ in 0..3 {
        match File::open(p) {
            Ok(f) => {
                // Its owner puts back what a restrictive umask took; anyone
                // else's attempt fails and changes nothing (codex's verify, 3).
                let _ = f.set_permissions(std::fs::Permissions::from_mode(0o666));
                return Ok(f);
            }
            Err(e) if e.kind() != ErrorKind::NotFound => return Err(e),
            Err(_) => {}
        }
        match OpenOptions::new().read(true).write(true).create_new(true).mode(0o666).open(p) {
            Ok(f) => {
                // Readable by every user whatever the umask.
                let _ = f.set_permissions(std::fs::Permissions::from_mode(0o666));
                return Ok(f);
            }
            Err(e) if e.kind() == ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    File::open(p)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Two takes in one process conflict, as two processes' do: flock is per
    /// open file, so the second open's lock is refused while the first holds.
    #[test]
    fn a_second_owner_is_refused_until_the_first_drops() {
        let first = match try_take() {
            Ok(Some(held)) => held,
            Ok(None) => return, // another owner holds it on this machine (a test run, the daemon): nothing to show
            Err(e) => panic!("the lock could not be tried: {e}"),
        };
        assert!(matches!(try_take(), Ok(None)), "held by the first");
        drop(first);
        let again = try_take();
        assert!(matches!(again, Ok(Some(_))), "released on drop: {:?}", again.as_ref().map(|o| o.is_some()));
    }
}
