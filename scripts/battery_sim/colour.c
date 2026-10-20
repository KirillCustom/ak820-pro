/* The colour path through QMK's real conversion: quantum/color.c and its CIE
 * table, linked in this build (SIM_REAL_COLOUR) in place of sim.c's stub, with
 * QMK's own colour types (stubs/quantum.h includes quantum/color.h here), and
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
 *   ceiling, frame for frame (option c: the palette built uncapped, the value
 *   knob capped): every value from an init, and one stream with the palette's
 *   cache warm while hue, saturation, value and the ceiling change;
 * - k_drift_usb: DRIFT with no ceiling (USB) renders exactly as before the
 *   revision. A stream across speeds and warm-cache changes, hashed against
 *   DRIFT_USB_FNV, which run.sh re-derives from 8b06e4dcc4's own DRIFT;
 * - k_rainfall: RAINFALL, which converts each drop's own value, never drives
 *   above the ceiling's drive (at nine values, not every one).
 *
 * The frames run as rgb_matrix.c runs them: the board's 81 LEDs in QMK's
 * default 5 chunks, init on every chunk of a first frame. The LED positions
 * are a synthetic grid across the board's 217 x 59, not its map: enough to
 * reach every cell of DRIFT's ramp, not a claim about the board's frames. */

/* What rgb_matrix_kb.inc takes from QMK's rgb_matrix, for its two effects on
 * a host. lib8tion's formulas as the board builds them (SCALE8_C, and
 * FASTLED_SCALE8_FIXED=1 from builddefs/common_features.mk's LIB8TION_ENABLE;
 * lib8tion's own headers carry AVR asm a host compiler refuses). */
typedef uint8_t fract8;
static inline uint8_t scale8(uint8_t i, fract8 s) { return (uint8_t)(((uint16_t)i * (1u + (uint16_t)s)) >> 8); }
static inline uint8_t qsub8(uint8_t i, uint8_t j) { int16_t t = (int16_t)(i - j); return (uint8_t)(t < 0 ? 0 : t); }
static uint16_t rand16seed = 1337;
static inline uint8_t random8(void) {
    rand16seed = (uint16_t)(rand16seed * 2053u + 13849u);
    return (uint8_t)((uint8_t)(rand16seed & 0xFF) + (uint8_t)(rand16seed >> 8));
}
static inline uint8_t random8_max(uint8_t lim) { return (uint8_t)((random8() * lim) >> 8); }
typedef struct { uint8_t iter; bool init; } effect_params_t;
static struct { hsv_t hsv; uint8_t speed; } rgb_matrix_config;

#undef RGB_MATRIX_LED_COUNT
#define RGB_MATRIX_LED_COUNT 81                          /* the board's (keyboard.json) */
#define K_LIMIT ((RGB_MATRIX_LED_COUNT + 4) / 5)          /* QMK's default RGB_MATRIX_LED_PROCESS_LIMIT */
static struct { struct { uint8_t x, y; } point[RGB_MATRIX_LED_COUNT]; } g_led_config;
static rgb_t k_frame[RGB_MATRIX_LED_COUNT];
static void rgb_matrix_set_color(int i, uint8_t r, uint8_t g, uint8_t b) { k_frame[i] = (rgb_t){r, g, b}; }
static bool rgb_matrix_check_finished_leds(uint8_t led_max) { return led_max < RGB_MATRIX_LED_COUNT; }
#define RGB_MATRIX_EFFECT(name)
#define RGB_MATRIX_CUSTOM_EFFECT_IMPLS
#define RGB_MATRIX_USE_LIMITS(min, max)                \
    uint8_t min = (uint8_t)(K_LIMIT * params->iter), \
            max = (uint8_t)((min) + K_LIMIT < RGB_MATRIX_LED_COUNT ? (min) + K_LIMIT : RGB_MATRIX_LED_COUNT)
