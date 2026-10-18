**I would keep change coding, replace whole-frame base-15 packing with small fixed blocks, compress the displayed text, and start with CRC plus delayed retries. I would not build timing-derived Reed–Solomon recovery yet.** The best opportunities to exceed the draft’s throughput are forwarded padding bits and the 2.4G path—not a more elaborate four-light alphabet.

For a first reliable version, I would also offer a profile that leaves Scroll untouched. It costs roughly 25–30% in transmission time, but removes a difficult restoration requirement.

I read the plan first and used only files inside the repository. Nothing was modified or run against the keyboard. Below, **measured** means recorded in the supplied research; **inferred** means derived from those measurements or the implementation; proposed rates assume the board actually receives the host’s approximately 32 acknowledged writes/s.

**The draft has several substantive gaps.**

1. **“Caps is not data” does not mean “Caps is never written.”** Every raw output report contains Caps. The probe explicitly writes it as zero; that is suitable only for its controlled experiments, not the daemon. See [main.swift:143](/Users/jdlien/code/ak820-pro/hostagent/ledprobe/main.swift:143).

   Production must preserve the current OS Caps state, including when Caps starts on, changes during transmission, or changes from another keyboard. Reading Caps immediately before each write reduces the race but does not make the read/write atomic. Queuing an entire frame makes this substantially worse.

   Also distinguish **changing the host’s logical Caps state** from **sending an incorrect Caps indicator bit**. Raw LED output is not itself a Caps keypress; the experiments have not established all resulting typing, indicator, and cache behavior. Both need testing.

   A literal guarantee that an old Caps bit can *never* be transmitted concurrently with an OS Caps transition is not available from the demonstrated whole-byte, user-space interface. That limitation should be explicit.

2. **Skipping every report whose Caps bit changed is not a reliable discriminator.** Such a report could be the daemon’s next legitimate data write carrying the newly correct Caps state. Conversely, an OS reassertion need not change Caps.

   Change coding tolerates an extra report that preserves the data bits. It does not automatically tolerate an OS report that clears or restores older data bits. For the initial implementation, aborting a frame on an observed Caps transition is simpler and safer than attempting to classify that report.

3. **The trace does not measure UART arrival times.** It timestamps parsing in the main loop and explicitly notes that queued frames can appear with nearly zero gaps. See [ch582f_ajazz.c:294](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:294).

   Therefore, a 60 ms gap could represent a missing report, delayed delivery, delayed parsing, or some combination. It is not evidence of a known-position erasure.

4. **A lost restore does not invalidate an already-received payload CRC.** The contrary statement in [BACKLOG.md:74](/Users/jdlien/code/ak820-pro/plans/BACKLOG.md:74) is wrong. Payload integrity and indicator restoration are separate properties. A valid frame can leave Scroll wrong indefinitely if the restore is lost and the host subsequently locks.

5. **The proposed alphabet does not enforce the Scroll rule.** Nonzero changes can toggle Num/Compose/Kana indefinitely while leaving Scroll changed. Arbitrary payloads, including structured headers and padding, can produce long runs.

   There is another complication: the implemented “own Scroll key” exception immediately accepts a Scroll transition within **500 ms**, even if it was actually channel traffic. See [indicators.c:67](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/indicators.c:67). Settling alone is insufficient.

6. **The application is larger and more active than the summary suggests.** The current display holds **19 artist characters plus 21 title characters**, not a total of approximately 30. The daemon refreshes unchanged text every 30 seconds and sends playback information every 3 seconds. See [text/mod.rs:38](/Users/jdlien/code/ak820-pro/ak820-agent/src/text/mod.rs:38), [media.rs:1](/Users/jdlien/code/ak820-pro/ak820-agent/src/media.rs:1), and [display.md:470](/Users/jdlien/code/ak820-pro/docs/display.md:470).

   Wireless needs its own publication policy. Tunnelling the existing padded raw-HID reports and their cadence would waste much of this channel.

7. **“One-way” is a working assumption, not a settled hardware limitation.** The adjacent backlog item already documents unusual consumer usages travelling board-to-host on Linux/2.4G. That deserves a focused Mac/BLE experiment before accepting permanent blind retransmission.

