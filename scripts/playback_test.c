/* The playback readout's model (graphics/playback.c) on the host: the host's
 * anchor, a media key's guess and its deadline, the expiry.
 *
 *   gcc -std=c11 -Wall -Wextra -I qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro \
 *       scripts/playback_test.c qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/graphics/playback.c \
 *       -o /tmp/playback_test && /tmp/playback_test
 */
#include <stdio.h>
#include <stdlib.h>
#include "graphics/playback.h"

static int failures;
#define CHECK(c)                                                          \
    do {                                                                  \
        if (!(c)) {                                                       \
            fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #c); \
            failures++;                                                   \
        }                                                                 \
    } while (0)

#define S(x) ((uint32_t)(x) * 1000u)

static playback_t at(uint16_t pos, uint16_t dur, uint32_t now) {
    playback_t p = {0};
    CHECK(playback_set(&p, 1, pos, dur, 135000u, now));
    return p;
}

/* Codex's scenario (the play-times verify, P2 2): two ignored presses must
 * not leave the readout behind once the guess's deadline passes. */
static void two_ignored_presses_give_the_anchor_back(void) {
    playback_t p = at(100, 0, 0);
    playback_key(&p, S(1));
    CHECK(playback_shown(&p, S(1)) == 101);
    CHECK(playback_shown(&p, S(9)) == 101);   /* frozen */
    CHECK(!playback_ticking(&p));
    playback_key(&p, S(11));                  /* resumed from 101 */
    CHECK(playback_ticking(&p));
    CHECK(!playback_service(&p, S(20)));
    CHECK(playback_shown(&p, S(20)) == 110);  /* the guess, still in its 15 s */
    CHECK(!playback_service(&p, S(26) + 1));
    CHECK(playback_shown(&p, S(26) + 1) == 126);   /* the anchor: 100 + 26 */
    CHECK(p.base == 100 && p.base_ms == 0);   /* never moved */
}

/* A press after the deadline passed unserviced (Fn+D, an animation) acts on
 * the anchor, not on the stale guess. */
static void a_press_after_an_unserviced_deadline_acts_on_the_anchor(void) {
    playback_t p = at(100, 0, 0);
    playback_key(&p, 0);                      /* frozen at 100 */
    playback_key(&p, S(20));                  /* 20 s later, no service between */
    CHECK(p.guess == PLAYBACK_GUESS_FROZEN);  /* a new freeze, not a resume */
    CHECK(playback_shown(&p, S(25)) == 120);
}

/* One ignored press: frozen for the deadline, then the anchor. */
static void one_ignored_press_ends_at_its_deadline(void) {
    playback_t p = at(50, 0, S(3));
    playback_key(&p, S(5));
    CHECK(!playback_service(&p, S(20)));      /* 15 s exactly: still held */
    CHECK(playback_shown(&p, S(20)) == 52);
    CHECK(!playback_service(&p, S(20) + 1));
    CHECK(playback_shown(&p, S(20) + 1) == 67);
    CHECK(playback_ticking(&p));
}

/* Any host word ends the guess and sets a new anchor. */
static void a_host_word_ends_the_guess(void) {
    playback_t p = at(10, 0, 0);
    playback_key(&p, S(2));
    CHECK(!playback_set(&p, 1, 40, 300, 20000u, S(4)));   /* same owner */
    CHECK(p.guess == PLAYBACK_GUESS_NONE);
    CHECK(playback_shown(&p, S(9)) == 45);
    CHECK(playback_set(&p, 0, 0, 0, 20000u, S(10)));      /* hands the band back */
    CHECK(!p.active);
    playback_key(&p, S(11));                              /* off: nothing to guess */
    CHECK(p.guess == PLAYBACK_GUESS_NONE);
}

/* The expiry is the last setter's, and the guess goes with it. */
static void the_expiry_is_the_last_setters(void) {
    playback_t p = at(0, 0, 0);                           /* 135 s */
    playback_key(&p, S(100));
    CHECK(!playback_service(&p, S(135)));
    CHECK(playback_service(&p, S(135) + 1));
    CHECK(!p.active && p.guess == PLAYBACK_GUESS_NONE);
    CHECK(!playback_service(&p, S(136)));                 /* once */
    playback_set(&p, 1, 0, 0, 20000u, S(200));            /* raw HID's 20 s */
    CHECK(!playback_service(&p, S(220)));
    CHECK(playback_service(&p, S(220) + 1));
}

/* Never past a known duration, a resumed guess included; never past 0xFFFF. */
static void clamps(void) {
    playback_t p = at(295, 300, 0);
    CHECK(playback_shown(&p, S(4)) == 299);
    CHECK(playback_shown(&p, S(60)) == 300);
    playback_key(&p, S(2));
    playback_key(&p, S(3));
    CHECK(playback_shown(&p, S(14)) == 300);
    playback_t q = at(0xFFF0, 0, 0);
    CHECK(playback_shown(&q, S(100)) == 0xFFFF);
}

/* The timer's rollover: elapsed time by unsigned subtraction. */
static void the_timer_wraps(void) {
    uint32_t t0 = 0xFFFFFFFFu - 2500u;
    playback_t p = at(10, 0, t0);
    CHECK(playback_shown(&p, t0 + S(5)) == 15);
    playback_key(&p, t0 + S(6));
    CHECK(!playback_service(&p, t0 + S(20)));
    CHECK(playback_shown(&p, t0 + S(20)) == 16);
    CHECK(!playback_service(&p, t0 + S(22)));
    CHECK(playback_shown(&p, t0 + S(22)) == 32);
}

/* A deep sleep's wake: off, and says whether it was on. */
static void off(void) {
    playback_t p = at(5, 0, 0);
    playback_key(&p, 1);
    CHECK(playback_off(&p));
    CHECK(!p.active && p.guess == PLAYBACK_GUESS_NONE);
    CHECK(!playback_off(&p));
}

int main(void) {
    two_ignored_presses_give_the_anchor_back();
    a_press_after_an_unserviced_deadline_acts_on_the_anchor();
    one_ignored_press_ends_at_its_deadline();
    a_host_word_ends_the_guess();
    the_expiry_is_the_last_setters();
    clamps();
    the_timer_wraps();
    off();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    puts("playback_test: all passed");
    return 0;
}
