//! The daemon's orderly stop. `launchctl bootout`, which `ak820 install` and
//! `uninstall` run, ends the job with SIGTERM, and a run by hand ends with
//! Ctrl-C's SIGINT. Either sets a flag the loop reads every slice
//! (`Platform::stop_requested`), so the loop returns and the lock-LED sender
//! puts back the lights it owes before the process ends (R3-3, R4-4). Without
//! it, the default action killed the process mid-frame.
//!
//! `SA_RESETHAND` puts the default back once the first signal is taken, so a
//! second one kills at once if the stop hangs; launchd's own SIGKILL follows
//! 20 s after its SIGTERM regardless. `SA_RESTART` keeps the signal from
//! failing a blocking read on whichever thread takes it (the media helper's
//! pipe, the transport's).

use std::os::raw::c_int;
use std::sync::atomic::{AtomicBool, Ordering};

static REQUESTED: AtomicBool = AtomicBool::new(false);

const SIGINT: c_int = 2;
const SIGTERM: c_int = 15;
const SA_RESTART: c_int = 0x0002;
const SA_RESETHAND: c_int = 0x0004;

/// Darwin's user-space `struct sigaction` (`<sys/signal.h>`): the handler,
/// a 32-bit `sigset_t`, the flags.
#[repr(C)]
struct SigAction {
    handler: usize,
    mask: u32,
    flags: c_int,
}

extern "C" {
    fn sigaction(sig: c_int, act: *const SigAction, old: *mut SigAction) -> c_int;
}

/// A lock-free atomic store is all a handler may safely do.
extern "C" fn on_signal(_sig: c_int) {
    REQUESTED.store(true, Ordering::SeqCst);
}

/// Take SIGTERM and SIGINT. Called once, before the loop starts.
pub fn install() -> Result<(), String> {
    for sig in [SIGTERM, SIGINT] {
        let act = SigAction { handler: on_signal as extern "C" fn(c_int) as usize, mask: 0, flags: SA_RESTART | SA_RESETHAND };
        // SAFETY: `act` is a valid Darwin sigaction and `old` may be null.
        if unsafe { sigaction(sig, &act, std::ptr::null_mut()) } != 0 {
            return Err(format!("sigaction({sig}): {}", std::io::Error::last_os_error()));
        }
    }
    Ok(())
}

pub fn requested() -> bool {
    REQUESTED.load(Ordering::SeqCst)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    extern "C" {
        fn getpid() -> c_int;
        fn kill(pid: c_int, sig: c_int) -> c_int;
    }

    fn current(sig: c_int) -> SigAction {
        let mut old = SigAction { handler: usize::MAX, mask: u32::MAX, flags: -1 };
        // SAFETY: a null `act` only reads the disposition into `old`.
        assert_eq!(unsafe { sigaction(sig, std::ptr::null(), &mut old) }, 0);
        old
    }

    /// One test, because the handler and the flag are the process's: the
    /// layout read back, then a real SIGTERM taken, then the default back.
    /// A wrong layout fails loudly here: the SIGTERM kills the test run.
    #[test]
    fn sigterm_sets_the_flag_once_then_the_default_returns() {
        install().unwrap();
        for sig in [SIGTERM, SIGINT] {
            let a = current(sig);
            assert_eq!(a.handler, on_signal as extern "C" fn(c_int) as usize);
            // XNU reports SA_RESTART back but never SA_RESETHAND; the reset
            // is checked below by what follows the signal.
            assert_eq!(a.flags & SA_RESTART, SA_RESTART);
            assert_eq!(a.mask, 0);
        }
        assert!(!requested());
        // SAFETY: signalling ourselves; the handler above takes it.
        assert_eq!(unsafe { kill(getpid(), SIGTERM) }, 0);
        let deadline = Instant::now() + Duration::from_secs(2);
        while !requested() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(requested());
        assert_eq!(current(SIGTERM).handler, 0, "SA_RESETHAND: SIG_DFL after the first");
        // Leave SIGINT as the run found it: default.
        let dfl = SigAction { handler: 0, mask: 0, flags: 0 };
        // SAFETY: restoring the default disposition.
        assert_eq!(unsafe { sigaction(SIGINT, &dfl, std::ptr::null_mut()) }, 0);
    }
}
