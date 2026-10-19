/* Deep sleep's hand-over to the gauge and the log (battery.c's
 * battery_sleep_*; plans/DEEP-SLEEP-BUILD-PLAN.md, sections 4 and 6),
 * #included by sim.c after count.c. A sleep here is what sleep.c does: the
 * hand-over before the lock, QMK's timer standing still (now_ms does not move,
 * no report arrives: the board polls the module for them), the wake, a few
 * awake passes, then the accounting with the slept ms. The sim's awake load is
 * count.c's 303.8 mW; asleep it is BATTERY_P_SLEEP_UW. */

#define SLEEP_MW (BATTERY_P_SLEEP_UW / 1000.0)

/* A sleep of `ms`, accounted after `gap_ms` of awake passes (the recovery's
 * first steps: the waking input, then the PCF's read). */
static void sleep_for(uint32_t ms, uint32_t gap_ms) {
    battery_sleep_enter();
    battery_sleep_wake();
    for (uint32_t t = 0; t < gap_ms; t += 100u) tick();
    battery_sleep_account(ms);
}

/* A night: the energy into e_used and the level, never into the average. */
static void s_energy(void) {
    c_start(96);
    run_s(3600);
    uint16_t l0 = battery_level_permille();
    uint32_t e0 = battery_energy_used_mwh_x10();
    uint16_t m0 = battery_avg_minutes();
    uint32_t a0 = battery_power_avg_uw();
    sleep_for(16u * 3600000u, 300);
    double   want_e = SLEEP_MW * 16.0 * 10.0;   /* 0.1 mWh: 13.35 mW for 16 h */
    uint32_t de     = battery_energy_used_mwh_x10() - e0;
    CHECK(fabs(de - want_e) <= 2.0, "16 h asleep is %.0f x 0.1 mWh: %u", want_e, de);
    double drop = l0 - battery_level_permille();
    double want = SLEEP_MW * 16.0 * 1000.0 / 15579.0;   /* pm */
    CHECK(fabs(drop - want) <= 1.0, "the level down %.1f pm for the night: %.0f", want, drop);
    CHECK(battery_avg_minutes() == m0, "the average's minutes unchanged: %u -> %u", m0, battery_avg_minutes());
    CHECK(battery_power_avg_uw() == a0, "the average unchanged: %u -> %u", a0, battery_power_avg_uw());
}

/* Time left is hours of use: across a night it moves only by the level, and
 * at once, not at the next minute. */
static void s_time_left(void) {
    c_start(96);
    run_s(2 * 3600);
    uint16_t t0 = battery_time_left_min(), l0 = battery_level_permille();
    sleep_for(16u * 3600000u, 300);
    uint16_t t1 = battery_time_left_min(), l1 = battery_level_permille();
    /* One pm is ~3 min of time left here, and t0 is up to a minute old (the
     * level moved since it was computed): two pm's worth. */
    double want = (double)t0 * l1 / l0, tol = 2.0 * t0 / l0 + 1.0;
    CHECK(fabs(t1 - want) <= tol, "time left scales with the level only: %u -> %u (want %.0f +- %.0f)", t0, t1,
          want, tol);
    CHECK(t1 < t0, "and it moved at the wake: %u -> %u", t0, t1);
}

/* The floor and the cut hold across a sleep; an unknown level stays unknown. */
static void s_floor_cut(void) {
    c_start(40);   /* 3515 mV: ~28 pm */
    sleep_for(30u * 24u * 3600000u, 300);   /* a month asleep: far past the floor */
    CHECK(battery_level_permille() == 10, "the count stops at 1%%: %u", battery_level_permille());
    c5 = 3;
    run_s(300);
    CHECK(power_lights_cut() && battery_level_permille() == 0, "the cut: 0 (%u)", battery_level_permille());
    sleep_for(3600000u, 300);
    CHECK(battery_level_permille() == 0 && battery_count_state() == BATTERY_COUNT_CUT,
          "still 0 and CUT after a sleep: %u, state %u", battery_level_permille(), battery_count_state());
}

static void s_unknown(void) {
    vdd_mv = 3900; c5 = 0xFF;   /* on battery, the module silent: no estimate, no level */
    run_s(30);
    CHECK(battery_level_permille() == 0xFFFF, "unknown before: %u", battery_level_permille());
    uint32_t e0 = battery_energy_used_mwh_x10();
    sleep_for(3600000u, 300);
    CHECK(battery_level_permille() == 0xFFFF, "still unknown: %u", battery_level_permille());
    CHECK(battery_energy_used_mwh_x10() - e0 >= 133, "the energy still counted: %u",
          battery_energy_used_mwh_x10() - e0);
}

