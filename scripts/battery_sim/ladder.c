/* Phase 2's power modes (power.c; plans/PHASE2-BUILD-PLAN.md, section 4),
 * #included by sim.c when power.h has the modes. One scenario per process, as
 * the rest: power.c's state is file-static too. */

rgb_t rgb_matrix_hsv_to_rgb(hsv_t hsv);   /* power.c's override of QMK's weak hook */

static void ltick(void) {
    tick();
    power_task();
}
static void lrun_s(uint32_t s) { for (uint32_t i = 0; i < s * 10u; i++) ltick(); }

/* On the pack at ~37% (5C 80, ~3.86 V): above the low-battery cap. */
static void on_battery_mid(void) {
    vdd_mv = 3900; c5 = 80;
    lrun_s(30);
}

static void expect_stage(power_stage_t st, uint8_t want_scale, uint8_t want_cap, uint8_t want_val, const char *what) {
    CHECK(power_stage() == st, "%s: stage %d, want %d", what, power_stage(), st);
    CHECK(scale == want_scale, "%s: driver scale %u, want %u", what, scale, want_scale);
    CHECK(bkl_cap == want_cap, "%s: backlight cap %u, want %u", what, bkl_cap, want_cap);
    CHECK(power_val_cap() == want_val, "%s: value cap %u, want %u", what, power_val_cap(), want_val);
}

/* Normal's stages at their times, and a keypress restoring all of it within
 * one 10 Hz tick. */
static void l_normal(void) {
    power_init();
    on_battery_mid();
    CHECK(power_mode() == POWER_MODE_NORMAL, "the default is Normal, got %d", power_mode());
    idle_ms = 59999;  ltick(); expect_stage(POWER_STAGE_ACTIVE, 255, 0xFF, 255, "59.999 s idle");
    idle_ms = 60000;  ltick(); expect_stage(POWER_STAGE_DIM, 255, 0xFF, 64, "1 min idle");
    idle_ms = 300000; ltick(); expect_stage(POWER_STAGE_LIGHTS_OFF, 0, 4, 255, "5 min idle");
    idle_ms = 900000; ltick(); expect_stage(POWER_STAGE_SCREEN_OFF, 0, 0, 255, "15 min idle");
    idle_ms = 0;      ltick(); expect_stage(POWER_STAGE_ACTIVE, 255, 0xFF, 255, "a keypress");
}

/* Power Saver's, and Full never; Fn+B's cycle order and its persistence. */
static void l_modes(void) {
    power_init();
    on_battery_mid();
    power_cycle_mode();
    CHECK(power_mode() == POWER_MODE_SAVER, "Normal -> Saver, got %d", power_mode());
    CHECK(!strcmp(last_status, "Power saver"), "overlay '%s'", last_status);
    uint8_t m = 0xFF;
    CHECK(kb_eeconfig_get_power_mode(&m) && m == POWER_MODE_SAVER, "persisted %u", m);
    idle_ms = 30000;  ltick(); expect_stage(POWER_STAGE_DIM, 255, 0xFF, 64, "Saver, 30 s");
    idle_ms = 120000; ltick(); expect_stage(POWER_STAGE_LIGHTS_OFF, 0, 4, 255, "Saver, 2 min");
    idle_ms = 300000; ltick(); expect_stage(POWER_STAGE_SCREEN_OFF, 0, 0, 255, "Saver, 5 min");
    power_cycle_mode();
    CHECK(power_mode() == POWER_MODE_MINIMAL, "Saver -> Minimal, got %d", power_mode());
    CHECK(!strcmp(last_status, "Minimal mode"), "overlay '%s'", last_status);
    power_cycle_mode();
    CHECK(power_mode() == POWER_MODE_FULL, "Minimal -> Full, got %d", power_mode());
    idle_ms = 86400000u; ltick(); expect_stage(POWER_STAGE_ACTIVE, 255, 0xFF, 255, "Full, a day idle");
    power_cycle_mode();
    CHECK(power_mode() == POWER_MODE_NORMAL, "Full -> Normal, got %d", power_mode());
}

/* On USB every mode is Full Power. */
static void l_usb(void) {
    power_init();
    become_full();
    idle_ms = 86400000u;
    lrun_s(2);
    expect_stage(POWER_STAGE_ACTIVE, 255, 0xFF, 255, "on USB, a day idle");
}

/* Below 15% the lighting follows Saver whatever the mode, and says so once;
 * Full Power included. */