#define RGB_MATRIX_TEST_LED_FLAGS()
#include "rgb_matrix_kb.inc"

/* One frame as rgb_matrix.c renders it: chunk after chunk until the effect
 * says the board is done, `init` on every chunk of a first frame. */
static void k_run(bool (*fx)(effect_params_t *), bool init) {
    effect_params_t p = {.iter = 0, .init = init};
    while (fx(&p)) p.iter++;
}

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
static void k_ceiling(uint8_t cap) {
    if (cap == 64) k_ceiling_dim();
    else if (cap == 128) k_ceiling_saver();
    else k_ceiling_none();
}

static uint8_t k_max3(rgb_t c) { return c.r > c.g ? (c.r > c.b ? c.r : c.b) : (c.g > c.b ? c.g : c.b); }
static bool    k_same(rgb_t a, rgb_t b) { return a.r == b.r && a.g == b.g && a.b == b.b; }

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
                if (!k_same(got, want) || k_max3(got) > top) {
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
        k_run(DRIFT, f == 0);
        memcpy(out[f], k_frame, sizeof k_frame);
    }
}

static rgb_t k_capped[256][K_FRAMES][RGB_MATRIX_LED_COUNT];
static rgb_t k_free[256][K_FRAMES][RGB_MATRIX_LED_COUNT];

/* One stream from a single init, the palette's cache warm throughout: each
 * step K_STEP_FRAMES frames at its hue, saturation and value, under its
 * ceiling. */
typedef struct { uint8_t h, s, v, cap; } k_step_t;
static const k_step_t k_stream[] = {
    {191, 255, 200, 128}, {191, 255, 200, 64}, {191, 255, 200, 128},   /* the ceiling moves */
    {30, 255, 200, 128},  {30, 120, 200, 128},                         /* hue, then saturation */
    {30, 120, 90, 128},   {30, 120, 90, 64},   {30, 120, 255, 64},     /* under and over each */
    {0, 0, 255, 128},     {0, 0, 128, 64},     {213, 64, 160, 128},
};
#define K_STEPS       (sizeof k_stream / sizeof k_stream[0])
#define K_STEP_FRAMES 4
static rgb_t k_stream_a[K_STEPS][K_STEP_FRAMES][RGB_MATRIX_LED_COUNT];
static rgb_t k_stream_b[K_STEPS][K_STEP_FRAMES][RGB_MATRIX_LED_COUNT];

/* The stream under its ceilings (capped), or with none and the value at
 * min(value, ceiling) (free). The ceilings move between frames, as the
 * ladder moves them; DRIFT is never re-initialized. */
static void k_stream_run(bool capped, rgb_t out[K_STEPS][K_STEP_FRAMES][RGB_MATRIX_LED_COUNT]) {
    if (!capped) k_ceiling_none();
    rgb_matrix_config.speed = 200;
    for (unsigned k = 0; k < K_STEPS; k++) {
        k_step_t st = k_stream[k];
        if (capped) k_ceiling(st.cap);
        rgb_matrix_config.hsv = (hsv_t){st.h, st.s, capped ? st.v : (st.v < st.cap ? st.v : st.cap)};
        for (unsigned f = 0; f < K_STEP_FRAMES; f++) {
            k_run(DRIFT, k == 0 && f == 0);
            memcpy(out[k][f], k_frame, sizeof k_frame);
        }
    }
}

