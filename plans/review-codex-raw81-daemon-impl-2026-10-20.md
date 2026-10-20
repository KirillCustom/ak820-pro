**Hold installation on gremlin.** The normal transaction path looks sound, but two ownership defects break R4-1’s quiet-period guarantee. The reported live run does not exercise those paths.

1. **Must-fix — `Stuck` releases ownership while I/O remains outstanding.**  
   [radio.rs:301](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:301) calls `release()` immediately on `Error::Stuck`. Windows deliberately leaks the pending operation at [device.rs:490](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/device.rs:490), and leaves its device handle open at [device.rs:585](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/device.rs:585).

   Consequently, another owner can acquire the lock, finish its quiet, and start sending while the abandoned **write** can still complete. The delivery-lifetime assumption cannot bound a write that has not left the kernel yet. Reacquisition by this daemon after 60 seconds has the same problem.

   Retain ownership until completion is established. With the current permanently abandoned-operation design, disable radio use and retain its guard for the process lifetime; USB takeover must not release that guard. A cooldown alone cannot fix this.

2. **Must-fix — The initial quiet starts before the lock is acquired.**  
   `now` is captured at [radio.rs:185](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:185), before discovery, device opening, directory/file operations, and locking. That old timestamp reaches `Link::new` at [radio.rs:212](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:212).

   Time spent before ownership therefore counts toward the mandatory quiet. If those operations or scheduling consume three seconds, the probe can run immediately after acquiring the lock. Sample the injected clock **after successful acquisition**, then start `QUIET`. Rotation already retains the lock.

3. **Must-fix — Add regressions at the actual ownership and agent boundaries.**  
   The agent’s transport-selection logic has no new test seam or integration tests: its tests remain Watch/watchdog tests at [agent.rs:845](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:845). The radio rig substitutes `Box::new(())` for ownership at [radio.rs:356](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:356); it counts acquisitions but cannot establish exclusion or guard-release timing.

   Before installation, cover delayed acquisition, outstanding writes after `Stuck`, every USB opener result, mid-transfer USB `Absent`, USB return during quiet/backoff, and refresh after an intervening radio failure. These are outstanding R2-6/R3-3/R4-3 requirements, and they would expose findings 1–6.

4. **Should-fix — USB handover can lose the full-refresh requirement.**  
   The USB branch refreshes only when the previous telemetry value is exactly `"radio"` at [agent.rs:310](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:310). An absent-USB tick first sets it to `"none"` at [agent.rs:328](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:328), retaining that value after radio silence, rotation, or dongle loss.

   Reproduction: publish a track over radio → radio fails → USB opens successfully before the keepalive. The shared publisher still considers the text current, so USB can send only playback until the 30-second keepalive. This contradicts R2-7. Track publication transport/invalidation independently of current status, or refresh when entering USB from any other transport.

5. **Should-fix — Observed USB presence does not always end the radio acquisition.**  
   `usb_present()` runs only after a successful **media** USB open at [agent.rs:302](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:302). A returning board that produces `Open`/Busy, `Incompatible`, or `Ambiguous` suppresses radio transmission but leaves its qualification and lock intact. Because `Radio::tick` is no longer called, its idle-release path cannot run either.

   Successful clock interactions also update only the USB Watch, for example [agent.rs:823](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:823). Thus USB can be observed while the radio remains acquired.

   Stand down on evidence that USB is present, including unsuccessful opens of a listed board, while preserving the exceptional outstanding-I/O ownership from finding 1. Do not treat an arbitrary discovery failure as proof of presence.

6. **Should-fix — Transport timeouts bypass qualification invalidation and recovery refresh.**  
   Protocol `Unknown` clears qualification correctly at [link.rs:313](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:313). But a cancelled write timeout returns `Failure::Hid` at [link.rs:264](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:264), and read errors return directly at line 295. The generic HID failure branch merely changes state at [radio.rs:306](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:306).

   The next tick can therefore reuse qualification and send playback without a probe or full refresh. Invalidate qualification on relevant transport failures, especially timeouts, and define recovery consistently. This leaves R2-1 and R3-3 partly implemented; envelope v2 prevents it becoming the old-firmware payload hazard.

7. **Should-fix — Strengthen the Windows lock’s pathname and cross-user contract.**  
   [radio_lock.rs:51](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:51) includes `FILE_SHARE_DELETE`. That permits rename access: an authorized process can rename the held file and create another `raw81.lock`, yielding two independently lockable file objects. Remove delete sharing; read/write sharing is sufficient for the Python participant. This follows from Windows’ documented [CreateFile sharing semantics](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew).

   Directory creation and file opening use inherited security at [radio_lock.rs:40](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:40). A ProgramData pathname alone does not establish second-user write access; [default ACLs inherit from the parent](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights). Provision or validate suitable permissions and test a second ordinary user. Access failure currently fails closed with a separate lock-error backoff, which is appropriate.

8. **Should-fix — `Incomplete` versus `Unknown` is not preserved in logs and counters as specified.**  
   The protocol variants and last-error strings differ, but both increment the same failure counter, and `Incomplete` produces no log transition at [radio.rs:275](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:275). A subsequent successful tick clears the last error. R2-4 asked for durable distinction in logs/counters.

   Also expose qualification separately from `State::Qualified`: an `Unknown` followed by a successful immediate retry leaves `link.qualified() == false`, while the radio can still report `"qualified"`. See [link.rs:233](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:233) and [radio.rs:241](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:241).