---

**The four-light alphabet is already close to its information limit.** With four independently usable bits, there are 16 values: at most 4 bits/write. If consecutive equal values cannot carry information, there are 15 next states: at most `log₂15 = 3.9069` bits/write. Changing the mapping cannot beat those limits.

The useful alternatives are these:

| Scheme | Rough rate or overhead | Host / board complexity | Unmeasured assumption | Verdict |
|---|---|---|---|---|
| Four-light change coding | **125 bit/s** before framing | Low / low with block packing | Four bits survive separately at speed | **Adopt**, subject to forwarding tests |
| Plain four-bit values | **128 bit/s**, only 2.4% faster | Very low / very low; interference handling harder | Repeats arrive at speed; extra OS reports are distinguishable or sufficiently rare | **Test first**; small speed gain alone is unconvincing |
| Rank each next value among the 15 values unlike the previous one | Same 125 bit/s | Low / low | Same as change coding | **Reject as an improvement**; essentially an alternative labelling |
| Byte/nibble escape scheme avoiding repeats | Typically below 3.9 bits/write; depends on escapes and source distribution | Low / low | Representative symbol distribution | **Reject initially**; fixed blocks are simpler and closer to capacity |
| Three data bits, leaving Scroll unchanged | Plain **96**, changes **89.8 bit/s** | Low / low | Num and bits 3–4 survive | **Adopt as conservative profile** |
| Only bits 3–4, leaving Num and Scroll unchanged | Plain **64**, changes **50.7 bit/s** | Low / low | Both unnamed bits survive | **Adopt as optional low-impact fallback** |
| Reserve one of four bits as an alternating sequence bit | **96 bit/s** before other overhead | Low / low–medium | Sequence transitions survive; insertions remain manageable | **Test first** if isolated deletions are common; detects some losses, not arbitrary bursts |
| Two sequence bits plus two data bits | **64 bit/s** | Low / medium | Loss bursts fit the modulo sequence interpretation | **Reject initially**; too expensive without measured need |
| Reserve one transition for control/markers | Alphabet 14: ceiling **121.8 bit/s** | Low / low–medium | Marker behavior under interference is understood | **Test first** if rapid resynchronization is needed |
| Use forwarded padding bits | Five, six, or seven non-Caps bits give up to **160/192/224 bit/s** | Low host; medium parser/protocol changes | Padding survives host, module, and operating mode | **High-priority test** |
| Queue many asynchronous writes | **Measured: about 32/s**, no gain | Medium / unchanged | Already tested at host completion level | **Reject for speed** |
| One outstanding asynchronous write | Same rate | Medium / unchanged | Cancellation and callbacks behave adequately | **Adopt if needed for daemon responsiveness**, not throughput |
| Direct GATT Write Without Response or another lower-level write path | Potentially removes the acknowledged-write bottleneck; rate unknown | High host / little board change if it works | Characteristic properties, OS access, ordering, coexistence with HID | **Test first, low priority**; descriptor alone does not establish this capability |

The particularly attractive padding-bit outcome is **bits 3–7 all surviving**. That would provide five data bits while leaving Num, Caps and Scroll out of the alphabet: approximately **158.5 bit/s with changes**. It improves both speed and indicator behavior.

The existing `d <= 0x1F` guard would reject such reports. Its purpose is important: the parser has seen bogus checksum-valid `5A` frames in startup/link-up traffic. Do not simply remove the guard. Separate validated transport data from the LED bitmap exposed to QMK, and retain link-state and framing checks. See [ch582f_ajazz.c:1168](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:1168).

**Timing modulation is mathematically possible, but the measured channel is a poor candidate.**

Suppose a write ordinarily costs `T = 31.25 ms`, and an extra timing bit chooses between no added delay and `Δ` added delay, equally often. The rate becomes:

`5 / (T + Δ/2)` bits/ms.

It beats four untimed bits/write only when `Δ < T/2`, approximately **15.6 ms**. For example:

- A reliably distinguishable extra 10 ms would yield approximately **138 bit/s**.
- An extra 30 ms yields only approximately **108 bit/s**.

The observed 24–45 ms write durations already span more than the useful separation, and those are completion times, not board arrival times. Connection-event quantization and main-loop timestamp distortion add further uncertainty.