/* A count held below the band before the sleep: the hold is stale after it
 * (the estimate was forgotten), so the sleep's energy is taken. */
static void s_band_hold(void) {
    c_start(80);
    c5 = 88;
    run_s(120);
    CHECK(battery_count_state() == BATTERY_COUNT_HELD, "held before: %u", battery_count_state());
    uint16_t l0 = battery_level_permille();
    sleep_for(16u * 3600000u, 300);
    double drop = l0 - battery_level_permille();
    CHECK(drop >= SLEEP_MW * 16.0 * 1000.0 / 15579.0 - 1.0, "the night taken despite the old hold: %.0f pm", drop);
}

/* Many short sleeps and one long one take the same energy: the remainder
 * below a pm carries. */
static void s_remainder(void) {
    c_start(96);
    uint16_t l0 = battery_level_permille();
    for (int k = 0; k < 2000; k++) sleep_for(28800u, 0);   /* 2000 x 28.8 s = 16 h */
    double drop = l0 - battery_level_permille();
    double want = SLEEP_MW * 16.0 * 1000.0 / 15579.0;
    CHECK(fabs(drop - want) <= 1.0, "2000 short sleeps = one night: %.0f pm (want %.1f)", drop, want);
}

/* The relaxation after an unplug is physical: a sleep ages it. UNKNOWN-CHG at
 * the clamp, unplugged: the re-seat (950) comes RELAX_S after, then needs
 * fresh reports (model.c, m_reseat_unknown). */
static void s_relax(void) {
    vdd_mv = 4300; chrg_low = true; c5 = 100;
    run_s(120);
    vdd_mv = 3900; chrg_low = false;
    run_s(60);
    CHECK(battery_level_permille() != 950, "not re-seated a minute after the unplug: %u", battery_level_permille());
    sleep_for(40u * 60000u, 300);   /* longer than RELAX_S */
    run_s(40);
    CHECK(battery_level_permille() == 950, "re-seated 40 s after a 40 min sleep: %u", battery_level_permille());
}

/* --- The log ------------------------------------------------------------------ */

typedef struct { unsigned slept, e_x10, c5_n, level, led, bkl, flags; } lent_t;
static unsigned log_count(void) {
    uint8_t out[29];
    battery_log_read(0, out);
    return out[1] | out[2] << 8;
}
static lent_t log_entry(unsigned idx) {
    uint8_t out[29];
    battery_log_read((uint16_t)idx, out);
    const uint8_t *e = &out[11];
    lent_t r = {(unsigned)(e[0] | e[1] << 8), (unsigned)(e[2] | e[3] << 8), (unsigned)(e[6] | e[15] << 8),
                e[11], (unsigned)(e[12] | e[13] << 8), e[14], e[9]};
    return r;
}
static uint32_t log_since(void) {
    uint8_t out[29];
    battery_log_read(0, out);
    return (uint32_t)out[5] | (uint32_t)out[6] << 8 | (uint32_t)out[7] << 16 | (uint32_t)out[8] << 24;
}

/* The log starts at the first tick: now_ms 100, so its periods end at
 * 900,100 + k x 900,000 in log time. */
#define P0_END 900100u

/* A sleep inside one period: no entry until the period's end, then one with
 * the sleep's seconds and energy in it. */
static void s_log_within(void) {
    c_start(96);              /* now 30,000 */
    run_s(200);               /* 230,000 */
    sleep_for(300000u, 200);  /* 5 min asleep, 0.2 s of recovery: log time 530,200 */
    CHECK(log_count() == 0, "no entry yet: %u", log_count());
    run_s(370);               /* log time 900,200 */
    CHECK(log_count() == 1, "one entry at the period's end: %u", log_count());
    lent_t e = log_entry(0);
    CHECK(e.slept == 300, "300 s slept in it: %u", e.slept);
    /* 600 s awake at 303.8 mW (~50.6 mWh) + 300 s asleep (~1.1 mWh) */
    CHECK(e.e_x10 >= 500 && e.e_x10 <= 525, "its energy, the sleep's included: %u x 0.1 mWh", e.e_x10);
    CHECK(e.c5_n >= 230, "and every report: %u", e.c5_n);
}

