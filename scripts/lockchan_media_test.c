/* Host test of the lock-LED handler's validator (lockchan_media.c), every row
 * of plans/LOCK-LED-CHANNEL-PLAN.md's R2-9 table, and the playback bytes
 * (10-20, "Play times") with lockchan_media_position. Build and run:
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

    /* Playback: icon bit 7 and 5 trailing bytes [state][pos][dur], big endian. */
    {
        uint8_t p[] = {0x81, 3, 'A', 'r', 't', 'T', 'i', 't', 'l', 'e', 1, 0x01, 0x2C, 0x0E, 0x10};
        CHECK(lockchan_media_parse(&(lockchan_frame_t){0}, &m) == false);
        lockchan_frame_t g = frame(1, p, sizeof p);
        CHECK(lockchan_media_parse(&g, &m));
        CHECK(m.icon == 1 && m.playback && m.state == 1 && m.pos == 300 && m.dur == 3600);
        CHECK(m.n0 == 3 && m.n1 == 5 && memcmp(m.l1, "Title", 5) == 0);
        /* Paused is a valid state: the board then shows the clock. */
        p[10] = 0;
        g = frame(1, p, sizeof p);
        CHECK(lockchan_media_parse(&g, &m) && m.playback && m.state == 0);
        /* Any other state rejects. */
        p[10] = 2;
        CHECK(rejects(frame(1, p, sizeof p)));
        /* The tail is not text: any bytes there are fine. */
        uint8_t q[] = {0x80, 0, 1, 0xFF, 0x00, 0xFF, 0x00};
        g = frame(1, q, sizeof q);
        CHECK(lockchan_media_parse(&g, &m) && m.n0 == 0 && m.n1 == 0 && m.pos == 0xFF00 && m.dur == 0xFF00);
        /* Bit 7 without room for the tail. */
        CHECK(rejects(frame(1, q, 6)));
        CHECK(rejects(frame(1, q, 2)));
        /* Bit 7 with another icon bit. */
        q[0] = 0x84;
        CHECK(rejects(frame(1, q, sizeof q)));
        /* Without bit 7, no playback, as before. */
        g = now_playing(2, "a", "b");
        CHECK(lockchan_media_parse(&g, &m) && !m.playback && m.pos == 0);
    }
    /* The limits with playback: 19 + 21 + 5 = 47 bytes, and 48 rejects. */
    {
        uint8_t p[48];
        memset(p, 'a', sizeof p);
        p[0] = 0x83; p[1] = 19;
        p[42] = 1; p[43] = 0; p[44] = 9; p[45] = 0; p[46] = 99;
        lockchan_frame_t g = frame(1, p, 47);
        CHECK(lockchan_media_parse(&g, &m) && m.n0 == 19 && m.n1 == 21 && m.pos == 9 && m.dur == 99);
        p[47] = 'a';
        CHECK(rejects(frame(1, p, 48)));
        /* A line too long once the tail is counted: line 1 at 22. */
        p[1] = 18;
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

    /* Unknown types, 0 and 3-15. */
    for (uint8_t t = 0; t < 16; t++) {
        if (t == 1 || t == 2) continue;
        if (!rejects(frame(t, NULL, 0))) { printf("FAIL: type %u accepted\n", t); fails++; }
    }

    printf(fails ? "%d FAILED\n" : "lockchan_media: all passed\n", fails);
    return fails != 0;
}