Biased coding with rare long delays can theoretically recover a small gain even with larger spacing; it requires entropy coding and a characterized timing channel. It is not a free fifth bit.

**Verdict:** **reject timing modulation for version one**. Host complexity is medium/high, board complexity medium, and the missing measurement is the conditional distribution of actual receive intervals under load. Use timing for expiry and clock anchoring instead.

**Caps as a fifth data bit should be rejected.** It offers approximately 25–27% more capacity, but there is no established way to distinguish a transient data Caps value from a legitimate user update without sometimes suppressing or misrepresenting the latter. Hiding it from the LCD does not solve that ambiguity. This violates the owner’s requirement even if raw LED writes do not change logical typing state.

**The dongle is a credible speed improvement, but the existing evidence does not establish its four-bit rate.**

The recorded 8–40 ms/bit corresponds to 25–125 writes/s if each bit consumes one write. At those rates:

- Four-bit changes would yield approximately **98–488 bit/s**.
- Two-bit changes would yield approximately **40–198 bit/s**.
- At the approximately 10 ms operating point, those become **391** and **158 bit/s** respectively.

These are **inferences**, conditional on the wider alphabet and separate-report delivery working through that dongle and host API. The reported 42/42 intact frames are encouraging, not a reliability characterization.

**Verdict:** **test first, high priority**. Reuse the same board decoder; expect medium host work for targeted dongle discovery and output-report access. A useful dongle result could outperform all the proposed BLE coding refinements.

---

**Compression and avoiding unnecessary updates are more valuable than recovering the last 0.093 bit/write.**

The font supports printable ASCII after host-side folding. It does not justify silently discarding case or punctuation.

| Application encoding | Approximate saving | Host / board complexity | Missing evidence | Verdict |
|---|---|---|---|---|
| Fold and truncate to actual visible lengths before encoding | Potentially large versus original metadata | Already implemented / none | None about present display limits | **Adopt** |
| Packed 7-bit ASCII | **12.5%** of text bits versus bytes | Low / low; shifts only | None for current printable ASCII contract | **Adopt** |
| Base-95 text | Ideal **6.57 bits/character**, versus 7 | Low–medium / medium | Whether the small saving merits another codec | **Defer** |
| Three printable characters in 20 bits | **6.67 bits/character**, 4.8% better than 7-bit | Low / low–medium; small division/unpacking work | Actual latency benefit | **Reasonable later option** |
| Six-bit common alphabet plus literal escape | Six bits for common characters, e.g. 13 for escaped ASCII | Low / low | Title/artist character distribution | **Test first** |
| Five-bit alphabet with shifts/escapes | Could help restricted text; overhead varies sharply | Medium / medium | Real corpus and preserved typography | **Test first**; reject silent uppercasing/transformation |
| Static word/token dictionary | Potentially substantial for common words and artists | Medium / medium; dictionary flash | Corpus coverage and versioning | **Defer** |
| General-purpose compression | Often little benefit or expansion on 20–40 characters | Medium / medium–high | Actual corpus measurements | **Reject initially** |
| Independently replace only changed artist/title fields | Can avoid retransmitting up to 19 or 21 characters | Low / low–medium | Recovery policy for a missed earlier field update | **Adopt after full snapshots work** |
| Prefix/suffix or edit delta | A tiny edit can become a few bytes | Medium / medium | Receiver has exactly the intended base | **Test first**, with a base identifier and periodic full snapshots |
| Playback extrapolation | Eliminates most three-second position reports | Medium / low–medium | Acceptable drift and seek detection | **Adopt if wireless playback is included** |

A concrete six-bit alphabet can contain all 52 letters, ten digits, space, and an escape. If an escaped character costs 13 bits, it beats seven-bit ASCII when fewer than approximately **14%** of characters require escaping. Measure that against actual displayed strings.

For deltas, never assume “the previous thing I sent” equals “the previous thing the board has.” Include a base version or digest; reject a delta whose base does not match. Otherwise one lost update can corrupt every later update. A full snapshot must eventually repair the chain.

I would retain **absolute snapshots as the initial wire contract**. They are naturally idempotent and tolerate an unknown receiver state.