/* A night across many periods, starting mid-period. */
static void s_log_across(void) {
    c_start(96);                     /* 30,000 */
    run_s(570);                      /* 600,000 */
    uint32_t e0 = battery_energy_used_mwh_x10();
    sleep_for(16u * 3600000u, 300);  /* asleep 600,000 .. 58,200,000; accounted at 58,200,300 */
    /* Boundaries through now: 900,100 + k x 900,000 <= 58,200,300: k = 0..63, 64 entries */
    CHECK(log_count() == 64, "64 periods closed: %u", log_count());
    lent_t first = log_entry(0);
    CHECK(first.slept == 300, "the first keeps its frozen contents and 300 s slept: %u", first.slept);
    CHECK(first.c5_n >= 230, "its reports from before the sleep: %u", first.c5_n);
    unsigned slept = 0, e_sum = 0, bad = 0;
    for (unsigned k = 0; k < 64; k++) {
        lent_t e = log_entry(k);
        slept += e.slept;
        e_sum += e.e_x10;
        if (k > 0 && (e.slept != 900 || e.c5_n != 0 || e.led != 0 || e.bkl != 0)) bad++;
        if (k > 0 && e.level > log_entry(k - 1).level) bad++;   /* never rises asleep */
    }
    CHECK(bad == 0, "every later entry slept whole, dark, no reports, the level falling: %u bad", bad);
    /* 300 + 63 x 900 = 57,000 s of the 57,600 slept; the last 600 in the open period */
    CHECK(slept == 57000, "57,000 s slept in the closed entries: %u", slept);
    /* the closed entries' energy: 600 s awake before (in the first) + 57,000 s asleep */
    double want = (303.8 * 600.0 + SLEEP_MW * 57000.0) / 3600.0 * 10.0;
    CHECK(fabs(e_sum - want) <= 64.0 + 3.0, "their energy %u x 0.1 mWh (want %.0f)", e_sum, want);
    CHECK(battery_energy_used_mwh_x10() - e0 >= (unsigned)(SLEEP_MW * 16 * 10 - 2), "e_used the whole night");
    /* Dating: the newest entry ended at 57,600,100 in log time; now is 58,200,300. */
    CHECK(log_since() == 58200300u - 57600100u, "the newest entry's age in log time: %u", log_since());
    run_s(10);
    CHECK(log_count() == 64, "no extra entry after the wake: %u", log_count());
}

/* A sleep longer than the log: its last 720 entries, all slept. */
static void s_log_long(void) {
    c_start(96);
    sleep_for(8u * 24u * 3600000u, 300);
    CHECK(log_count() == 720, "the log full: %u", log_count());
    unsigned bad = 0;
    for (unsigned k = 0; k < 720; k++) if (log_entry(k).slept != 900) bad++;
    CHECK(bad == 0, "every entry slept whole: %u not", bad);
}

/* Between the wake and the accounting: HC_BATTLOG pending, no period closes
 * even past its end, and the reports arriving go to the live period. */
static void s_log_pending(void) {
    c_start(96);
    run_s(860);                      /* 890,000 */
    battery_sleep_enter();
    battery_sleep_wake();
    uint8_t out[29];
    battery_log_read(0, out);
    CHECK(out[0] == BATTERY_LOG_PENDING, "pending after the wake: %u", out[0]);
    run_s(20);                       /* 910,000: past the period's end on awake time alone */
    CHECK(log_count() == 0, "no period closed before the accounting: %u", log_count());
    battery_sleep_account(5000u);
    battery_log_read(0, out);
    CHECK(out[0] == BATTERY_LOG_VERSION, "the version back: %u", out[0]);
    CHECK(log_count() == 1, "one entry once accounted: %u", log_count());
    lent_t e = log_entry(0);
    CHECK(e.slept == 5, "5 s slept in it: %u", e.slept);
    unsigned before = e.c5_n;
    run_s(900);
    lent_t e2 = log_entry(1);
    CHECK(e2.c5_n >= 360 && before >= 350, "the 20 s of live reports in the next period, not the frozen one: %u, %u",
          before, e2.c5_n);
}

/* Codex's case (deep-sleep verify 3): the period ends at 900 s; the sleep runs
 * 890-899; the accounting comes at 901. The period ending at 900 keeps its
 * frozen contents and 9 s slept; the live 899-901 goes to the next. */
