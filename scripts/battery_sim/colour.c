/* The colour path through QMK's real conversion: quantum/color.c and its CIE
 * table, linked in this build (SIM_REAL_COLOUR) in place of sim.c's stub, and
 * the board's own effects (rgb_matrix_kb.inc) compiled here. #included by
 * sim.c; run.sh builds it as sim_colour.
 *
 * What it checks (plans/POWER-MODES-REVISION-PLAN.md, firmware step 4 and
 * "Host side"), for the dim's 64 and Power saver's 128, each set through
 * power.c's own ladder:
 * - k_hook: every colour at every user value converts as the same colour at
 *   min(value, ceiling), so a value at or under the ceiling is never changed
 *   and nothing drives above the ceiling's own drive;
 * - k_drift: DRIFT under a ceiling is DRIFT at min(value, ceiling) with no
 *   ceiling, frame for frame (option c: the palette is built uncapped, the
 *   value knob capped);
 * - k_rainfall: RAINFALL, which converts each drop's own value, never drives
 *   above the ceiling's drive. */

/* What rgb_matrix_kb.inc takes from QMK's rgb_matrix, for its two effects on
 * a host. lib8tion's formulas as the board builds them (SCALE8_C, with
 * FASTLED_SCALE8_FIXED unset; lib8tion's own headers carry AVR asm a host
 * compiler refuses). One chunk a frame: the whole board at iter 0. */
typedef uint8_t fract8;
static inline uint8_t scale8(uint8_t i, fract8 s) { return (uint8_t)(((uint16_t)i * (uint16_t)s) >> 8); }
static inline uint8_t qsub8(uint8_t i, uint8_t j) { int16_t t = (int16_t)(i - j); return (uint8_t)(t < 0 ? 0 : t); }
static uint16_t rand16seed = 1337;
static inline uint8_t random8(void) {
    rand16seed = (uint16_t)(rand16seed * 2053u + 13849u);
    return (uint8_t)((uint8_t)(rand16seed & 0xFF) + (uint8_t)(rand16seed >> 8));
}
static inline uint8_t random8_max(uint8_t lim) { return (uint8_t)((random8() * lim) >> 8); }
typedef struct { uint8_t iter; bool init; } effect_params_t;
static struct { hsv_t hsv; uint8_t speed; } rgb_matrix_config;
static struct { struct { uint8_t x, y; } point[RGB_MATRIX_LED_COUNT]; } g_led_config;
static rgb_t k_frame[RGB_MATRIX_LED_COUNT];
static void rgb_matrix_set_color(int i, uint8_t r, uint8_t g, uint8_t b) { k_frame[i] = (rgb_t){r, g, b}; }
static bool rgb_matrix_check_finished_leds(uint8_t led_max) { return led_max < RGB_MATRIX_LED_COUNT; }
#define RGB_MATRIX_EFFECT(name)
#define RGB_MATRIX_CUSTOM_EFFECT_IMPLS
#define RGB_MATRIX_USE_LIMITS(min, max) uint8_t min = 0, max = RGB_MATRIX_LED_COUNT
#define RGB_MATRIX_TEST_LED_FLAGS()
#include "rgb_matrix_kb.inc"

/* The board's LED field is 217 x 59 (DRIFT's comment); a grid across it is
 * enough to reach every part of the ramp. */
static void k_points(void) {
    for (unsigned i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
        g_led_config.point[i].x = (uint8_t)((i % 14u) * 16u + (i / 14u) * 3u);
        g_led_config.point[i].y = (uint8_t)((i / 14u) * 11u);
    }
}

/* The ceilings, through power.c: the dim (Normal at 10 min), Saver while
 * typing, and none (USB). Booted once; the mode is persisted, so each sets
 * its own. */
static bool k_booted;
static void k_on_battery(void) {
    if (!k_booted) { power_init(); k_booted = true; }
    on_battery_mid();
}
static void k_ceiling_dim(void) {
    k_on_battery();
    while (power_mode() != POWER_MODE_NORMAL) power_cycle_mode();
    idle_ms = 600000; ltick();
    CHECK(power_val_cap() == 64, "the dim's ceiling: %u", power_val_cap());
}
static void k_ceiling_saver(void) {
    k_on_battery();
    while (power_mode() != POWER_MODE_SAVER) power_cycle_mode();
    idle_ms = 0; ltick();
    CHECK(power_val_cap() == 128, "Saver's ceiling: %u", power_val_cap());
}
static void k_ceiling_none(void) {
    vdd_mv = 4470; chrg_low = false; c5 = 100;
    lrun_s(30);
    CHECK(power_val_cap() == 255, "on USB, no ceiling: %u", power_val_cap());
}

static uint8_t k_max3(rgb_t c) { return c.r > c.g ? (c.r > c.b ? c.r : c.b) : (c.g > c.b ? c.g : c.b); }

/* Every hue, saturation and value through the hook, against QMK's own
 * conversion at min(value, ceiling). */
static unsigned k_hook_at(uint8_t cap) {
    uint8_t  top = hsv_to_rgb((hsv_t){0, 0, cap}).r;   /* white at the ceiling: its CIE drive */
    unsigned bad = 0;
    for (unsigned h = 0; h < 256; h++)
        for (unsigned s = 0; s < 256; s++)
            for (unsigned v = 0; v < 256; v++) {
                hsv_t c = {(uint8_t)h, (uint8_t)s, (uint8_t)v};
                rgb_t got = rgb_matrix_hsv_to_rgb(c);
                hsv_t w = c;
                if (w.v > cap) w.v = cap;
                rgb_t want = hsv_to_rgb(w);
                if (got.r != want.r || got.g != want.g || got.b != want.b || k_max3(got) > top) {
                    if (bad++ < 3)
                        CHECK(false, "cap %u: hsv %u,%u,%u gives %u,%u,%u, want %u,%u,%u (top %u)", cap, h, s, v,
                              got.r, got.g, got.b, want.r, want.g, want.b, top);
                }
            }
    return bad;
}