---

**Clock sync should use an earlier identifiable edge and carry its timestamp afterward.**

The draft’s edge idea is good, but it needs a complete causality model. A timestamp cannot describe a future write completion accurately before that completion happens.

A simple implementation is:

1. Finish a known preamble edge.
2. The board remembers its local monotonic time at that edge.
3. The host records wall time immediately when that write completes.
4. The remaining frame carries that timestamp.
5. After validation, the board adds the elapsed local time since the edge.

Thus a one-second clock frame does not install a time that is already one second old.

The remaining error is the difference between **host write completion** and **board observation of the edge**, plus timestamping and local-clock error. The current evidence does not establish its sign, mean, or tail. **±20 ms remains a hypothesis.**

| Clock scheme | Wire cost | Host / board complexity | Unmeasured assumption | Verdict |
|---|---|---|---|---|
| Absolute seconds plus fractional second, referenced to preamble | About **6 payload bytes** | Low–medium / low–medium | Completion-to-board bias and jitter | **Adopt** |
| Time-of-day plus fraction, reconstruct date locally | About **4 bytes** | Low / medium | Board date is valid and close enough | **Optional later**, with periodic full date |
| Signed correction only | Often **2 bytes** | Medium / medium | Host knows the board’s current error | **Reject for initial one-way transport** |
| Future “apply at this second” edge | Small payload, but needs lead time and expiry | Medium / medium | Timing edge arrives before a known deadline | **Test first**; reject late markers |
| Frequency estimation from spaced anchors | Little extra traffic beyond syncs | Medium / medium | Delay variation is small relative to sample interval | **Test first later** |
| Two-way timestamp exchange over an ACK path | Several small messages | Medium / medium | A usable, characterized return channel | **Best future precision path** |

Do not feed unverified wireless observations into the existing USB SOF bias learner. Also check reference ownership: the current off-USB path still invokes PCF discipline; the skip is tied to `REF_SOF`, not simply `host_synced`. See [rtc.c:1514](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc/rtc.c:1514). Wireless phase corrections, PCF updates, and legacy discipline must agree rather than periodically undo each other.

The recorded 1.6 s drift over 26 minutes also matters more than nominal acquisition precision. If representative, its linear equivalent is roughly 1 ms/s. Choose the refresh interval from measured untethered drift and the desired displayed-time error.

---

**An ACK path is worth investigating before sophisticated FEC.**

The strongest evidence is [BACKLOG.md:199](/Users/jdlien/code/ak820-pro/plans/BACKLOG.md:199): unusual `A3` consumer usages already worked for the other fork on Linux/2.4G. Ordinary consumer reports work on this firmware over both wireless modes. The unknown is unusual usages through BLE and macOS.

The descriptor’s other input collections establish possible host-visible report formats. They do **not** establish corresponding SN32-to-CH582 UART commands.

| Candidate | Potential ACK cost | Host / board complexity | Missing evidence and risk | Verdict |
|---|---|---|---|---|
| Unusual `A3` consumer usages | A marker, sequence/status tokens and releases; roughly **0.3–1 s** at conservative pacing | Medium / medium | Mac receives them; no OS/app action; releases remain reliable | **Test first, highest priority** |
| Reserved byte in the `A1` keyboard report | Could carry several bits per report | Medium / medium | Module preserves it and maps it to raw input; presently forced to zero | **Test first as a protocol hypothesis** |
| Descriptor’s vendor bit | One bit/report, perhaps enough for stop-and-wait ACKs | Medium / unknown UART work | Reachability, raw visibility, actual semantics | **Test first if a mapping can be established** |
| Unused keyboard bitmap positions | Potentially several bits/report | Medium / high | Positions are genuinely unused and module can transmit them | **Defer**; incorrect mapping generates keys |
| Mouse padding or other inert fields | Potentially several bits/report | Medium / high | UART command and preserved padding exist | **Defer** |
| Mouse motion/buttons, system control, eject, Dial actions | Could convey data, but through real input actions | Medium / high | Would require suppressing observable behavior | **Reject as production ACK carriers** |
| F13–F24 or other “unbound” keyboard keys | A few bits/event | Low–medium / medium | No user/app binds them | **Reject as a generally transparent channel** |