static void s_log_recovery_boundary(void) {
    c_start(96);                     /* 30,000 */
    run_s(860);                      /* 890,000; log time 890,000 */
    unsigned frozen_reports = 0;
    battery_sleep_enter();
    battery_sleep_wake();
    run_s(2);                        /* the recovery: log time after the account 890,000 + 9,000 + 2,000 */
    battery_sleep_account(9000u);
    CHECK(log_count() == 1, "the period ending at 900,100 closed: %u", log_count());
    lent_t e = log_entry(0);
    frozen_reports = e.c5_n;
    CHECK(e.slept == 9, "with the 9 s slept: %u", e.slept);
    CHECK(frozen_reports >= 340, "and its frozen reports: %u", frozen_reports);
    run_s(900);
    lent_t e2 = log_entry(1);
    CHECK(e2.slept == 0, "the next period slept none: %u", e2.slept);
}

/* Asleep on USB power (the bench, or a charge wake): no energy, the seconds. */
static void s_usb(void) {
    vdd_mv = 4300; chrg_low = true; c5 = 100;
    run_s(120);
    uint32_t e0 = battery_energy_used_mwh_x10();
    sleep_for(3600000u, 300);
    CHECK(battery_energy_used_mwh_x10() == e0, "no energy for a sleep on USB: %u",
          battery_energy_used_mwh_x10() - e0);
}

/* An accounting with no wake before it does nothing. */
static void s_no_wake(void) {
    c_start(96);
    uint16_t l0 = battery_level_permille();
    battery_sleep_account(3600000u);
    CHECK(battery_level_permille() == l0, "no wake, no debit: %u -> %u", l0, battery_level_permille());
    CHECK(!battery_sleep_pending(), "nothing pending");
}

/* A v7 log replayed with its sleeps (codex, the deep-sleep review 16): two
 * awake hours at 5C 96, a 16 h night as 64 slept entries, two awake hours.
 * The replay plays each entry's awake part, then its slept part through the
 * hand-over, so the night costs its sleep energy and never matures the
 * average. A CSV with no vdd_avg column takes the supply from the flags. */
static void s_replay_v7(void) {
    char path[] = "/tmp/battery_sim_v7_XXXXXX";
    int fd = mkstemp(path);
    FILE *f = fd >= 0 ? fdopen(fd, "w") : NULL;
    if (!f) { CHECK(0, "cannot write a temp CSV"); return; }
    fprintf(f, "time,slept_s,e_mwh,c5_mean,c5_n,c5_min,c5_max,pack_mv,level,led_pm,bkl,charging,external,flags_any\n");
    int row = 0;
    for (int k = 0; k < 8 + 64 + 8; k++, row++) {
        int slept = (k >= 8 && k < 72) ? 900 : 0;
        int mins = row * 15;
        fprintf(f, "2026-10-20 %02d:%02d,%d,0.0,%s,%d,%s,%s,,,%s,%s,0,0,on_batt\n", (mins / 60) % 24, mins % 60,
                slept, slept ? "" : "96.00", slept ? 0 : 360, slept ? "" : "96", slept ? "" : "96",
                slept ? "0" : "1000", slept ? "0" : "10");
    }
    fclose(f);
    /* rp_epoch wraps the hour at 24: rows past midnight would go backwards, so
     * the durations are fixed here after the load instead. */
    if (!rp_load(path)) { remove(path); return; }
    remove(path);
    for (int i = 0; i < rp_n; i++) rp[i].dur_s = 900;
    vdd_mv = 3900; chrg_low = false; c5 = 0xFF;
    run_s(2);
    for (int i = 0; i < 8; i++) rp_play(i);
    uint16_t l0 = battery_level_permille();
    uint16_t m0 = battery_avg_minutes();
    for (int i = 8; i < 72; i++) rp_play(i);
    CHECK(battery_avg_minutes() == m0, "the night matured nothing: %u -> %u minutes", m0, battery_avg_minutes());
    double drop = l0 - battery_level_permille();
    double want = SLEEP_MW * 16.0 * 1000.0 / 15579.0;
    CHECK(fabs(drop - want) <= 1.5, "the night cost its sleep energy: %.0f pm (want %.1f)", drop, want);
    CHECK(battery_supply() == BATTERY_SUPPLY_BATTERY, "on battery from the flags, no vdd_avg column");
}
