/* Host test of the lock-LED handler's validator (lockchan_media.c), every row
 * of plans/LOCK-LED-CHANNEL-PLAN.md's R2-9 table, and type 3, the transport
 * (10-20, "Play times"), with lockchan_media_position. Build and run:
 *
 *   gcc -std=c11 -Wall -Wextra -I qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro \
 *       scripts/lockchan_media_test.c \
 *       qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/lockchan_media.c -o lcm && ./lcm
 *
 * (On Windows, mingw64's gcc first on PATH: CLAUDE.md.) */
#include <stdio.h>
#include <string.h>
#include "lockchan_media.h"
#include "rtc/rtc_payload.h"

static int fails;

/* The board's hooks, faked: who owns the clock, what the setter answers, and
 * what it was asked. */
static bool              fk_owned;
static uint8_t           fk_status;
static int               fk_sets;
static lockchan_clock_t  fk_at;
static bool fk_owned_elsewhere(void) { return fk_owned; }
static uint8_t fk_set(const lockchan_clock_t *at, int16_t *off) {
    fk_sets++;
    fk_at = *at;
    *off  = -42;
    return fk_status;
}
static const lockchan_clock_hooks_t fk_hooks = {fk_owned_elsewhere, fk_set};
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

static lockchan_frame_t frame(uint8_t type, const uint8_t *p, uint8_t len) {
    lockchan_frame_t f;
    memset(&f, 0, sizeof f);
    f.type = type;
    f.len  = len;
    if (len) memcpy(f.payload, p, len);
    return f;
}

static lockchan_frame_t now_playing(uint8_t icon, const char *l0, const char *l1) {
    uint8_t p[LOCKCHAN_MAX_PAYLOAD];
    uint8_t n0 = (uint8_t)strlen(l0), n1 = (uint8_t)strlen(l1);
    p[0] = icon;
    p[1] = n0;
    memcpy(&p[2], l0, n0);
    memcpy(&p[2 + n0], l1, n1);
    return frame(1, p, (uint8_t)(2 + n0 + n1));
}

/* parse must leave *m untouched on false */
static bool rejects(lockchan_frame_t f) {
    lockchan_media_t m;
    memset(&m, 0xA5, sizeof m);
    lockchan_media_t before = m;
    bool ok = lockchan_media_parse(&f, &m);
    if (!ok && memcmp(&m, &before, sizeof m) != 0) {
        printf("FAIL: a rejected frame wrote its output\n");
        fails++;
    }
    return !ok;
}

