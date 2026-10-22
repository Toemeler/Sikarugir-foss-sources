// harness_main.m - headless Madeira for the iOS Simulator (.github/workflows/sim-seh.yml).
//
// Runs one guest exe through the app's own launch path (WineServerBridge.m,
// WineProcessBridge.m) with no UI, and copies the session log to stdout so
// `xcrun simctl spawn` hands it straight to CI.
//
//   madeira-sim <exe> [args...]
//
// <exe> is a bare name in the bundle's arm64ec-windows/ (or i386-windows/), linked into
// C:\windows\system32 (syswow64) exactly as on the device, or a C:\ path.
// SIM_TIMEOUT (seconds, default 300) bounds the run. SIM_JIT_POOL_MB sizes the pool.
//
// The one thing the app does that this does differently is the JIT pool. On a device
// StikDebug allocates its RX half; the Simulator needs no debugger, so the pool is an
// anonymous region made RX plus a vm_remap'd RW alias - the same dual mapping, with the
// same WINE_IOS_JIT_* environment the app exports (StikJITHelper.allocatePool).

#import <Foundation/Foundation.h>
#include <mach/mach.h>
#include <libkern/OSCacheControl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "WineServerBridge.h"
#include "WineProcessBridge.h"

extern unsigned long madeira_early_window_base, madeira_early_window_size;
extern unsigned long madeira_early_pool_base, madeira_early_pool_size;
extern uintptr_t sim_txm_pool_lo, sim_txm_pool_hi;   /* txm_shim.c */
extern unsigned long sim_txm_dropped(void);

static int g_out = -1;   /* the real stdout; the launcher points fd 1 and 2 at the log */

