**Four P2s remain in `pad_check.py`.** I reproduced them with in-memory checks; no files or hardware were changed.

1. **P2 — `advance` still accepts unrelated failures.** [pad_check.py:124](/Users/jdlien/code/ak820-pro/scripts/pad_check.py:124)

   **Scenario:** normal sleep records with `key_gain=0`, `new_ge25=1`, or a UART overrun still produce `complete=True, advance=True`. `strict_pass`, `logger_only`, and `failing` do not affect advancement. A regression record also immediately overrides other abnormal wake evidence. An abnormal child exit after writing its JSON is accepted too.

   **Fix:** require completed execution **and no unexplained failures**. Allow the specific v09 logger discrepancy, and explicitly allow failures explained by the reproduced regression on each affected cycle; retain independent gates for lost keys, stalls, transport errors and other abnormal wakes. Reject child exit statuses other than the expected 0/1.

   The existing gates correctly stop ordinary partial runs, `summary.stopped`, resets, open recovery and reported incomplete cleanup.

2. **P2 — `failing_checks()` omits strict checks and falsely labels failures logger-only.** [pad_check.py:80](/Users/jdlien/code/ak820-pro/scripts/pad_check.py:80)

   Compared with [deep_cycles.py’s `good` expression](/Users/jdlien/code/ak820-pro/scripts/deep_cycles.py:255), it omits `record.slept`, key wake reason, the rig’s P2 bit, zero strays, and all six `LINK_GATED` counters. Omitting Mac timing is correct because this invokes `--board-only`.

   **Scenario:** adding one UART overrun to an otherwise passing cycle produces `failing=[[]]`, `logger_only=True`, and `ADVANCE`. This omission also hides actual overruns in the saved `deep-cycles-2g4` and transaction runs. Separately, `pcf_ok=False` with **present** logger edges is called “power-log edges only,” even when the PCF disagrees substantially.

   **Fix:** enumerate every applicable strict predicate. Require the specific missing-edge condition—`asleep_s is None`, `pcf_ok=False`, and no other failures—for the v09 exception. Empty failure sets must not explain a failed cycle.

3. **P2 — Regression detection ignores the ring and does not require an actual sleep.** [pad_check.py:70](/Users/jdlien/code/ak820-pro/scripts/pad_check.py:70)

   **Scenario:** the last cycle records show key wakes, while the ring retains one-second charge wakes with b2a0’s `p1="0xbd102000"`. The result is `other/STOP`, although the ring establishes the regression. Conversely, an unslept zero-second charge record with P1 zero is accepted as regression.

   **Fix:** recognize the signature in both representations, requiring `slept` for records and `flags & 0x40` for ring entries. Parse hexadecimal ring GPIO values and use `counted_s`. For ring charge evidence, test the missing **B16 charge bit** (`p1 & 0x10000 == 0`), not equality to zero: b2a0’s corrupted ring value is nonzero. Keep legitimate charge wakes distinct.

4. **P2 — The first valid ring entry is discarded.** [pad_check.py:72](/Users/jdlien/code/ak820-pro/scripts/pad_check.py:72)

   The firmware starts ring numbering at zero; [`wakes()`](/Users/jdlien/code/ak820-pro/scripts/overrun_diag.py:152) already selects valid entries.

   **Scenario:** normal cycle records plus a backstop at ring sequence zero produce `normal/ADVANCE`. Changing only that entry’s sequence to one produces `other/STOP`. The first entry is also omitted from printed evidence.

   **Fix:** remove both truthiness filters on `seq`; inspect and print every entry returned by `wakes()`.

The historical cycle classifications match your report: b2a0 and all three `deep-cycles-2g4*` runs are regression; the VT runs and both probe variants are normal. The saved probe rings agree, and v09’s failed cycle has exactly `{asleep_s, pcf_ok}`. The manifest validation and narrowed layout disclaimer address the prior P3s.

Retiring the runner removes P1s 1–2’s automatic subsequent/final flashing paths. Your procedure addresses P1 3 operationally **provided this session is the sole flashing actor**; requiring `pgrep` exit 1 correctly rejects process-check errors, but does not provide a shared lock. [`flash.sh`](/Users/jdlien/code/ak820-pro/flash.sh:227) still detaches its writer, so after interruption its termination must be established before another flash. Use `--control` for both b2a0 checks.

**Verdict: the stated attended flashing procedure is acceptable; fix these P2s before relying on `pad_check.py`’s ADVANCE decision.**