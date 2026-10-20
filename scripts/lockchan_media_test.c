/* Host test of the lock-LED handler's validator (lockchan_media.c), every row
 * of plans/LOCK-LED-CHANNEL-PLAN.md's R2-9 table. Build and run:
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

    /* Text: printable ASCII only, every position. */
    for (unsigned c = 0; c < 256; c++) {
        char s[2] = {(char)c, 0};
        uint8_t p[] = {1, 1, (uint8_t)c, 'x'};
        lockchan_frame_t g = frame(1, p, 4);
        bool ok = c >= 0x20 && c <= 0x7E;
        lockchan_media_t mm;
        if (lockchan_media_parse(&g, &mm) != ok) { printf("FAIL: byte 0x%02x in line 0\n", c); fails++; }
        p[2] = 'x'; p[3] = (uint8_t)c;
        g = frame(1, p, 4);
        if (lockchan_media_parse(&g, &mm) != ok) { printf("FAIL: byte 0x%02x in line 1\n", c); fails++; }
        (void)s;
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
