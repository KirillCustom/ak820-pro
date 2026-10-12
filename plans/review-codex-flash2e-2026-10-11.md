No blocking findings in `efd5b0f74a`. Read-only; no files modified.

1. **Tile background is safe.** It occupies x0–11, y27–38 (two lines), y34–45 (big), or y35–46 (small). Line 0 starts at x14; line 1 starts at y41 only in the two-line layout. The top strip ends at y24. Overlay entry suppresses the icon; retirement and icon movement retain the existing cleanup and `icon_clobbered` repaint ordering.

2. **Both force calls have a blank band, y27–55.** Restore stages 9–13 and dashboard draws stay outside it. Old queued glyphs are discarded; the pump prioritizes restore over glyphs, and housekeeping is gated. Healing uses that same restore. Raw-HID text pushes only update state.

3. **Pixel identity verified programmatically:** all 144 pixels match the old runs on black for each icon—84 green, 96 amber, 120 red pixels respectively. RGB565 byte order matches; NONE still draws nothing.

4. **No new issue found.** The static tile adds 288 bytes; synchronous RAM transmission makes buffer reuse safe. Hardware timing remains unverified.

flash