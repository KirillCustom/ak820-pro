/* Host test of rtc.c's PCF write bookkeeping (rtc/rtc_pcf_sync.h): codex's
 * wireless-clock review, must-fixes 4 and 5. Build and run:
 *
 *   gcc -std=c11 -Wall -Wextra -I qmk_firmware-ak820pro/keyboards/a_jazz/ak820pro/rtc \
 *       scripts/rtc_pcf_sync_test.c -o pst && ./pst
 *
 * (On Windows, mingw64's gcc first on PATH: CLAUDE.md.) */
#include <stdio.h>
#include "rtc_pcf_sync.h"

static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

int main(void) {
    /* A request, a run, a verify: done, not stale. */
    pcf_sync_t s = {0};
    pcf_sync_host_accepted(&s);
    pcf_sync_request(&s);
    CHECK(s.pending && s.stale);
    pcf_sync_run_start(&s);
    CHECK(!pcf_sync_run_ok(&s));
    CHECK(!s.pending && !s.stale);

    /* Must-fix 4: a newer request during an older run survives its verify. */
    s = (pcf_sync_t){0};
    pcf_sync_host_accepted(&s);
    pcf_sync_request(&s);            /* the wireless sync */
    pcf_sync_run_start(&s);          /* its write planned */
    pcf_sync_host_accepted(&s);
    pcf_sync_request(&s);            /* USB corrects meanwhile */
    CHECK(pcf_sync_run_ok(&s));      /* the older write verifies: still pending */
    CHECK(s.pending && s.stale);
    pcf_sync_run_start(&s);          /* the newer one runs */
    CHECK(!pcf_sync_run_ok(&s));
    CHECK(!s.pending && !s.stale);

    /* The rtc fixes' review, must-fix 1: a request during a run marks it
     * superseded (the writer re-plans before writing), and re-starting the
     * run clears that. */
    s = (pcf_sync_t){0};
    pcf_sync_request(&s);
    pcf_sync_run_start(&s);
    CHECK(!pcf_sync_superseded(&s));
    pcf_sync_request(&s);            /* a legacy set mid-run */
    CHECK(pcf_sync_superseded(&s));
    pcf_sync_run_start(&s);          /* the writer re-plans */
    CHECK(!pcf_sync_superseded(&s));
    CHECK(!pcf_sync_run_ok(&s));

    /* Must-fix 5: a give-up is not pending (the board may sleep) but stays
     * stale, and is asked for again after the retry delay, not before. */
    s = (pcf_sync_t){0};
    pcf_sync_host_accepted(&s);
    pcf_sync_request(&s);
    pcf_sync_run_start(&s);
    pcf_sync_gave_up(&s, 1000);
    CHECK(!s.pending && s.stale);
    CHECK(!pcf_sync_retry_due(&s, 1000));
    CHECK(!pcf_sync_retry_due(&s, 1000 + PCF_SYNC_RETRY_S - 1));
    CHECK(pcf_sync_retry_due(&s, 1000 + PCF_SYNC_RETRY_S));
    pcf_sync_request(&s);
    CHECK(!pcf_sync_retry_due(&s, 5000));   /* pending again: not twice */
    pcf_sync_run_start(&s);
    pcf_sync_run_ok(&s);
    CHECK(!s.stale);
    CHECK(!pcf_sync_retry_due(&s, 99999));

    /* The retry clock wraps with rtc_seconds_count. */
    s = (pcf_sync_t){0};
    pcf_sync_host_accepted(&s);
    pcf_sync_gave_up(&s, 0xFFFFFFF0u);
    CHECK(!pcf_sync_retry_due(&s, 0xFFFFFFF0u + 10));
    CHECK(pcf_sync_retry_due(&s, 0xFFFFFFF0u + PCF_SYNC_RETRY_S));

    /* A synchronous write clears stale; an idle, fresh PCF is never due. */
    s = (pcf_sync_t){0};
    pcf_sync_host_accepted(&s);
    pcf_sync_written(&s);
    CHECK(!s.stale && !pcf_sync_retry_due(&s, 12345));

    /* The board's own request (not a host correction) never makes it stale. */
    s = (pcf_sync_t){0};
    pcf_sync_request(&s);
    pcf_sync_run_start(&s);
    pcf_sync_gave_up(&s, 10);
    CHECK(!s.stale && !pcf_sync_retry_due(&s, 1000));

    printf(fails ? "%d FAILED\n" : "rtc_pcf_sync: all passed\n", fails);
    return fails != 0;
}
