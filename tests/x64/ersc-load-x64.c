/* ersc-load-x64.c — load Seamless Co-op's ersc.dll the way Mod Engine 2 does, without the game.
 *
 * ersc.dll (Themida) sets EFLAGS.TF while it initializes and takes a SINGLE_STEP at
 * ersc.dll+0x30c390 that its frame handler (ersc.dll+0x31eed6) should accept. Under
 * Madeira the handler returns 0xffffffff and ntdll raises STATUS_INVALID_DISPOSITION
 * into it until the stack overflows. Wine patch 0002 logs the handler's return value
 * and context as [guest-seh] lines; this exe only gets ersc.dll that far:
 *
 *   1. load ersc.dll's imports, then LoadLibraryA(argv[1], default "ersc.dll"): DllMain
 *      and its TLS callbacks run;
 *   2. GetProcAddress("modengine_ext_init") and call it as Mod Engine 2 does, with a
 *      connector whose vtable entries log and return 0, and an out pointer for the
 *      extension it creates (argv[2] = "noinit" skips this step);
 *   3. wait 20 s for any thread it started, then exit 0.
 *
 * Every step is logged before it runs, to stdout and ersc-load-x64.log next to the exe,
 * so a crash shows which one triggered the single step. ersc.dll imports
 * steam_api64.dll (only SteamAPI_GetHSteamPipe); steam_api64-stub.c stands in for it.
 *
 * Build: tests/x64/build.sh ersc-load-x64
 */

#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

static HANDLE g_fh = INVALID_HANDLE_VALUE;

static void say(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 2, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
    buf[n++] = '\n';
    DWORD w;
    HANDLE out = GetStdHandle(STD_OUTPUT_HANDLE);
    if (out && out != INVALID_HANDLE_VALUE) WriteFile(out, buf, n, &w, NULL);
    if (g_fh != INVALID_HANDLE_VALUE) { WriteFile(g_fh, buf, n, &w, NULL); FlushFileBuffers(g_fh); }
}

/* Mod Engine 2's ModEngineExtensionConnector: an object with a vtable. ersc.dll may call
 * into it from modengine_ext_init; each entry logs its index and returns 0. */
#define VT_N 32
static void *g_vtbl[VT_N];
static struct { void **vtbl; void *pad[31]; } g_connector = {g_vtbl};

#define STUB(i) static ULONG64 vt_##i(void *self, void *a, void *b, void *c) \
    { say("[ersc-load] connector vtable[%d](this=%p, %p, %p, %p)", i, self, a, b, c); return 0; }
STUB(0) STUB(1) STUB(2) STUB(3) STUB(4) STUB(5) STUB(6) STUB(7)
STUB(8) STUB(9) STUB(10) STUB(11) STUB(12) STUB(13) STUB(14) STUB(15)
STUB(16) STUB(17) STUB(18) STUB(19) STUB(20) STUB(21) STUB(22) STUB(23)
STUB(24) STUB(25) STUB(26) STUB(27) STUB(28) STUB(29) STUB(30) STUB(31)
static void *const c_stubs[VT_N] = {
    vt_0, vt_1, vt_2, vt_3, vt_4, vt_5, vt_6, vt_7, vt_8, vt_9, vt_10, vt_11, vt_12, vt_13, vt_14, vt_15,
    vt_16, vt_17, vt_18, vt_19, vt_20, vt_21, vt_22, vt_23, vt_24, vt_25, vt_26, vt_27, vt_28, vt_29, vt_30, vt_31,
};

typedef ULONG64 (*ext_init_fn)(void *connector, void **extension);

int main(int argc, char **argv)
{
    char path[MAX_PATH];
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    if (n > 4 && n < MAX_PATH) {
        memcpy(path + n - 4, ".log", 5);
        g_fh = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, 0, NULL);
    }
    const char *dll = argc > 1 ? argv[1] : "ersc.dll";
    int do_init = !(argc > 2 && !strcmp(argv[2], "noinit"));
    memcpy(g_vtbl, c_stubs, sizeof(g_vtbl));

    /* ersc.dll imports WS2_32.dll, ADVAPI32.dll, CRYPT32.dll ... in upper case; the
     * prefix's lookup of those file names is case-sensitive, so load them first by the
     * bundle's names, as the game has them loaded before Mod Engine 2 loads ersc.dll. */
    static const char *const deps[] = {"user32.dll", "ws2_32.dll", "advapi32.dll", "crypt32.dll",
                                       "wldap32.dll", "normaliz.dll", "steam_api64.dll"};
    for (unsigned i = 0; i < sizeof(deps) / sizeof(deps[0]); i++) {
        HMODULE d = LoadLibraryA(deps[i]);
        say("[ersc-load] preload %s: %p (error %lu)", deps[i], (void *)d, d ? 0 : GetLastError());
    }

    say("[ersc-load] pid %lu, LoadLibraryA(\"%s\") ...", GetCurrentProcessId(), dll);
    HMODULE h = LoadLibraryA(dll);
    DWORD err = GetLastError();
    if (!h) {
        say("[ersc-load] LoadLibraryA failed: error %lu", err);
        return 1;
    }
    say("[ersc-load] loaded at %p (single step expected at %p, handler %p)",
        (void *)h, (char *)h + 0x30c390, (char *)h + 0x31eed6);

    if (do_init) {
        ext_init_fn init = (ext_init_fn)GetProcAddress(h, "modengine_ext_init");
        say("[ersc-load] modengine_ext_init = %p", (void *)init);
        if (init) {
            void *ext = NULL;
            say("[ersc-load] calling modengine_ext_init(%p, %p) ...", (void *)&g_connector, (void *)&ext);
            ULONG64 r = init(&g_connector, &ext);
            say("[ersc-load] modengine_ext_init returned 0x%llx, extension %p (vtable %p)",
                r, ext, ext ? *(void **)ext : NULL);
        }
    }

    say("[ersc-load] waiting 20 s for ersc.dll's threads ...");
    Sleep(20000);
    say("[ersc-load] still alive, exiting 0");
    return 0;
}