int main(void) {
    lockchan_media_t m;

    /* Type 1, a normal title. */
    lockchan_frame_t f = now_playing(1, "Cosmic Cadet", "Europa - The Advent");
    CHECK(lockchan_media_parse(&f, &m));
    CHECK(m.type == 1 && m.icon == 1 && m.n0 == 12 && m.n1 == 19);
    CHECK(memcmp(m.l0, "Cosmic Cadet", 12) == 0 && memcmp(m.l1, "Europa - The Advent", 19) == 0);

    /* The limits exactly: 19 + 21, 42 bytes. */
    f = now_playing(3, "1234567890123456789", "123456789012345678901");
    CHECK(f.len == 42 && lockchan_media_parse(&f, &m) && m.n0 == 19 && m.n1 == 21);
    /* One over on either line. */
    CHECK(rejects(now_playing(1, "12345678901234567890", "x")));
    CHECK(rejects(now_playing(1, "x", "1234567890123456789012")));

    /* Empty lines are allowed: they clear that line. Both empty: len 2. */
    f = now_playing(0, "", "");
    CHECK(f.len == 2 && lockchan_media_parse(&f, &m) && m.n0 == 0 && m.n1 == 0);
    f = now_playing(2, "", "Only line one");
    CHECK(lockchan_media_parse(&f, &m) && m.n0 == 0 && m.n1 == 13);

    /* Length below 2. */
    uint8_t one = 1;
    CHECK(rejects(frame(1, &one, 1)));
    CHECK(rejects(frame(1, NULL, 0)));

    /* n0 past what the frame holds. */
    uint8_t bad_n0[] = {1, 5, 'a', 'b'};
    CHECK(rejects(frame(1, bad_n0, sizeof bad_n0)));

    /* Icons: 0-3 only; bit 7 (the reserved playback flag) rejects. */
    for (unsigned icon = 4; icon < 256; icon++) {
        lockchan_frame_t g = now_playing((uint8_t)icon, "a", "b");
        if (!rejects(g)) { printf("FAIL: icon %u accepted\n", icon); fails++; }
    }

    /* Text: printable ASCII only, every position; a rejection writes nothing
     * (through rejects(): codex, handler review, 3). */
    for (unsigned c = 0; c < 256; c++) {
        bool ok = c >= 0x20 && c <= 0x7E;
        uint8_t p[] = {1, 1, (uint8_t)c, 'x'};
        if (rejects(frame(1, p, 4)) == ok) { printf("FAIL: byte 0x%02x in line 0\n", c); fails++; }
        p[2] = 'x'; p[3] = (uint8_t)c;
        if (rejects(frame(1, p, 4)) == ok) { printf("FAIL: byte 0x%02x in line 1\n", c); fails++; }
        /* The last byte of a maximum-length frame. */
        uint8_t full[42];
        memset(full, 'a', sizeof full);
        full[0] = 0; full[1] = 19; full[41] = (uint8_t)c;
        if (rejects(frame(1, full, 42)) == ok) { printf("FAIL: byte 0x%02x last of 42\n", c); fails++; }
    }

    /* Type 3, transport: exactly [icon][state][pos][dur], big endian. */
    {
        uint8_t p[] = {1, 1, 0x01, 0x2C, 0x0E, 0x10};
        lockchan_frame_t g = frame(3, p, sizeof p);
        CHECK(lockchan_media_parse(&g, &m));
        CHECK(m.type == 3 && m.icon == 1 && m.state == 1 && m.pos == 300 && m.dur == 3600);
        /* Paused is a valid state: the board then shows the clock. */
        p[1] = 0;
        g = frame(3, p, sizeof p);
        CHECK(lockchan_media_parse(&g, &m) && m.state == 0);
        /* Any other state, any icon past 3. */
        p[1] = 2;
        CHECK(rejects(frame(3, p, sizeof p)));
        p[1] = 1;
        for (unsigned icon = 4; icon < 256; icon++) {
            p[0] = (uint8_t)icon;
            if (!rejects(frame(3, p, sizeof p))) { printf("FAIL: type 3 icon %u accepted\n", icon); fails++; }
        }
        p[0] = 0;
        /* Exactly 6 bytes. */
        for (uint8_t len = 0; len <= LOCKCHAN_MAX_PAYLOAD; len++) {
            uint8_t q[LOCKCHAN_MAX_PAYLOAD];
            memset(q, 0, sizeof q);
            if (len == 6) continue;
            if (!rejects(frame(3, q, len))) { printf("FAIL: type 3 of %u bytes accepted\n", len); fails++; }
        }
        /* The position and duration are not text: any bytes. */
        uint8_t q[] = {3, 1, 0xFF, 0x00, 0x00, 0xFF};
        g = frame(3, q, sizeof q);
        CHECK(lockchan_media_parse(&g, &m) && m.icon == 3 && m.pos == 0xFF00 && m.dur == 0x00FF);
    }
    /* Type 1 stays frozen: icon bit 7 still rejects, at any length. */
    {
        uint8_t p[47];
        memset(p, 'a', sizeof p);
        p[0] = 0x81; p[1] = 19;
        CHECK(rejects(frame(1, p, 42)));
        CHECK(rejects(frame(1, p, 47)));
    }
    /* The position at the commit: the frame's plus anchor to commit, rounded. */
    {
        lockchan_media_t pm = {.pos = 100, .dur = 3600};
        CHECK(lockchan_media_position(&pm, 1000, 1000) == 100);
        CHECK(lockchan_media_position(&pm, 1000, 1499) == 100);
        CHECK(lockchan_media_position(&pm, 1000, 1500) == 101);
        CHECK(lockchan_media_position(&pm, 1000, 3600) == 103);
        pm.pos = 3599;
        CHECK(lockchan_media_position(&pm, 0, 5000) == 3600);       /* never past the duration */
        pm.dur = 0; pm.pos = 0xFFFE;
        CHECK(lockchan_media_position(&pm, 0, 5000) == 0xFFFF);     /* a stream: saturates */
        pm.pos = 10; pm.dur = 0;
        CHECK(lockchan_media_position(&pm, 0xFFFFF000u, 0x00000400u) == 15);   /* the timer wraps */
    }

    /* Over 42 even when each line would fit by n0 alone. */
    uint8_t big[43];
    memset(big, 'a', sizeof big);
    big[0] = 1; big[1] = 19;
    CHECK(rejects(frame(1, big, 43)));

    /* Type 2: exactly empty. */
    CHECK(lockchan_media_parse(&(lockchan_frame_t){.type = 2, .len = 0}, &m) && m.type == 2);
    uint8_t z = 0;
    CHECK(rejects(frame(2, &z, 1)));

    /* Unknown types, 0 and 4-15 (type 3 empty rejects too: it is 6 bytes). */
    for (uint8_t t = 0; t < 16; t++) {
        if (t == 1 || t == 2) continue;
        if (!rejects(frame(t, NULL, 0))) { printf("FAIL: type %u accepted\n", t); fails++; }
    }

    /* What a type 1 does to the readout (lockchan_media_readout). */
    {
        enum { OFF = LOCKCHAN_READOUT_OFF, KEEP = LOCKCHAN_READOUT_KEEP, GUESS = LOCKCHAN_READOUT_GUESS };
        lockchan_media_t t;
        lockchan_frame_t f1 = now_playing(1, "Song", "Artist");
        CHECK(lockchan_media_parse(&f1, &t));
#define RO(shown, a, b, icon) lockchan_media_readout(&t, shown, a, (uint8_t)strlen(a), b, (uint8_t)strlen(b), icon)
        CHECK(RO(true, "Song", "Artist", 1) == KEEP);     /* the refresh, the wake's resend */
        CHECK(RO(false, "Song", "Artist", 1) == OFF);     /* the title expired, or a wake */
        CHECK(RO(true, "Song", "Artist", 2) == OFF);      /* resumed: the position is the type 3's */
        CHECK(RO(true, "Song", "Artist", 0) == OFF);
        CHECK(RO(true, "Other", "Artist", 1) == GUESS);   /* a new track */
        CHECK(RO(true, "Song", "Other", 1) == GUESS);     /* line 1 alone is a new track too */
        CHECK(RO(true, "Other", "Artist", 2) == GUESS);   /* a new track from paused, playing */
        CHECK(RO(true, "Sang", "Artist", 1) == GUESS);    /* the same length, other bytes */
        CHECK(RO(true, "Song", "Artisu", 1) == GUESS);
        CHECK(RO(true, "Son", "Artist", 1) == GUESS);     /* a prefix is not the same line */
        CHECK(RO(true, "Song!", "Artist", 1) == GUESS);
        CHECK(RO(true, "Song", "", 1) == GUESS);
        CHECK(RO(false, "Other", "Artist", 1) == OFF);
        /* Not playing: the readout goes, whatever the slot held. */
        lockchan_frame_t f2 = now_playing(2, "Song", "Artist");
        CHECK(lockchan_media_parse(&f2, &t));
        CHECK(RO(true, "Song", "Artist", 2) == OFF);
        CHECK(RO(true, "Song", "Artist", 1) == OFF);      /* paused */
        CHECK(RO(true, "Other", "Artist", 1) == OFF);
        lockchan_frame_t f3 = now_playing(3, "Song", "Artist");
        CHECK(lockchan_media_parse(&f3, &t));
        CHECK(RO(true, "Song", "Artist", 3) == OFF);
        lockchan_frame_t f0 = now_playing(0, "Song", "Artist");
        CHECK(lockchan_media_parse(&f0, &t));
        CHECK(RO(true, "Song", "Artist", 0) == OFF);
        /* Empty lines, the play icon alone, are a title like any other. */
        lockchan_frame_t fe = now_playing(1, "", "");
        CHECK(lockchan_media_parse(&fe, &t));
        CHECK(RO(true, "", "", 1) == KEEP);
        CHECK(RO(true, "Song", "", 1) == GUESS);
        /* Types 2 and 3 never reach it, but would get OFF. */
        CHECK(lockchan_media_parse(&(lockchan_frame_t){.type = 2, .len = 0}, &t));
        CHECK(RO(true, "", "", 1) == OFF);
        uint8_t tp[6] = {1, 1, 0, 10, 0, 100};
        lockchan_frame_t ft = frame(3, tp, 6);
        CHECK(lockchan_media_parse(&ft, &t));
        CHECK(RO(true, "", "", 1) == OFF);
#undef RO
        /* The guess's position: 0 at the anchor, the frame's airtime added. */
        lockchan_media_t start = {0};
        CHECK(lockchan_media_position(&start, 1000, 4499) == 3);
        CHECK(lockchan_media_position(&start, 1000, 4500) == 4);
        CHECK(lockchan_media_position(&start, 1000, 1000) == 0);
    }

    /* Type 4, the clock: raw HID's RTC_SET_TIME_MS body, flags 0, bias
     * 0x7FFF; and its advance by the anchor-to-set time (codex, the wireless
     * clock's review, note 14: midnight, leap day, year end, the upper year,
     * the timer's wrap). */
    {
#define CLK(yy, mo, dd, wd, hh, mi, ss, ms) \
        {yy, mo, dd, wd, hh, mi, ss, (uint8_t)((ms) & 0xFF), (uint8_t)((ms) >> 8), 0, 0xFF, 0x7F}
        lockchan_clock_t c, o;
        uint8_t ok[12] = CLK(26, 10, 20, 2, 21, 59, 59, 999);
        lockchan_frame_t f = frame(4, ok, 12);
        CHECK(lockchan_clock_parse(&f, &c));
        CHECK(c.yy == 26 && c.mo == 10 && c.dd == 20 && c.wd == 2 && c.hh == 21 && c.mi == 59 && c.ss == 59 && c.ms == 999);
        /* Not a display frame: the media parser rejects it whole. */
        lockchan_media_t m;
        CHECK(!lockchan_media_parse(&f, &m));
        /* Exact length; flags 0; bias exactly the sentinel. */
        lockchan_frame_t fs = frame(4, ok, 11);
        CHECK(!lockchan_clock_parse(&fs, &c));
        uint8_t bad[12];
        memcpy(bad, ok, 12); bad[9] = 1;                           /* FORCE_STEP */
        f = frame(4, bad, 12); CHECK(!lockchan_clock_parse(&f, &c));
        memcpy(bad, ok, 12); bad[9] = 2;                           /* SKIP_PCF */
        f = frame(4, bad, 12); CHECK(!lockchan_clock_parse(&f, &c));
        memcpy(bad, ok, 12); bad[10] = 0; bad[11] = 0;             /* a bias of 0 ppm */
        f = frame(4, bad, 12); CHECK(!lockchan_clock_parse(&f, &c));
        /* raw HID's rules: the calendar, the range, ms. */
        uint8_t feb29_27[12] = CLK(27, 2, 29, 0, 0, 0, 0, 0);
        f = frame(4, feb29_27, 12); CHECK(!lockchan_clock_parse(&f, &c));
        uint8_t feb29_28[12] = CLK(28, 2, 29, 2, 0, 0, 0, 0);
        f = frame(4, feb29_28, 12); CHECK(lockchan_clock_parse(&f, &c));
        uint8_t y25[12] = CLK(25, 12, 31, 3, 0, 0, 0, 0);
        f = frame(4, y25, 12); CHECK(!lockchan_clock_parse(&f, &c));
        uint8_t y99[12] = CLK(99, 1, 1, 4, 0, 0, 0, 0);
        f = frame(4, y99, 12); CHECK(!lockchan_clock_parse(&f, &c));
        uint8_t ms1000[12] = CLK(26, 1, 1, 4, 0, 0, 0, 1000);
        f = frame(4, ms1000, 12); CHECK(!lockchan_clock_parse(&f, &c));
        uint8_t sec60[12] = CLK(26, 1, 1, 4, 0, 0, 60, 0);
        f = frame(4, sec60, 12); CHECK(!lockchan_clock_parse(&f, &c));
        /* Another type with a clock's body is not a clock. */
        f = frame(3, ok, 12); CHECK(!lockchan_clock_parse(&f, &c));

        /* The advance: nothing, within the second, across midnight. */
        f = frame(4, ok, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 0, &o) && o.ss == 59 && o.ms == 999 && o.dd == 20 && o.wd == 2);
        CHECK(lockchan_clock_advance(&c, 1, &o) && o.hh == 22 && o.mi == 0 && o.ss == 0 && o.ms == 0 && o.dd == 20);
        uint8_t eod[12] = CLK(26, 10, 20, 2, 23, 59, 59, 900);
        f = frame(4, eod, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 150, &o) && o.dd == 21 && o.mo == 10 && o.hh == 0 && o.mi == 0 && o.ss == 0 &&
              o.ms == 50 && o.wd == 3);
        /* Saturday into Sunday: the host's weekday numbering wraps at 7. */
        uint8_t sat[12] = CLK(26, 10, 24, 6, 23, 59, 59, 999);
        f = frame(4, sat, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 1, &o) && o.dd == 25 && o.wd == 0);
        /* Leap day, both ways. */
        uint8_t feb28_28[12] = CLK(28, 2, 28, 1, 23, 59, 59, 999);
        f = frame(4, feb28_28, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 1, &o) && o.mo == 2 && o.dd == 29);
        uint8_t feb28_27[12] = CLK(27, 2, 28, 0, 23, 59, 59, 999);
        f = frame(4, feb28_27, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 1, &o) && o.mo == 3 && o.dd == 1);
        /* Year end; and the upper year's end leaves the range. */
        uint8_t ye[12] = CLK(26, 12, 31, 4, 23, 59, 58, 500);
        f = frame(4, ye, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 2000, &o) && o.yy == 27 && o.mo == 1 && o.dd == 1 && o.ss == 0 && o.ms == 500);
        uint8_t ye98[12] = CLK(98, 12, 31, 3, 23, 59, 59, 999);
        f = frame(4, ye98, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, 0, &o) && o.yy == 98);
        CHECK(!lockchan_clock_advance(&c, 1, &o));
        /* The longest trusted anchor, and one past it. */
        f = frame(4, ok, 12); lockchan_clock_parse(&f, &c);
        CHECK(lockchan_clock_advance(&c, LOCKCHAN_CLOCK_MAX_ELAPSED_MS, &o) && o.hh == 22 && o.mi == 0 && o.ss == 59);
        CHECK(!lockchan_clock_advance(&c, LOCKCHAN_CLOCK_MAX_ELAPSED_MS + 1, &o));
        /* The timer's wrap: the handler passes now - anchor, unsigned. */
        uint32_t anchor = 0xFFFFFF00u, now = 0x00000064u;   /* 356 ms later */
        CHECK(lockchan_clock_advance(&c, now - anchor, &o) && o.hh == 22 && o.ss == 0 && o.ms == 355);
        /* Every day of 2026-2098 round-trips one second past its end. */
        int days_ok = 1;
        for (uint8_t yy = 26; yy <= 97 && days_ok; yy++)
            for (uint8_t mo = 1; mo <= 12 && days_ok; mo++) {
                uint8_t dim = rtc_days_in_month((uint16_t)(2000 + yy), mo);
                uint8_t last[12] = CLK(yy, mo, dim, 0, 23, 59, 59, 0);
                f = frame(4, last, 12);
                if (!lockchan_clock_parse(&f, &c) || !lockchan_clock_advance(&c, 1000, &o)) { days_ok = 0; break; }
                uint8_t nmo = mo == 12 ? 1 : mo + 1, nyy = mo == 12 ? yy + 1 : yy;
                if (o.yy != nyy || o.mo != nmo || o.dd != 1 || o.hh || o.mi || o.ss || o.wd != 1) days_ok = 0;
            }
        CHECK(days_ok);

        /* The pending machine (implementation review, 7). */
        uint8_t now12[12] = CLK(26, 10, 20, 2, 21, 0, 0, 0);
        lockchan_frame_t fc = frame(4, now12, 12);
        fc.anchor_ms = 1000;
        fc.commit_ms = 4000;
        lockchan_clock_state_t st = LOCKCHAN_CLOCK_STATE_INIT;
        /* Applied at once: the time advanced from the anchor, not the commit. */
        fk_owned = false; fk_status = 1; fk_sets = 0;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        CHECK(!st.pending && st.applied == 1 && st.status == 1 && st.off == -42);
        CHECK(fk_at.ss == 3 && fk_at.ms == 0);
        /* Another owner: refused, the setter never asked. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        fk_owned = true; fk_sets = 0;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        CHECK(!st.pending && st.refused == 1 && fk_sets == 0 && st.status == 0xFF);
        /* A RETRY: kept; each poll recomputes from the original anchor and
         * rechecks the owner. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        fk_owned = false; fk_status = LOCKCHAN_SET_RETRY; fk_sets = 0;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        CHECK(st.pending && st.retried == 1 && st.applied == 0);
        lockchan_clock_poll(&st, 4500, &fk_hooks);
        CHECK(st.pending && fk_sets == 2 && fk_at.ss == 3 && fk_at.ms == 500);
        fk_owned = true;                 /* USB takes the clock meanwhile */
        lockchan_clock_poll(&st, 4600, &fk_hooks);
        CHECK(!st.pending && st.refused == 1 && fk_sets == 2);
        /* A retry that lands, late in its window: the elapsed time is all of it. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        fk_owned = false; fk_status = LOCKCHAN_SET_RETRY;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        fk_status = 0;
        lockchan_clock_poll(&st, 4000 + LOCKCHAN_CLOCK_RETRY_MS, &fk_hooks);
        CHECK(!st.pending && st.applied == 1 && fk_at.ss == 5 && fk_at.ms == 0);
        /* The deadline is fixed from the commit: one ms past it, dropped
         * without asking. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        fk_status = LOCKCHAN_SET_RETRY;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        fk_sets = 0;
        lockchan_clock_poll(&st, 4000 + LOCKCHAN_CLOCK_RETRY_MS + 1, &fk_hooks);
        CHECK(!st.pending && st.dropped == 1 && fk_sets == 0);
        lockchan_clock_poll(&st, 99999, &fk_hooks);   /* idle: nothing */
        CHECK(st.dropped == 1 && fk_sets == 0);
        /* A drop (a link change, a wake, a raw-HID set) ends a pending one. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        lockchan_clock_drop(&st);
        CHECK(!st.pending && st.dropped == 1);
        lockchan_clock_drop(&st);                      /* none pending: not counted */
        CHECK(st.dropped == 1);
        /* A newer frame replaces a pending one, counted dropped. */
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        lockchan_frame_t fc2 = fc;
        fc2.anchor_ms = 5000; fc2.commit_ms = 6000;
        lockchan_clock_commit(&st, &fc2, 6000, &fk_hooks);
        CHECK(st.pending && st.dropped == 2 && st.anchor_ms == 5000);
        /* The timer's wrap between anchor and set. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        fk_status = 0;
        fc.anchor_ms = 0xFFFFFF00u; fc.commit_ms = 0x00000064u;
        lockchan_clock_commit(&st, &fc, 0x00000064u, &fk_hooks);
        CHECK(st.applied == 1 && fk_at.ss == 0 && fk_at.ms == 356);
        /* An invalid frame: rejected, the setter never asked. */
        st = (lockchan_clock_state_t)LOCKCHAN_CLOCK_STATE_INIT;
        fk_sets = 0;
        lockchan_frame_t fbad = frame(4, ok, 11);
        lockchan_clock_commit(&st, &fbad, 1, &fk_hooks);
        CHECK(st.rejected == 1 && fk_sets == 0 && !st.pending);
        /* The setter's REJECT is counted rejected. */
        fk_status = LOCKCHAN_SET_REJECT;
        fc.anchor_ms = 1000; fc.commit_ms = 4000;
        lockchan_clock_commit(&st, &fc, 4000, &fk_hooks);
        CHECK(st.rejected == 2 && st.applied == 0 && !st.pending);
#undef CLK
    }

    /* The owner stamp's age (implementation review, 1): a wake's credit made
     * after the stamp is taken back out; wraps. */
    CHECK(rtc_stamp_age_s(100, 0, 160, 0) == 60);
    CHECK(rtc_stamp_age_s(100, 0, 102, 1000) == 0);             /* USB set mid-recovery, then +1000 credit */
    CHECK(rtc_stamp_age_s(100, 5, 700, 1005) == 0);             /* still inside: 600 - 1000 < 0 */
    CHECK(rtc_stamp_age_s(100, 5, 1702, 1005) == 602);
    CHECK(rtc_stamp_age_s(0xFFFFFFF0u, 0xFFFFFFFFu, 20, 9) == 26);   /* both wrap */
    CHECK(rtc_stamp_age_s(100, 0, 200, (uint32_t)-30) == 130);  /* a negative credit adds */

    printf(fails ? "%d FAILED\n" : "lockchan_media: all passed\n", fails);
    return fails != 0;
}