Input Monitoring may permit raw report observation, but it does not make the OS ignore those reports. Nor does “unbound on one desktop” establish safety on this Mac.

There is also a firmware integration issue: `A1` and `A3` state reports can be coalesced near queue exhaustion, and the TX pump can give up. See [ch582f_ajazz.c:740](/Users/jdlien/code/ak820-pro/qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/bluetooth/ch582f_ajazz.c:740). An ACK must not replace a real consumer action, release a held key, or delay typing. Give normal input priority; let ACK loss cause a later retry.

A successful transport ACK should identify a transaction and mean **validated and accepted by the board**. The Mac’s current write completion means neither.

---

**CRC plus retransmission is the right starting point, but “resend later” needs a policy.**

Bluetooth reliability does not eliminate:

- UART corruption and checksum-valid false parses;
- dropped or coalesced LED reports;
- duplicated or interfering reports;
- changes caused by an OS rewrite;
- host permission failures or disconnects.

Change coding also changes the error model. If states `A → B → C` are sent and `B` disappears, the receiver observes `A → C`: two intended transitions become their XOR. That is generally **a deletion plus a changed symbol**, not a clean known erasure. If `C == A`, deduplication can remove both transitions.

| Recovery method | Cost | Host / board complexity | What must be established | Verdict |
|---|---|---|---|---|
| CRC-16, whole-frame rejection | Two bytes, roughly four or five writes | Low / low | Measured frame-loss rate is acceptable with retries | **Adopt** |
| Delayed full-state retry | One additional frame per retry | Low / low | Retry spacing decorrelates failures sufficiently | **Adopt** for new state until an ACK path exists |
| CRC-32 | Two bytes more than CRC-16, approximately **0.13 s** | Low / low | Need for lower undetected-error probability | **Optional**, especially if stronger recovery is added |
| Small independently checked chunks | Often **20–50%** overhead at these message sizes | Medium / medium | Bursts repeatedly destroy only part of a message | **Test first** |
| RS(15,11) over GF(16) | Four parity nibbles per eleven data nibbles; **93.9 bit/s** at 32 nibble writes/s before framing | Medium / medium | Symbol positions are known; error model fits | **Test first only after alignment is solved** |
| Single parity/check symbol per short block | Roughly **7–15%** overhead | Low / low–medium | At most one known-position erasure per block | **Test first** |
| Convolutional code | Common rate-½ form reduces raw payload to **64 bit/s** | Medium / high relative to this task | Mostly substitutions with maintained alignment | **Reject initially** |
| VT/deletion-correcting or marker codes | Potentially a few symbols per short block; practical framing may make it **10–25%+** | Medium–high / medium–high | Bounded insertions/deletions and explicit block boundaries | **Test first only if measurements justify it** |
| Interleaving | Little intrinsic parity cost, but more buffering and latency | Medium / medium | Burst lengths and an underlying correcting code are known | **Defer** |
| Fountain coding | IDs/seeds/checks are expensive for 30–40 characters | High / medium–high | Much larger transfers or sustained packet erasures | **Reject for this workload** |
| CRC-guided insertion/deletion search | Little wire overhead, potentially hundreds/thousands of hypotheses | Medium / high unless carefully bounded | Strict error model and a stronger final integrity check | **Reject initially** |

RS(15,11) corrects combinations satisfying `2 × errors + erasures ≤ 4`. It cannot locate arbitrary deletions in an unindexed stream.

Also, “change coding cannot map onto GF(16)” is too strong. You can encode RS symbols into bits and transport those through base-15 blocks. The problem is **maintaining correspondence between received errors and RS symbol positions**, not a mathematical incompatibility between the alphabets.

CRC-16 is reasonable for short state messages, with validation of types, lengths, dates, and canonical encodings. Its approximately `1/65536` random-error miss probability is conditional on an erroneous candidate; it is not an absolute guarantee, and CRC is not authentication.

Without ACKs, “no duplicates” cannot mean “no retransmissions.” I would send a new snapshot once, repeat the current snapshot after a short delay, then refresh periodically. Cancel an old retry when newer state arrives. A missed title should not wait until the next track.