static void k_drift(void) {
    static const uint8_t hs[][2] = {{0, 255}, {170, 255}, {85, 128}, {213, 64}, {40, 0}};
    k_points();
    for (unsigned pass = 0; pass < 2; pass++) {
        uint8_t cap = pass == 0 ? 64 : 128;
        for (unsigned k = 0; k < sizeof hs / sizeof hs[0]; k++) {
            /* Each ceiling's frames for every value, then the same with no
             * ceiling (USB). */
            k_ceiling(cap);
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
                        if (!k_same(a, b) && bad++ < 3)
                            CHECK(false, "cap %u, hue %u sat %u, value %u, frame %u, LED %u: %u,%u,%u, want %u,%u,%u",
                                  cap, hs[k][0], hs[k][1], v, f, i, a.r, a.g, a.b, b.r, b.g, b.b);
                    }
            }
            CHECK(bad == 0, "cap %u, hue %u sat %u: %u LED frames differ", cap, hs[k][0], hs[k][1], bad);
            CHECK(lit > 0, "cap %u, hue %u sat %u: nothing lit, so nothing compared", cap, hs[k][0], hs[k][1]);
        }
        printf("  DRIFT under %u matches DRIFT at min(value, %u), %u values x %u frames x %u LEDs x %u colours\n",
               cap, cap, 256u, K_FRAMES, RGB_MATRIX_LED_COUNT, (unsigned)(sizeof hs / sizeof hs[0]));
    }
    k_stream_run(true, k_stream_a);
    k_stream_run(false, k_stream_b);
    unsigned bad = 0, lit = 0;
    for (unsigned k = 0; k < K_STEPS; k++)
        for (unsigned f = 0; f < K_STEP_FRAMES; f++)
            for (unsigned i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
                rgb_t a = k_stream_a[k][f][i], b = k_stream_b[k][f][i];
                if (k_max3(a)) lit++;
                if (!k_same(a, b) && bad++ < 3)
                    CHECK(false, "the warm stream, step %u frame %u LED %u: %u,%u,%u, want %u,%u,%u", k, f, i, a.r,
                          a.g, a.b, b.r, b.g, b.b);
            }
    CHECK(bad == 0 && lit > 0, "the warm stream: %u LED frames differ, %u lit", bad, lit);
    printf("  and with the cache warm: %u steps x %u frames, the ceiling, hue, saturation and value moving\n",
           (unsigned)K_STEPS, K_STEP_FRAMES);
}

/* DRIFT on USB, hashed: three speeds, each one stream from an init with the
 * cache warm across hue, saturation and value changes. DRIFT_USB_FNV is the
 * hash 8b06e4dcc4's DRIFT gives (run.sh builds that DRIFT and checks it), so
 * the revision leaves USB rendering exactly as it was. */
#define DRIFT_USB_FNV 0x41da838au
static void k_drift_usb(void) {
    static const uint8_t speeds[] = {0, 128, 255};
    static const hsv_t   steps[]  = {{191, 255, 255}, {191, 255, 120}, {30, 255, 120}, {30, 90, 120},
                                     {30, 90, 255},   {0, 0, 200},     {213, 64, 40},  {170, 255, 0}};
    k_points();
    k_ceiling_none();
    uint32_t h = 2166136261u;
    unsigned lit = 0;
    for (unsigned sp = 0; sp < sizeof speeds; sp++) {
        rgb_matrix_config.speed = speeds[sp];
        for (unsigned k = 0; k < sizeof steps / sizeof steps[0]; k++) {
            rgb_matrix_config.hsv = steps[k];
            for (unsigned f = 0; f < 6; f++) {
                k_run(DRIFT, k == 0 && f == 0);
                for (unsigned i = 0; i < RGB_MATRIX_LED_COUNT; i++) {
                    const uint8_t b[3] = {k_frame[i].r, k_frame[i].g, k_frame[i].b};
                    if (k_max3(k_frame[i])) lit++;
                    for (unsigned j = 0; j < 3; j++) h = (h ^ b[j]) * 16777619u;
                }
            }
        }
    }
    printf("  DRIFT on USB: FNV-1a %08x over %u lit LED frames\n", (unsigned)h, lit);
    CHECK(h == DRIFT_USB_FNV, "DRIFT on USB renders differently: %08x, want %08x", (unsigned)h, DRIFT_USB_FNV);
    CHECK(lit > 0, "DRIFT on USB: nothing lit");
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
            k_run(RAINFALL, f == 0);
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
