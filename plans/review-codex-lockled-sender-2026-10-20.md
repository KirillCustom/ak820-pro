I would revise the design before implementation. The basic choices are sound: full snapshots, one frame for both lines, Windows P0 at 16 ms, a separate sender thread, and delayed repeats. The main gaps are restoration after abandonment, stale-content ordering, transport handover, and bounded cancellation.

This is a static design review against the requested sources. I made no changes or board writes.

1. **Must-fix — “abandon leaves the board on the user’s state” is false.**  
   [Plan:531](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:531), [plan:570](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:570).

   A concrete Windows race: read Caps off → user turns Caps on → Windows sends its correct report → the daemon’s already-prepared report overwrites the board with Caps off → the next read detects the change and abandons. Windows still has Caps on; the board can retain the daemon’s wrong indicators indefinitely. SET_INDICATORS changing only the indicators is precisely why Windows need not correct them again.

   Specify an explicit, bounded restoration procedure using **fresh current locks**, covering cancellation, errors, shutdown, and a change during the final write. Include a post-final-write check. Restoration remains best effort on removal, process death, or stuck I/O.

   Also distinguish restoration from frame cancellation: when only the last trailer transition is missing, writing the old baseline can **commit** the abandoned frame. For cancellation caused by superseding content, expire the receiver before issuing a baseline repair that could finish its trailer.

2. **Must-fix — `user()` needs a defined freshness and validity contract.**  
   [Plan:500](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:500), [ledchannel_test.py:63](C:/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:63).

   Repeatedly calling `GetKeyState` from a background sender does not, by itself, establish the promised “read fresh.” Its state follows the calling thread’s input-queue processing. The probe’s initial read does not validate production behavior during lock changes. Define and test the message-queue/session strategy, including another keyboard and lock/unlock. Unknown or unavailable lock state should suppress transmission, rather than become an all-off baseline. [Microsoft’s GetKeyState contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getkeystate).

   `GetAsyncKeyState`’s low bit is “pressed since a previous call,” not toggle state, so it is not a replacement. [Microsoft’s GetAsyncKeyState contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getasynckeystate).

3. **Must-fix — a latest-content slot does not solve an already-running obsolete frame.**  
   [Plan:518](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:518), [plan:542](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:542).

   If A starts, B becomes current, and A completes, the board can newly display A after B is current. Specify a monotonically increasing **local content generation**, plus a transport generation. Every completion and scheduled repeat must identify which generations it belongs to.

   Choose explicitly whether a content change cancels the current frame or lets it finish. Either way, B must become the next eligible send immediately; A’s completion must not clear B’s pending work, update the slot back to A, or schedule an A repeat. An immutable byte sequence for one attempt is correct; an immutable pending retry of obsolete content is not.

4. **Must-fix — the ~65 s stale-title bound is not a bound.**  
   [Plan:545](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:545), [plan:561](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:561), [display.c:3070](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3070).

   Sixty seconds plus a frame is conditional on that refresh arriving. It can also be lost. With no further accepted text, the actual fallback is approximately **180 s after the last board-applied text update**, under normal awake display housekeeping.

   Late commits of old A restart that expiry. A completed old frame delayed before its first received preamble is not made old by the receiver’s 12 s lifetime: that lifetime begins on the board, not when the host chose A.

   The loss-free “one frame after the change” claim also excludes an in-flight A, warm-up/quiet, and media polling. A maximum text frame has 249 P0 writes—about 3.97 s of spacing alone. Finishing A and then sending B can approach nine seconds before polling and I/O overhead.

   Two lost CLEARs followed by silence likewise permit the previous title to remain until expiry. Either accept and document that behavior, or retain bounded periodic CLEAR recovery. Startup and transport acquisition must issue CLEAR when idle even without a new content change.

5. **Must-fix — transport selection must revoke the running worker, not merely prevent another frame from starting.**  
   [Plan:563](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:563), [agent.rs:414](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:414), [radio.rs:251](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:251).

   Checking “USB Absent and radio not qualified” when starting an LED frame does not establish exclusivity for its next four seconds. USB can appear, and `Radio::tick` can qualify **and push text in the same call**.

   Give the worker an explicit enabled/ownership generation, checked between writes and after blocking calls. USB evidence from media, clock, or health must revoke it. Radio acquisition must coordinate the transition before its first media push. Preserve the existing distinction between **Absent** and busy/refused/discovery failure; do not turn all failed USB operations into permission to use Bluetooth.