**Timing gaps should cause bounded abandonment, not speculative symbol insertion.**

- A gap is not successful frame termination. Only the declared length and integrity check establish completion.
- Keep 150 ms as an initial **warm-link inactivity threshold**, subject to measurement.
- Warm the link before starting framing; do not allow the measured approximately 236 ms wake delay to occur inside a frame.
- Use a separate maximum frame lifetime, so occasional reports cannot keep a partial frame alive indefinitely.
- Reset on link/slot changes, malformed framing, overflow, or disqualifying lock activity.
- After aborting, re-arm through an explicit preamble; do not search every payload position as a new frame.
- Do not round a gap to a number of missing symbols.

Even actual UART timestamps would not prove whether a delayed packet was lost. Erasure recovery needs sequence information, a verified transmission-slot contract, or a bounded alignment decoder as well.

---

**Sync can exclude ordinary human lock traffic under a defined model; it cannot provide an unconditional “never.”**

On the measured Mac model, ordinary lock keys cannot create upward transitions in bits 3–4. A preamble requiring **multiple upward transitions** in those bits therefore has a useful structural distinction from human Num/Scroll/Caps traffic.

Use an absolute training pattern such as:

`upper bits: 00 → 11 → 00 → 11`

with Num and Scroll held at their baseline throughout. A Caps rewrite that merely preserves or clears the unnamed bits cannot generate that entire sequence independently.

That guarantee depends on what the OS actually does. If it can replay older cached LED bytes containing different high-bit values, the argument changes. Test six currently checks only a held value, not that case.

Two further boundaries matter:

- A corrupt data stream can resemble a preamble. Restrict where the parser searches, and require protected header and payload validation.
- With **only Num and Scroll**, every possible symbol sequence can in principle be generated by lock-key activity. Timing and a magic number make accidental acceptance unlikely; they cannot make it impossible.

For Windows, I would test per-device indicator access first. If the only available path toggles real global locks, **reject it for normal production use**. An idle check cannot guarantee that the user stays idle or that applications/KVMs ignore the toggles. Restoring the state afterward does not undo actions taken during transmission.

---

**Whole-frame base-15 packing is unnecessary, but it is not inherently too slow.**

The draft’s decoder multiplies by 15; it does not need division. A Cortex-M0’s lack of hardware divide is therefore not the main objection. A few thousand byte operations spread across seconds are probably affordable, though that is an inference.

The better alternative is:

- Take **31 bits**.
- Encode them as **eight base-15 digits**.
- Decode into one `uint32_t` using `acc = acc * 15 + digit`.
- Reject values outside the 31-bit range.
- Emit the bits and reset the accumulator.

`15⁸ > 2³¹`, so this carries **3.875 bits/write**, approximately **124 bit/s**: **99.2% of the change alphabet’s theoretical capacity**.

This gives constant work per symbol, small bounded state, straightforward test vectors, and bounded arithmetic error propagation. It does not independently correct deletion-induced block misalignment; CRC still rejects that frame.

For a three-bit change alphabet, **14 bits in five base-7 digits** gives **2.8 bits/write**, or **89.6 bit/s**, also very close to capacity.

The 25 ms limit is a limit for the **entire main-loop pass**, not a decoder allocation. Feed a small state machine from the parser, update CRC incrementally, and defer application changes. No LCD work, waiting, allocation, or unbounded error search belongs there. Remember that the UART loop can drain up to 64 bytes in one pass, delivering several symbols together.

---

**The six pending tests should select the physical channel, not yet the complete error-control scheme.**