static void say(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void say(const char *fmt, ...)
{
    char buf[2048];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
    buf[n++] = '\n';
    write(g_out >= 0 ? g_out : 1, "[sim-harness] ", 14);
    write(g_out >= 0 ? g_out : 1, buf, n);
}

/* ---------------------------------------------------------------- JIT pool */

static int is_bad_pool_addr(vm_address_t a, vm_size_t n)
{
    /* StikJITHelper's two placement rules: FEX's dispatcher emit needs the pool at or
     * above 0x119000000, and nothing may sit in the guest window [0x70,0x80)G. */
    return a < 0x119000000ul || (a < 0x8000000000ul && a + n > 0x7000000000ul);
}

static int make_pool(size_t want)
{
    mach_port_t task = mach_task_self();
    vm_address_t rx = 0, rw = 0;
    vm_size_t size = want;
    kern_return_t kr;

    if (madeira_early_window_base && madeira_early_window_size) {
        char v[64];
        snprintf(v, sizeof(v), "%lx:%lx", madeira_early_window_base, madeira_early_window_size);
        setenv("WINE_IOS_EXE_WINDOW", v, 1);
        say("executable window held since image load: %s", v);
    }

    /* The early placeholder directly above the window is where the app puts the pool. */
    if (madeira_early_pool_base && madeira_early_pool_size) {
        if (size > madeira_early_pool_size) size = madeira_early_pool_size;
        rx = madeira_early_pool_base;
        kr = vm_allocate(task, &rx, size, VM_FLAGS_FIXED | VM_FLAGS_OVERWRITE);
        say("pool at the early placeholder 0x%lx+%luMB: kr=%d", (unsigned long)rx,
            (unsigned long)(size >> 20), kr);
        if (kr != KERN_SUCCESS) rx = 0;
    }
    if (!rx) {
        size = want;
        kr = vm_allocate(task, &rx, size, VM_FLAGS_ANYWHERE);
        if (kr != KERN_SUCCESS) { say("FAIL: vm_allocate(pool) kr=%d", kr); return -1; }
        if (is_bad_pool_addr(rx, size))
            say("WARNING: pool 0x%lx+%luMB breaks the device placement rules", (unsigned long)rx,
                (unsigned long)(size >> 20));
    }

    vm_prot_t cur = 0, max = 0;
    kr = vm_remap(task, &rw, size, 0, VM_FLAGS_ANYWHERE, task, rx, FALSE, &cur, &max, VM_INHERIT_NONE);
    if (kr != KERN_SUCCESS) { say("FAIL: vm_remap(RW alias) kr=%d", kr); return -1; }
    if ((kr = vm_protect(task, rw, size, FALSE, VM_PROT_READ | VM_PROT_WRITE)) != KERN_SUCCESS) {
        say("FAIL: vm_protect(RW alias) kr=%d", kr); return -1;
    }
    /* The pool is the one range txm_shim.c lets execute, as TXM does on a device. */
    sim_txm_pool_lo = rx;
    sim_txm_pool_hi = rx + size;
    if ((kr = vm_protect(task, rx, size, FALSE, VM_PROT_READ | VM_PROT_EXECUTE)) != KERN_SUCCESS) {
        say("FAIL: vm_protect(RX) kr=%d", kr); return -1;
    }

    /* Prove the pair before Wine relies on it: write through RW, run from RX. */
    static const uint32_t ret42[] = { 0xD2800540 /* mov x0,#42 */, 0xD65F03C0 /* ret */ };
    memcpy((void *)rw, ret42, sizeof(ret42));
    sys_icache_invalidate((void *)rx, sizeof(ret42));
    long r = ((long (*)(void))rx)();
    memset((void *)rw, 0, sizeof(ret42));
    say("JIT pool RX=0x%lx RW=0x%lx size=%luMB, execute test returned %ld (%s)", (unsigned long)rx,
        (unsigned long)rw, (unsigned long)(size >> 20), r, r == 42 ? "OK" : "FAIL");
    if (r != 42) return -1;

    char v[32];
    snprintf(v, sizeof(v), "%lx", (unsigned long)rx);   setenv("WINE_IOS_JIT_RX", v, 1);
    snprintf(v, sizeof(v), "%lx", (unsigned long)rw);   setenv("WINE_IOS_JIT_RW", v, 1);
    snprintf(v, sizeof(v), "%lx", (unsigned long)size); setenv("WINE_IOS_JIT_SIZE", v, 1);
    /* fex_get_jit_write_offset() reads FEXBridge's own pool, which needs a debugger;
     * the launcher keeps a value already in the environment. */
    snprintf(v, sizeof(v), "0x%llx", (unsigned long long)(rw - rx));
    setenv("MADEIRA_JIT_WRITE_OFFSET", v, 1);
    return 0;
}

/* ------------------------------------------------------------- log follower */

static char g_log_path[1024];
static volatile int g_follow = 1;

static void *follow_log(void *arg)
{
    (void)arg;
    char buf[16384];
    off_t pos = 0;
    int fd = -1;
    for (;;) {
        int stop = !g_follow;
        if (fd < 0) fd = open(g_log_path, O_RDONLY);
        if (fd >= 0) {
            ssize_t n;
            while ((n = pread(fd, buf, sizeof(buf), pos)) > 0) {
                write(g_out, buf, n);
                pos += n;
            }
        }
        if (stop) break;
        usleep(100 * 1000);
    }
    if (fd >= 0) close(fd);
    return NULL;
}

/* -------------------------------------------------------------------- main */

int main(int argc, char *argv[])
{
    @autoreleasepool {
        g_out = dup(STDOUT_FILENO);
        signal(SIGPIPE, SIG_IGN);

        const char *exe = argc > 1 ? argv[1] : getenv("MADEIRA_EXE");
        if (!exe || !*exe) { say("usage: madeira-sim <exe> [args...]"); return 2; }
        setenv("MADEIRA_EXE", exe, 1);
        if (argc > 2) {
            NSMutableArray *a = [NSMutableArray array];
            for (int i = 2; i < argc; i++) [a addObject:@(argv[i])];
            setenv("MADEIRA_ARGS", [a componentsJoinedByString:@" "].UTF8String, 1);
        }
        const char *to = getenv("SIM_TIMEOUT");
        double timeout = to && atof(to) > 0 ? atof(to) : 300;
        const char *pm = getenv("SIM_JIT_POOL_MB");
        size_t pool_mb = pm && atol(pm) > 0 ? (size_t)atol(pm) : 896;

        NSString *bundle = NSBundle.mainBundle.bundlePath;
        NSString *docs = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
        [NSFileManager.defaultManager createDirectoryAtPath:docs withIntermediateDirectories:YES attributes:nil error:nil];
        NSString *prefix = [docs stringByAppendingPathComponent:@"wine"];
        snprintf(g_log_path, sizeof(g_log_path), "%s/madeira-log.txt", docs.UTF8String);
        unlink(g_log_path);

        say("exe=%s args=%s", exe, getenv("MADEIRA_ARGS") ?: "");
        say("bundle=%s", bundle.UTF8String);
        say("HOME=%s docs=%s", getenv("HOME") ?: "(unset)", docs.UTF8String);
        say("timeout=%.0fs pool=%zuMB", timeout, pool_mb);

        if (make_pool(pool_mb << 20) != 0) { say("RESULT: no JIT pool"); return 3; }

        pthread_t follower;
        pthread_create(&follower, NULL, follow_log, NULL);

        if (wineserver_start(prefix.UTF8String) != 0) { say("RESULT: wineserver_start failed"); return 4; }
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:60];
        while (!wineserver_is_ready() && wineserver_is_running() && [deadline timeIntervalSinceNow] > 0)
            [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        if (!wineserver_is_ready()) {
            say("RESULT: wineserver not ready (running=%d)", wineserver_is_running());
            g_follow = 0; pthread_join(follower, NULL);
            return 5;
        }
        say("wineserver ready");

        wine_exit_status_reset();
        if (wine_process_start(prefix.UTF8String) != 0) { say("RESULT: wine_process_start failed"); return 6; }

        /* The main thread keeps its run loop turning: parts of the app's Wine glue
         * dispatch to the main queue. */
        deadline = [NSDate dateWithTimeIntervalSinceNow:timeout];
        int timed_out = 0;
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.5]];
        while (wine_process_is_running()) {
            if ([deadline timeIntervalSinceNow] <= 0) { timed_out = 1; break; }
            [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
        }
        usleep(500 * 1000);
        g_follow = 0;
        pthread_join(follower, NULL);

        uint32_t st = 0;
        int crashed = wine_crash_exit_status(&st);
        say("execute requests refused outside the pool (TXM emulation): %lu", sim_txm_dropped());
        if (timed_out) say("RESULT: TIMEOUT after %.0fs (guest still running)", timeout);
        else if (crashed) say("RESULT: guest ended with status 0x%08x", st);
        else say("RESULT: guest exited");
        _exit(timed_out ? 124 : crashed ? 1 : 0);
    }
}