static void l_low_cap(void) {
    power_init();
    vdd_mv = 3900; c5 = 62;   /* ~3.70 V: ~11% on the curve */
    lrun_s(30);
    CHECK(battery_level_permille() <= 150, "level %u pm, want <= 150", battery_level_permille());
    CHECK(!strcmp(last_status, "Saving power"), "overlay '%s'", last_status);
    last_status[0] = 0;
    idle_ms = 30000; ltick(); expect_stage(POWER_STAGE_DIM, 255, 0xFF, 64, "low, Normal, 30 s");
    CHECK(last_status[0] == 0, "said once, then '%s'", last_status);
    power_cycle_mode(); power_cycle_mode(); power_cycle_mode();   /* Normal -> Saver -> Minimal -> Full */
    CHECK(power_mode() == POWER_MODE_FULL, "mode %d", power_mode());
    idle_ms = 120000; ltick(); expect_stage(POWER_STAGE_LIGHTS_OFF, 0, 4, 255, "low, Full, 2 min");
}

/* The dim is a ceiling on the value: it never raises one, and it keeps hue. */
static void l_val_cap(void) {
    power_init();
    on_battery_mid();
    idle_ms = 60000; ltick();
    hsv_t hi = {191, 255, 200}, lo = {191, 255, 30};
    rgb_t a = rgb_matrix_hsv_to_rgb(hi), b = rgb_matrix_hsv_to_rgb(lo);
    CHECK(a.r == 64, "200 capped to 64, got %u", a.r);
    CHECK(b.r == 30, "30 left alone, got %u", b.r);
    idle_ms = 0; ltick();
    a = rgb_matrix_hsv_to_rgb(hi);
    CHECK(a.r == 200, "after the wake 200, got %u", a.r);
}

/* The low-battery lights cut holds the RGB dark through every stage. */
static void l_lights_cut(void) {
    power_init();
    on_battery_mid();
    power_set_lights_cut(true);
    CHECK(scale == 0, "cut while active: scale %u", scale);
    idle_ms = 60000; ltick();
    CHECK(scale == 0 && power_val_cap() == 64, "cut and dim: scale %u, cap %u", scale, power_val_cap());
    idle_ms = 0; ltick();
    CHECK(scale == 0, "cut after the wake: scale %u", scale);
    power_set_lights_cut(false);
    CHECK(scale == 255, "lifted: scale %u", scale);
}

/* A mode left unset (a flash) is Normal, and the overlay says so once, 3 s
 * after boot; a persisted one is taken silently. */
static void l_announce(void) {
    power_init();
    lrun_s(2);
    CHECK(last_status[0] == 0, "nothing before 3 s, got '%s'", last_status);
    lrun_s(2);
    CHECK(!strcmp(last_status, "Normal power"), "overlay '%s'", last_status);
    uint8_t m = 0xFF;
    CHECK(kb_eeconfig_get_power_mode(&m) && m == POWER_MODE_NORMAL, "persisted %u", m);
}

/* Minimal: dark on battery even while typing; the backlight lights for 4 s
 * after a message and while Fn+D's pages are open; USB is Full. */
static void l_minimal(void) {
    kb_eeconfig_set_power_mode(POWER_MODE_MINIMAL);
    power_init();
    on_battery_mid();
    idle_ms = 0; ltick();
    expect_stage(POWER_STAGE_SCREEN_OFF, 0, 0, 255, "Minimal, typing");
    display_set_param_status("Brightness 5");   /* a key's overlay */
    ltick();
    CHECK(bkl_cap == 0xFF && scale == 0, "lit for the message, RGB still off: cap %u scale %u", bkl_cap, scale);
    lrun_s(3);
    CHECK(bkl_cap == 0xFF, "still lit at 3 s: cap %u", bkl_cap);
    lrun_s(2);
    CHECK(bkl_cap == 0, "dark again after 4 s: cap %u", bkl_cap);
    debug_open = true; ltick();
    CHECK(bkl_cap == 0xFF, "lit while Fn+D's page is open: cap %u", bkl_cap);
    lrun_s(30);
    CHECK(bkl_cap == 0xFF, "for as long as it is open: cap %u", bkl_cap);
    debug_open = false; ltick();
    CHECK(bkl_cap == 0, "dark when it closes: cap %u", bkl_cap);
    become_full(); ltick();
    expect_stage(POWER_STAGE_ACTIVE, 255, 0xFF, 255, "Minimal on USB");
}

static void l_persisted(void) {
    kb_eeconfig_set_power_mode(POWER_MODE_SAVER);
    power_init();
    lrun_s(5);
    CHECK(power_mode() == POWER_MODE_SAVER, "mode %d", power_mode());
    CHECK(last_status[0] == 0, "no overlay for a persisted mode, got '%s'", last_status);
}