| Test | Decision it should make | What needs strengthening |
|---|---|---|
| **Width** | Which bits are independently usable; choose two-, three-, or four-bit alphabet | Compare exact values and accepted counts. Treat Caps-changing patterns as controlled experiments, not production behavior. |
| **High** | Whether padding survives, is masked, or causes rejection | Existing trace precedes the low-five-bit guard, which is useful. Add mixed low/high patterns and later test at full speed. A nonzero received high bit with zero accepted count is still an important positive result. |
| **Repeats** | Whether plain-value coding is possible | Repeat all candidate values, especially bit 4 and combined masks, at full speed as well as 200 ms spacing. |
| **Speed** | Fastest pace with exact delivery and acceptable input latency | Preserve per-write issue/completion identity; compare complete board sequence. Completion rate alone is insufficient. |
| **Random** | Initial evidence of substitutions, deletions, duplicates, and merging | Forty-four values are a smoke test. They deliberately avoid repeats and therefore cannot validate plain-value coding. Add full-frame and adversarial patterns. |
| **Caps** | How OS updates interact with channel-owned bits | Test Caps initially on; physical and software changes; another keyboard; transitions during changing data; delayed reassertions; both directions. Inspect the whole sequence, not just whether one expected value appeared. |

A zero-error run of 44 independent symbols only gives an approximately **6.6% one-sided 95% upper bound** on per-symbol failure probability. That is nowhere near a reliability result.

For perspective, under an independent-loss model:

- 0.1% loss per symbol gives approximately **93% success** for a 70-symbol frame.
- 1% loss per symbol gives approximately **49% success**.

Real burst correlation means full-message measurements are essential. Approximately 3,000 zero-failure **frames**, not symbols, would bound frame failure near 0.1% under the usual independent-trial assumption.

The missing tests most likely to change the design are:

1. **Caps race and real-light restoration.** Start Caps on and off; change locks during every protocol phase; preserve current state rather than restoring an obsolete snapshot. Verify typing and indicators independently.
2. **Failure at every write boundary.** Lock the Mac, terminate the sender, disconnect, switch slots, and lose the final restore. Include a stuck outstanding write and a refusal immediately after a successful data write.
3. **Actual frame-length capture.** The trace ring has 52 entries; the proposed messages are longer. Use adequate bounded capture or full-frame counters plus selected traces, rather than silently relying on a wrapped trace.
4. **Insertion/deletion fault injection.** Delete, duplicate, merge, replace, and insert reports—including plausible Caps rewrites—at every position. Require rejection or exact recovery, never a corrupted commit.
5. **Performance while doing real work.** Typing, held keys, Caps, media keys, page exits, overlays, text repaint and clock application. Check delivered input and UART/TX counters as well as the ≥25 ms counter.
6. **Clock ground truth.** Correlate host issue/completion with board observations; distinguish parser timestamp error from radio delay. Repeat off USB, because cable attachment changes clock discipline and operating conditions.
7. **Long and pathological patterns.** Repeated zero payloads, repeated characters, long runs of unchanged Scroll, maximum lengths, invalid headers, CRC tails, wake/sleep transitions and RF contention.
8. **Targeted ACK and dongle probes.** These could change the architecture more than choosing between two FEC codes.

---

**The design I would build is a small state-message protocol with a conservative default and an optional faster profile.** The following is specific enough to implement without committing to unmeasured capabilities.

1. **Use one serialized wireless writer in the existing daemon.**

   Allow at most one outstanding write. Preserve the current authoritative Caps state in every report; never use Caps as data. Observe lock-state changes and abort/restart on interference.

   Keep user-state tracking separate from the target device’s LED cache: the daemon’s own writes contaminate that cache. Restore the latest known user state, not a hard-coded zero or the frame-start Caps value.

   On `NotPermitted`, stop sending. On unlock or reconnect, warm up and send a fresh snapshot and clock anchor. Mark wireless state as **sent**, not confirmed.

2. **Default to Num plus bits 3–4; leave Scroll unchanged.**

   Use base-7 change coding, 14 bits per five changes. This avoids the 1.5-second Scroll problem during normal transmission and failures.

   Add four-light base-15 coding as an optional profile after the Scroll restoration and interference tests pass. If padding bits survive, prioritize a profile using them to leave visible locks alone.

   This still requires the Caps preservation tests. No alphabet eliminates the whole-byte Caps race.

3. **Use one common framing format.**

   A concrete layout is:

   ```text
   baseline / quiet interval / training preamble
   header: version+profile+type, sequence, payload_length, header_crc8
   payload
   frame_crc16
   restoration trailer
   ```

   Encode the header with the conservative three-bit alphabet so the header can select the payload profile.

   Bound payload length to **48 bytes**. Use fixed storage. Validate version, type, flags, length and header CRC before accepting a body.

   For example, specify CRC-8 polynomial `0x07`, initial zero, and CRC-16/CCITT-FALSE polynomial `0x1021`, initial `0xFFFF`, no reflection or final XOR. Define byte order and bit order explicitly. Compute frame CRC over the complete decoded header and payload.