6. **Must-fix — the firmware’s existing gate does not mean “the cable is absent.”**  
   [Plan:475](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:475), [ch582f_ajazz.c:160](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:160), [ch582f_ajazz.c:528](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:528), [ch582f_ajazz.c:1837](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1837).

   `usb_mode` follows the slider. USB raw HID works while the slider remains on BT—the measurements themselves used this arrangement. Therefore, USB can publish B and a trailing lock-light frame can subsequently commit A into the same display slot.

   Define firmware media-source ownership at **application time**, with USB/raw81 priority and invalidation of pending lower-priority frames. Resetting only the currently assembling decoder is insufficient against a complete old frame arriving later. Distinguish plugging in USB while remaining in BT mode from switching the slider to cable mode; the latter already resets and gates the receiver.

7. **Must-fix — handover needs publication state, including unchanged content and CLEAR.**  
   [agent.rs:469](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:469), [agent.rs:516](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:516), [media.rs:106](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/media.rs:106).

   Specify all directions: USB→BT, raw81→BT, BT→USB, and BT→raw81. Entering a transport must publish a full current snapshot even when text has not changed. Leaving BT must invalidate its repeat and refresh work. Its generation must also invalidate late completion notifications.

   The existing `Publisher` advances after answered text pushes and has a 30 s keepalive. LED write completion proves neither delivery nor application. Give BT separate attempt/repeat/refresh bookkeeping, and describe status as “sent/attempted,” not “known displayed.” Merely extending `Via` without defining these semantics risks skipped handover refreshes or importing the raw-HID cadence into BT.

8. **Must-fix — a separate thread does not solve a stuck IOCTL or shutdown.**  
   [Plan:502](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:502), [plan:518](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:518), [radio.rs:341](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/radio.rs:341).

   Define deadlines and cancellation for open/query/write, interruptible waits, stop signaling, and bounded shutdown—including `--once`. Never hold the latest-slot mutex through I/O or sleep.

   Cancellation can lose a race to normal completion, and requesting cancellation does not wait for completion. Consequently, do not close/reuse live I/O resources or launch a replacement writer merely because a timeout elapsed. An operation that cannot be retired needs a poisoned/disabled transport and retained ownership, analogous to raw81. [Microsoft’s cancellation contract](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelsynchronousio).

   Require one writer per target device across daemon instances and diagnostic tools; one thread per process is not sufficient serialization.

9. **Must-fix — finish the type 1/type 2 contract before writing either endpoint.**  
   [Plan:464](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:464), [lockchan.c:107](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:107), [display.c:2263](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2263).

   For the first release, specify:

   | Field/rule | Required definition |
   |---|---|
   | Type 1 length | `2 <= len <= 42` |
   | Line lengths | `n0 <= 19`, `n0 <= len - 2`, `n1 = len - 2 - n0 <= 21` |
   | Icon | Exact IDs 0–3; other bits rejected, including unsupported playback bit |
   | Text | Printable ASCII `0x20..0x7e`; explicit handling of invalid bytes/NUL |
   | Updates | Both lines replaced; zero-length means clear that line, never “retain” |
   | Type 2 | Exactly zero payload; clear defined host media state |
   | Invalid/unknown | No partial mutation and no expiry refresh |

   Validate the entire payload before either display call. The existing setters clamp, sanitize, and stop at NUL; they are not a substitute for validating framing arithmetic.

   Wire version 0 already exists. No extra version byte is inherently necessary, but freeze this interpretation and distinguish “first daemon release” from wire version 1. Future packing or optional fields must have an unambiguous discriminator and defined unsupported behavior.

10. **Must-fix — omitting playback does not turn playback off.**  
    [Plan:549](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:549), [display.c:421](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:421), [display.c:2337](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2337).

    Text setters and `display_clear_text()` leave playback state alone. After USB/raw81→BT, an old track’s timer can run beside a new title for up to its 20 s expiry.

    An accepted BT snapshot without playback must explicitly disable playback. Type 2 must do so too. Define absence as “off,” rather than “leave unchanged,” for this absolute-snapshot protocol.

