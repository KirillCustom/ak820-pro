**Hold installation on gremlin.** The main fixes are sound, but finding 1 remains open: the poisoned guard survives subsequent ticks, yet does **not** survive dropping `Radio` before process exit.

1. **Must-fix — Poisoned ownership is still `Radio`-lifetime, not process-lifetime.**  
   The guard moves into an ordinary `Option<RadioGuard>` at [radio.rs:342](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:342). There is no protective destructor; dropping `Radio` drops that field, and [RadioLock::drop](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:22) closes the lock handle. The abandoned device and pending operation remain leaked. [device.rs:585](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/device.rs:585)

   `usb_present()` and later ticks **correctly preserve** the poisoned guard through their early returns. [radio.rs:172](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:172), [radio.rs:200](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:200)

   `--once` returns from `run`, invoking ordinary local destruction before process termination. It normally exits during the initial quiet without transmitting, so it is not a normal stuck-write reproduction. Nevertheless, a delay after quiet creation can let the first tick transmit; once-mode provides no protection if that tick poisons the radio. [radio.rs:232](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:232), [agent.rs:400](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:400), [daemon.rs:140](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/daemon.rs:140)

   Make poisoned ownership intentionally non-dropping, leaving OS process cleanup to release it. Extend the regression to retain the observable lock state, **drop the poisoned `Radio`**, and verify exclusion remains. The present test ends before that assertion. [agent.rs:1254](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:1254)

2. **Should-fix — Finding 5 closes for media opens, but not for all clock/health evidence.**  
   `media_tick` correctly stands down after a successful USB open and after `Open`, `Incompatible`, or `Ambiguous`; discovery failure correctly preserves the acquisition. [agent.rs:429](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:429), [agent.rs:452](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:452)

   The added outer check sees only the **final** `Watch::Present` state. Successful health reads never call `watch.observe(Ok(()))`, so they cannot trigger it. Clock busy/refused outcomes likewise become `Busy`/`Refused`, not `Present`. An earlier successful clock interaction can also be overwritten by a later failure before this check. [agent.rs:356](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:356), [agent.rs:386](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:386), [agent.rs:866](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:866), [agent.rs:908](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:908)

   Report USB-presence evidence at each opener boundary, preserving it independently of the eventual transaction result. Also, the `acquired()` condition skips clearing radio cooldowns that have already released their link.

   **No new `radio_only` reset loop:** the explicit exemption is correct. Normal media fallback records `Absent`, preventing an old `Present` from repeatedly cancelling fresh acquisitions. The existing common `watch.holding()` gate still pauses radio media—including `radio_only`—after a USB `Stuck`; this is unchanged. [agent.rs:296](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:296), [agent.rs:323](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:323)

3. **Should-fix — Finding 8’s new qualification telemetry becomes stale on release.**  
   `release()` clears the link and session but leaves `status.qualified` unchanged. After successful radio publication followed by USB takeover or dongle loss, status can therefore say `radio=off`/`absent` with `radio_qualified=yes`. Refusal has the same problem because `fail()` copies qualification before releasing. [radio.rs:163](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:163), [radio.rs:292](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:292), [radio.rs:332](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:332)

   Additionally, `st.radio` is cloned **before** clock/health stand-down, leaving the published radio state and transport stale until another media tick. Clear qualification whenever the link disappears and snapshot status after the final stand-down decision. [agent.rs:329](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:329), [agent.rs:386](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:386)

The original findings close as follows:

| Finding | Assessment |
|---|---|
| **1** | **Partial; blocking.** USB takeover and later ticks are fixed; destruction is not. |
| **2** | **Closed.** The only production `Link::new` samples the clock after acquisition. Rotation timestamps are taken while ownership remains held; I found no other pre-lock time credited toward quiet. [radio.rs:232](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:232), [link.rs:244](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:244), [link.rs:334](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:334) |
| **3** | **Substantially addressed, incomplete.** Real media-selection and observable guard seams now exist. The blocking missing regression is poisoned-object destruction; clock/health integration remains untested. |
| **4** | **Closed.** `Via` follows completed text publication independently of current telemetry. [agent.rs:456](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:456), [agent.rs:470](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:470) |
| **5** | **Partial.** Media evidence works; clock/health propagation remains incomplete. |
| **6** | **Closed by inspection.** Write timeout, write error and post-write read error disqualify; the next successful probe increments generation and forces text refresh. [link.rs:270](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:270), [link.rs:306](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:306), [radio.rs:248](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:248) |
| **7** | **Code changes sound; cross-user validation deferred.** Read access suffices for exclusive locking, and removing delete sharing addresses pathname replacement. Acquisition errors now fail the test. [radio_lock.rs:46](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:46), [radio_lock.rs:94](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:94), [Microsoft locking contract](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex), [sharing contract](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew). Actual inherited ACLs remain unverified; not blocking for this one-account test. |
| **8** | **Partial.** Separate counters, incomplete logging and retained error timestamps work; qualification lifecycle reporting needs the correction above. [radio.rs:302](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:302) |

**Notes on `media_tick` refresh behavior:** I found no new steady-state refresh storm or permanently lost transport refresh.

| Sequence | Result |
|---|---|
| First publication, either transport | Full text remains due until successful. |
| Same transport succeeds repeatedly | Playback only between changes/keepalives. |
| USB → radio, or radio → USB | Full text refresh; `Via` changes only after completed text. |
| Radio succeeds → radio fails → USB | Full USB refresh; the original defect is fixed. |
| Handover text fails → retry or return to previous transport | Refresh remains pending because failure does not restore the keepalive timestamp. |
| Text succeeds but playback fails | `Via` still records the successful text transport. Radio failure additionally triggers probe/refresh recovery where required. |

These follow from the success booleans in [cycle](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:504) and [Radio::tick](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:274), plus [Publisher::refresh](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/media.rs:117). Repeated refresh calls during quiet/backoff only keep publication pending; they do not themselves transmit.

**Notes on stuck opens and tests:**

- An opener returning `Stuck` still releases the ordinary acquisition and uses a 60-second cooldown. That differs from transfer poisoning, but the current Windows opener performs no raw81 write and has no `Stuck` return path. A genuinely hanging synchronous open remains unbounded—a pre-existing limitation, not an outstanding-write ownership regression. [radio.rs:209](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:209), [device.rs:207](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/device.rs:207), [device.rs:361](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/device.rs:361)
- The delayed-acquisition test genuinely checks the full quiet and its boundary. `StuckWrite` now fails the write itself; the guard test meaningfully checks immediate retention, USB return and later ticks. Your reported mutation failures support those assertions, but do not establish retention after destruction. [agent.rs:1207](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:1207), [fake.rs:155](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/fake.rs:155)
- Search exhaustion tests the rotation branch with **zero candidates**, not exhaustion of 256 actual candidates. The residual test covers **all 65,536 initial pairs for 12 frames**. Command round-trips cover the listed allow-list, but do not execute firmware dispatch. Those are useful tests with appropriately limited claims. [link.rs:623](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:623), [mod.rs:652](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/mod.rs:652), [mod.rs:682](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/mod.rs:682)
- Non-stuck write timeout/recovery, USB return during backoff, clock/health evidence and text-success/playback-failure deserve explicit regressions. Beyond poisoned destruction, I would not independently block a supervised one-machine test on those coverage gaps.

Process launches were denied, so I could not execute `git show` or rerun tests. I reconstructed the commit diff from Git objects, verified its source preimages match `8da0dfc`, and verified all eight working source files match `a65baae`. No files changed.