static void k_hook(void) {
    k_ceiling_dim();
    CHECK(k_hook_at(64) == 0, "the dim's ceiling, every colour");
    k_ceiling_saver();
    CHECK(k_hook_at(128) == 0, "Saver's ceiling, every colour");
    CHECK(hsv_to_rgb((hsv_t){0, 0, 128}).r == 48 && hsv_to_rgb((hsv_t){0, 0, 64}).r == 12,
          "QMK's CIE table: 128 drives 48, 64 drives 12");
    k_ceiling_none();
    CHECK(k_hook_at(255) == 0, "no ceiling: the hook is QMK's conversion");
}

/* DRIFT's frames at one value: the init frame and K_FRAMES - 1 after it, at a
 * mid speed so the field moves between them. */
#define K_FRAMES 6
static void k_drift_frames(hsv_t c, rgb_t out[K_FRAMES][RGB_MATRIX_LED_COUNT]) {
    rgb_matrix_config.hsv   = c;
    rgb_matrix_config.speed = 200;
    for (unsigned f = 0; f < K_FRAMES; f++) {
        effect_params_t p = {.iter = 0, .init = f == 0};
        DRIFT(&p);
        memcpy(out[f], k_frame, sizeof k_frame);
    }
}

static rgb_t k_capped[256][K_FRAMES][RGB_MATRIX_LED_COUNT];
static rgb_t k_free[256][K_FRAMES][RGB_MATRIX_LED_COUNT];

static void k_drift(void) {
    static const uint8_t hs[][2] = {{0, 255}, {170, 255}, {85, 128}, {213, 64}, {40, 0}};
    k_points();
    for (unsigned pass = 0; pass < 2; pass++) {
        uint8_t cap = pass == 0 ? 64 : 128;
        for (unsigned k = 0; k < sizeof hs / sizeof hs[0]; k++) {
            /* Each ceiling's frames for every value, then the same with no
             * ceiling (USB), from a fresh boot each time. */
            if (cap == 64) k_ceiling_dim(); else k_ceiling_saver();
            for (unsigned v = 0; v < 256; v++) k_drift_frames((hsv_t){hs[k][0], hs[k][1], (uint8_t)v}, k_capped[v]);
            k_ceiling_none();
            for (unsigned v = 0; v < 256; v++) k_drift_frames((hsv_t){hs[k][0], hs[k][1], (uint8_t)v}, k_free[v]);
            unsigned bad = 0, lit = 0;
            for (unsigned v = 0; v < 256; v++) {
                unsigned ve = v < cap ? v : cap;
                for (unsigned f = 0; f < K_FRAMES; f++)
                    for (unsigned i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
                        rgb_t a = k_capped[v][f][i], b = k_free[ve][f][i];
                        if (k_max3(a)) lit++;
                        if (a.r != b.r || a.g != b.g || a.b != b.b) {
                            if (bad++ < 3)
                                CHECK(false, "cap %u, hue %u sat %u, value %u, frame %u, LED %u: %u,%u,%u, want %u,%u,%u",
                                      cap, hs[k][0], hs[k][1], v, f, i, a.r, a.g, a.b, b.r, b.g, b.b);
                        }
                    }
            }
            CHECK(bad == 0, "cap %u, hue %u sat %u: %u LED frames differ", cap, hs[k][0], hs[k][1], bad);
            CHECK(lit > 0, "cap %u, hue %u sat %u: nothing lit, so nothing compared", cap, hs[k][0], hs[k][1]);
        }
        printf("  DRIFT under %u matches DRIFT at min(value, %u), %u values x %u frames x %u LEDs x %u colours\n",
               cap, cap, 256u, K_FRAMES, RGB_MATRIX_LED_COUNT, (unsigned)(sizeof hs / sizeof hs[0]));
    }
}

/* RAINFALL's drops, a few hundred frames at each value, never above the
 * ceiling's drive. */
static void k_rainfall_at(uint8_t cap) {
    static const uint8_t vs[] = {255, 200, 150, 129, 128, 100, 65, 64, 30};
    uint8_t  top = hsv_to_rgb((hsv_t){0, 0, cap}).r;
    unsigned bad = 0, lit = 0;
    for (unsigned k = 0; k < sizeof vs; k++) {
        rgb_matrix_config.hsv   = (hsv_t){0, 255, vs[k]};
        rgb_matrix_config.speed = 255;   /* a drop every frame */
        for (unsigned f = 0; f < 300; f++) {
            effect_params_t p = {.iter = 0, .init = f == 0};
            RAINFALL(&p);
            for (unsigned i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
                uint8_t m = k_max3(k_frame[i]);
                if (m) lit++;
                if (m > top && bad++ < 3) CHECK(false, "cap %u, value %u: LED %u drives %u, top %u", cap, vs[k], i, m, top);
            }
        }
    }
    CHECK(bad == 0 && lit > 0, "RAINFALL under %u: %u over the ceiling, %u lit", cap, bad, lit);
}

static void k_rainfall(void) {
    k_ceiling_dim();
    k_rainfall_at(64);
    k_ceiling_saver();
    k_rainfall_at(128);
}
