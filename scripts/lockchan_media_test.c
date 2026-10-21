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

static int fails;
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

    printf(fails ? "%d FAILED\n" : "lockchan_media: all passed\n", fails);
    return fails != 0;
}