The following checks passed by inspection:

- **Envelope and sequence accounting:** masking includes zero padding; CRC covers transmitted bytes; the complete payload and outer sum are checked. `clean_seq` skips unsafe high bytes. `used = seq.wrapping_sub(previous)` counts numerical skips, and the span check precedes transmission. Sequence consumption is recorded before attempting the write. With this bounded search and the 16,384-position limit, crossing `0xFFFF` does not reuse an identity within the host session. [mod.rs:90](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/mod.rs:90), [mod.rs:136](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/mod.rs:136), [link.rs:241](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:241).
- **Exchange contract:** the injected-clock drain requires an empty read, bounds reports/time, and preserves `Stuck`. The read deadline starts after writing, reads receive the remaining budget, and reports returned at/after the deadline are rejected before feeding. Cancellation grace remains additional elapsed time. [link.rs:274](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:274), [link.rs:349](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:349).
- **Receipt correlation:** foreign valid receipts are ignored; corrupt receipts close `Unknown`; report ID zero is stripped only from 33-byte reports; receipt-first or wrong-reply-CRC closes `Incomplete`. The completed text reply must echo the entire supplied command, and `0xFF` is rejected. [mod.rs:277](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/mod.rs:277), [link.rs:301](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:301).
- **Retry/admission:** text gets at most one immediate retry, on a new sequence, for `Unknown`/BADCRC. `Incomplete` counts as admission and is not immediately retried. STALE rotates without trusting `last_seq`. BUSY allows three grace waits before first admission; the fourth BUSY backs off, and a new host session resets the grace. [link.rs:227](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:227), [link.rs:309](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:309).
- **USB isolation:** radio outcomes do not enter `watch.observe`, USB failure counters, or USB backoff. Only the opener’s `Absent` selects fallback; a USB transaction failure does not. Clock and ordinary health reads still use USB, and `Link` exposes text plus the explicitly permitted probe. [agent.rs:300](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:300), [platform/mod.rs:59](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/mod.rs:59).
- **Normal lifecycle:** successful media USB opening cancels an acquisition before that tick’s radio work. Observed dongle-open failure releases it. Silence backs off to 30 seconds and releases at the cap; reacquisition starts another quiet. New qualification refreshes text. [radio.rs:191](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:191), [radio.rs:228](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:228), [radio.rs:279](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:279).

**Notes on the stated limitations and remaining coverage:**

| Item | Assessment |
|---|---|
| Board idles after 1 s; media ticks every 3 s | Acceptable under the stated delivery bound. Firmware explicitly restarts admission after idle; host sequences still advance without reuse. [raw81.c:157](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/raw81.c:157). |
| Nonblocking quiet and grace resume next tick | Acceptable. Longer waits are conservative; the three-retry grace bound remains. Initial cable-out display latency can approach **two media intervals**, so “within a tick” overstates startup behavior. |
| Dongle opened per tick; Link and lock span ticks | Acceptable once findings 1–2 are fixed. An unplug/replug entirely between observations is not detected as a distinct arrival. V2 still protects the next request. |
| macOS defaults, not built | Acceptable for this Windows-only installation decision; not evidence of Mac support. |
| Lock primitive and process exit | Byte-zero exclusive locking beyond EOF is valid and compatible with the Python byte-zero lock. Closing the handle or terminating the process releases it, possibly with an OS delay. The reported contention/kill measurements support the normal path. [radio_lock.rs:22](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:22), [Python owner:69](C:/Users/jdlien/code/ak820-pro/scripts/win_raw81_s2.py:69), [Microsoft lock semantics](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex). |
| Other plan deviations | Probes are single-attempt, whereas text implements the immediate retry. FORBIDDEN is a 600-second cooldown, rather than original D3’s disable-until-build-change rule. Document these explicitly. [link.rs:218](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:218), [radio.rs:295](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:295). |

The fake decodes envelopes but does **not** implement firmware session freshness, idle restart, allow-list enforcement, response TTL, or real per-handle queues. Writes always succeed immediately; its `Stuck` scenario makes subsequent **reads** fail. Its reads honor deadlines, so the late-answer test does not exercise a Windows completion returned late during cancellation. [fake.rs:104](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/fake.rs:104), [fake.rs:150](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/fake.rs:150).

Beyond finding 3, missing explicit cases include forced search exhaustion, exact span/wrap boundaries, same-ID restart with late previous-session responses, stage-1 echoes, unobserved target replacement, arbitrary initial windows asserting the **permitted residual**, and Rust round-trips across every allowed command. The current window property starts cleared and covers probe/CLEAR/SET_LINE/PLAYBACK. [mod.rs:521](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/mod.rs:521), [link.rs:556](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/raw81/link.rs:556). Firmware idle tests already exist in [raw81_test.py:308](C:/Users/jdlien/code/ak820-pro/scripts/raw81_test.py:308), and the recorded hardware TTL tests are separate useful evidence.

Cross-user lock tests and install flag combinations are also missing. The lock unit test silently returns even on an acquisition **error**, potentially hiding ACL failure. [radio_lock.rs:87](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/radio_lock.rs:87), [cli.rs:544](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/cli.rs:544).

Process launches were blocked in this review environment. I inspected both commits through Git objects, verified all changed source files match `8da0dfc`, and confirmed the firmware checkout is `0ae4cf8c`. No files changed; the reported 372 passing tests were not independently rerun.