4. **Make acquisition and packing canonical.**

   Write the current baseline with bits 3–4 clear, then allow an initial approximately **200 ms quiet interval**. Train with `00 → 11 → 00 → 11` on bits 3–4, holding Num/Scroll baseline values.

   Encode payload/CRC in 14-bit/base-7 or 31-bit/base-15 blocks. Use a fixed table for the final partial block’s digit count. Reject out-of-range block values and nonzero unused padding.

   During a frame, unchanged data reports are ignored. Caps transitions abort the candidate while still updating the normal Caps indication. Do not try to salvage that frame initially.

5. **Give restoration a defined protocol phase.**

   Use a two-change trailer whose first value has baseline Num/Scroll and one unnamed bit set, followed by the exact baseline. Choose that intermediate unnamed bit deterministically so both writes change the data value.

   Commit the pending message only after the expected trailer. This does not make restoration infallible, but avoids declaring success before observing the planned return.

   The optional four-light profile should use the same two-change restoration checkpoint after every three full base-15 blocks—24 payload writes—and enforce a host wall-time limit as well. Checkpointing costs at most roughly **8%** at that spacing.

   **An absolute 1.5-second physical-state guarantee remains impossible if the host loses permission or connectivity before restoration.** That is why the default profile leaves Scroll alone. Firmware can restore its displayed shadow state after an abort, but that does not restore the module’s or host’s LED cache.

6. **Use bounded failure handling.**

   Start with approximately 150 ms between accepted symbols and an approximately eight-second total candidate lifetime; tune against measured tails. Neither timeout implies success.

   Abort on malformed header, overflow, bad CRC, bad trailer, disconnection or slot change. Return to acquisition rather than guessing missing symbols.

   Keep all decode work incremental. Stage a validated text/clock update for the normal application path; never draw from the UART parser.

7. **Send compact absolute application state first.**

   For text, use:

   - One byte for artist length, bounded to 19.
   - One byte containing title length, bounded to 21, and icon.
   - Concatenated seven-bit ASCII, with canonical zero padding.

   That is **29 payload bytes for 30 displayed characters**, or **37 for the full 40**. Add small separate clear/status messages. Defer dictionaries and previous-string deltas.

   Refresh a new snapshot once more roughly two seconds after completion if it is still current. Retain a periodic full refresh initially, such as the existing 30 seconds, so a missed update and board reset recover. Compare semantic content to avoid needless repainting on duplicates.

   If playback position is included, extrapolate locally and send changes/seeks plus occasional correction; do not reproduce the three-second USB traffic cadence.

8. **Anchor clock messages to the final training edge.**

   Record that edge on the board. After its write completes, construct a six-byte timestamp—32-bit seconds in an explicitly defined epoch/time convention plus 16-bit milliseconds.

   On validated commit, advance it by elapsed board monotonic time. Use a measured transport bias if justified; otherwise report the uncertainty honestly. A retry gets a fresh edge and timestamp.

   Integrate this with RTC reference ownership and the existing nonblocking PCF writer; do not modify USB calibration from wireless observations.

9. **Add ACKs before adding heavy correction.**

   If a harmless consumer/vendor return path is proven, add stop-and-wait acknowledgement of accepted sequence IDs, bounded retries, and later confirmed-base deltas. That is likely to earn its complexity sooner than RS with inferred erasures.

With the example header, trailer and packing above, **calculated warm-link transmission times** are approximately:

| Displayed text | Conservative three-bit profile | Four-bit profile with checkpoints |
|---|---:|---:|
| 30 characters | **3.34 s** | **2.69 s** |
| 40 characters | **4.06 s** | **3.28 s** |

Allow roughly another **0.4 s** for the proposed quiet interval and a cold-link wake, with actual tails to be measured. These are estimates, not board-delivery measurements. The extra time buys explicit restoration, protected framing, bounded decoding, and recovery from an unknown receiver state.