11. **Must-fix — distinguish unchanged content from a media source that stopped producing observations.**  
    [Plan:519](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:519), [agent.rs:308](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/agent.rs:308), [windows/smtc.rs:17](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/platform/windows/smtc.rs:17).

    Preserve the existing failed-read→idle behavior when feeding BT. Also carry observation freshness: the SMTC worker explicitly permits an indefinitely blocked read, while its previous `last_poll_ok` can remain true. A sender refreshing that cached snapshot every minute can keep yesterday’s title alive forever.

    Define a stale-source policy using the observation age, not the time the main loop recopied the slot. Stop renewing stale content and clear or allow expiry. Healthy repeated observations of an unchanged track must remain distinguishable from no new observations.

12. **Should-fix — specify retry and discovery behavior separately from content changes.**  
    [Plan:510](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:510), [plan:539](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:539), [ledchannel_test.py:114](C:/Users/jdlien/code/ak820-pro/scripts/ledchannel_test.py:114).

    “The next frame reopens” leaves unclear when that frame becomes due after a failed initial send, repeat, restore, or CLEAR. Retain pending current state, use bounded backoff, and do not mark failed attempts delivered. Recovery/acquisition should force publication even when content is unchanged.

    Preserve unique-device selection, distinguish discovery failure from absence, handle list-size races, and refuse ambiguous matches. Query `UnitId` on each new handle as the measured sender does, but never take the user’s flags from QUERY_INDICATORS. Keep this device-directed; no real-lock toggling fallback.

13. **Should-fix — keep the proposed timing, but define phase deadlines and what completion measures.**  
    [Plan:524](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:524), [lockchan.h:22](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.h:22), [lockchan.c:179](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:179).

    The receiver requires quiet **≥150 ms**, preamble continuations **<150 ms**, then gaps **≤500 ms** and total lifetime **≤12 s**. The proposed >600 ms recovery wait and 300 ms quiet fit those rules. Sixteen milliseconds after completion fits the Windows evidence; zero added spacing fits the Mac measurements.

    Apply spacing and lock checks to warm-up and repair writes too. Use monotonic deadlines, retain timing across handle reopen, and start with an unknown-history recovery wait. Abandon and restart framing after a suspension or missed phase deadline rather than finishing an already-doomed sequence.

    Windows acceptance is not board arrival. The margins are measured engineering choices, not proof that queued reports have drained. This limitation also applies to restoration and late-frame claims.

14. **Should-fix — refine the wake heuristic and give it an explicit resend event.**  
    [Plan:553](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:553), [display.c:3198](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:3198).

    Deep wake really does expire text and playback; `ch582_sleep_wake()` also resets the decoder. That part is supported.

    `GetLastInputInfo` is session-specific, not system-wide across sessions, and its timestamps need not increase monotonically. Handle failure and tick wrap, and retain an idle-armed state so a wake during a multi-second send is not missed. [Microsoft’s GetLastInputInfo contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo).

    A wake request must force an unchanged snapshot through the latest slot and coalesce with pending work. Include startup, resume/unlock, and newly acquired interfaces as resend reasons. The refresh provides another opportunity after a missed wake, not a guaranteed 60 s recovery.

15. **Should-fix — the board’s optimistic play/pause icon becomes much slower to correct.**  
    [Plan:542](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:542), [ak820pro.c:476](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/ak820pro.c:476), [display.c:2314](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/display.c:2314).

    The board flips its icon immediately on a media key. If the key has no effect on the selected player, host content does not change, so BT’s change detector sends nothing. The wrong icon can persist until the minute refresh.

    Explicitly accept that delay, disable/bound the optimistic guess for this source, or provide an appropriate reassertion trigger. Turning the playback timer off does not address the icon.

16. **Should-fix — compare the serialized panel content for BT scheduling.**  
    [Plan:542](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:542), [smtc/mod.rs:137](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/smtc/mod.rs:137), [text/mod.rs:153](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/text/mod.rs:153).

    `text_differs` compares full Unicode artist/title strings, before folding and fitting. Changes beyond the visible truncation or between equivalent folded strings can trigger seconds of traffic with identical displayed bytes. For BT, use `(type, canonical payload)` as content identity; keep observation freshness separate.

