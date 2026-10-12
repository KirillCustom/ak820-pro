No new flash blocker found.

1. **Indices correct:** 24 slots, indices 0–23. Restore steps 1–14 map to 0–13; repeated switch-case 5 stays in slot 12. Heal starts at slot 0. Probe pages are bounds-checked.
2. **Raw HID:** main-thread execution, fixed 32-byte buffer. Paused dashboard entry is rejected. Re-entry suspends an unfinished restore; camera exit restarts it at step 1. Existing caveat: starting animation while debug/restore is active is not fully gated; unchanged from 2c.
3. **Timing:** 349.525 ms wrap comfortably covers the measured 31–33 ms stalls; repeated stages are timed individually. Export saturates at 65.535 ms. Pathological calls lasting ≥349.525 ms can alias, so this is not a general hang-duration probe.
4. **Artifacts:** hashes match manifests; dev marker present, daily absent; build guards correct. Independently confirmed exactly five daily binary differences, all token-generation instructions in `health_fill6`.

flash