17. **Should-fix — generated parity is appropriate, but proves only the encoder layer.**  
    [Plan:484](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:484), [plan:584](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:584), [lockchan_test.py:698](C:/Users/jdlien/code/ak820-pro/scripts/lockchan_test.py:698).

    Keep deterministic Python-generated vectors and the independently checked golden vectors. Cover all eight user-lock baselines, block boundaries, both trailer branches, all profiles, representative sequence wrap values, and encoder rejection cases. Check regeneration for unexplained drift.

    Separately test payload validation and display effects, including malformed `n0`, unsupported flags/types, empty lines, CLEAR, and invalid frames leaving both state and expiry untouched. Sender tests need timestamped fake writes and completion delays—not just byte arrays—with changes/errors/cancellation at every write boundary, especially both trailer writes. Include A→B during send, handovers, lost wake notifications, superseded repeats, and stuck shutdown.

18. **Should-fix — make the hardware soak cover the new display side effects and input path.**  
    [Plan:588](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:588), [GREMLIN-RESULTS.md:294](C:/Users/jdlien/code/ak820-pro/plans/GREMLIN-RESULTS.md:294), [indicators.c:234](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:234).

    Existing measurements exercised decoding/counting, not the proposed media handler. Keep the handler to bounded validation and RAM-state updates; let the existing staged display path paint afterward.

    Soak maximum-length frames while typing, operating media keys, changing each lock from this and another keyboard, and exercising display overlays, sleep/wake, removal, and handovers. Check actual key delivery, loop stalls, UART errors, final indicators, and final text. The inspected indicator callback consumes Caps and Scroll, not Num; I found no Num-driven typing behavior in the board sources inspected. That supports the design, but does not replace load testing.

19. **Note — one frame for both lines is the right choice; a wire content ID is not automatically required.**  
    [Plan:464](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:464), [lockchan.c:135](C:/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan.c:135).

    It fits, avoids torn artist/title updates, and makes empty-line clearing explicit. Full snapshots need no delta base ID.

    The existing `seq` is merely copied into the committed frame; it currently supplies no deduplication or freshness ordering. Local generations and properly enforced source ownership may suffice. If wire ordering is added, define sender/session identity, wrap/restart behavior, CLEAR ordering, and repeated snapshots refreshing expiry. An ID cannot tell the board about B before B arrives.

    Re-budget any added field: a four-byte ID fits the 42-byte text form, but makes the proposed 47-byte playback form exceed 48 bytes.

20. **Note — replace “never commits wrong” with the actual evidence and guarantees.**  
    [Plan:535](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:535), [plan:570](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:570), [GREMLIN-RESULTS.md:285](C:/Users/jdlien/code/ak820-pro/plans/GREMLIN-RESULTS.md:285).

    CRC and trailer checks reject many corruptions; finite CRCs do not make erroneous acceptance impossible. A user report can also be harmless to decoding, or arrive after a valid commit. Valid-but-obsolete content is a different problem that CRC cannot detect.

    Correct the measurement summary too: the dongle cliff was 6–7 ms; BLE was 2/6 at 7 ms and 6/6 at 10 ms. Sixteen milliseconds remains the measured recommendation. “No wrong committed frame observed” accurately describes the experiments.

21. **Note — keep future playback explicitly unsupported until its sampling contract exists.**  
    [Plan:467](C:/Users/jdlien/code/ak820-pro/plans/LOCK-LED-CHANNEL-PLAN.md:467), [smtc/mod.rs:77](C:/Users/jdlien/code/ak820-pro/ak820-agent/src/smtc/mod.rs:77), [scripts/lockchan.py:118](C:/Users/jdlien/code/ak820-pro/scripts/lockchan.py:118).

    Sampling position at the preamble anchor requires a staged encoder and an observation timestamp; the current snapshot has no timestamp and `encode()` prepares the whole frame in advance. Board anchor-to-commit compensation alone does not correct age accumulated before the preamble or host acceptance-to-arrival delay.

    Deferring playback is sensible. Reserve the extension unambiguously, and reject it in the first handler rather than accidentally interpreting its bytes as line 1.