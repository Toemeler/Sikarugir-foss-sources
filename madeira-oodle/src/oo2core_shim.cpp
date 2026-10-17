/* madeira-oodle: oo2core_6_win64.dll as an ARM64EC Wine builtin.
 *
 * WHY: on an M4 iPad, Elden Ring's single "CR2.Decomp_0" thread runs RAD's x86 Oodle
 * through the emulator at 50-98% of a core on every loading screen and while new
 * areas stream in, and the game waits on it (docs/ELDEN_RING_PERFORMANCE.md).
 *
 * WHAT: a game config that sets WINEDLLOVERRIDES=...;oo2core_6_win64=b and
 * MADEIRA_OODLE_NATIVE=1 gets this DLL instead of the game's own (the iOS loader's
 * exact-name opt-in route is in virtual_ios.c). OodleLZ_Decompress decodes natively with ooz's Kraken,
 * Mermaid, Selkie and Leviathan decoder (kraken.cpp, GPL-3.0-or-later). Anything it
 * does not take -- a history window (decBufBase), a threaded phase, a progress
 * callback, LZNA/BitKnit, a failure -- goes to the game's own DLL, loaded from a copy
 * under another name (the override would otherwise answer with this DLL again), and
 * so do the five other functions the game imports. Without the override nothing
 * loads this file. env MADEIRA_OODLE_NATIVE=0 sends every call to the game's DLL.
 *
 * ooz writes up to 64 bytes past the end of its output and its bit readers read past
 * the end of their input, so both go through private buffers with 64 bytes of room;
 * the game's buffers see exactly rawLen bytes written, as with RAD's decoder. */
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

extern "C" long long madeira_ooz_decompress(const unsigned char *src, size_t src_len,
                                            unsigned char *dst, size_t dst_len);
extern "C" void *madeira_ooz_decoder_create(void);
extern "C" long long madeira_ooz_decompress_dec(void *decoder, const unsigned char *src, size_t src_len,
                                                unsigned char *dst, size_t dst_len);

#define PAD 64

/* ---- reusable decode slots -------------------------------------------------------------
 * A fresh decoder (432 KB) plus fresh input and output buffers on every call means fresh
 * pages on every call: outputs of a megabyte or more are their own reservation, which
 * Madeira's swap tier backs with file pages, and the last run measured ~21k such first-touch
 * faults a second (~47 us each) on threads copying into new memory. A slot keeps its decoder
 * and buffers, so after the first calls a decode touches only pages it already has. Four
 * slots: the game decodes on CR2.Decomp_0 and now and then elsewhere; a call that finds all
 * four busy, or needs more than SLOT_MAX, takes the one-shot path as before. */
#define NSLOTS 4
#define SLOT_MAX ((size_t)64 << 20)
struct slot {
    volatile LONG busy;
    void *dec;
    unsigned char *in, *out;
    size_t in_cap, out_cap;
};
static struct slot g_slots[NSLOTS];

static struct slot *slot_take(void) {
    for (int i = 0; i < NSLOTS; i++)
        if (InterlockedCompareExchange(&g_slots[i].busy, 1, 0) == 0) return &g_slots[i];
    return NULL;
}
static void slot_give(struct slot *s) { InterlockedExchange(&s->busy, 0); }
/* Room for need bytes (padding included), grown in 1 MB steps; on failure the slot keeps nothing. */
static unsigned char *slot_room(unsigned char **p, size_t *cap, size_t need) {
    if (*cap >= need) return *p;
    free(*p);
    *cap = (need + ((size_t)1 << 20) - 1) & ~(((size_t)1 << 20) - 1);
    if (!(*p = (unsigned char *)malloc(*cap))) *cap = 0;
    return *p;
}

/* ---- log: Wine's own debug output, as madeira_d3d12's d3d12_log ---------------- */
static void mlog(const char *fmt, ...) {
    typedef int (__cdecl *dbg_output_fn)(const char *);
    dbg_output_fn out = NULL;
    char buf[512];
    va_list ap;
    size_t len;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf - 2, fmt, ap);
    va_end(ap);
    len = strlen(buf);
    if (!len || buf[len - 1] != '\n') { buf[len] = '\n'; buf[len + 1] = 0; }
    HMODULE nt = GetModuleHandleA("ntdll.dll");
    if (nt) out = (dbg_output_fn)GetProcAddress(nt, "__wine_dbg_output");
    if (out) out(buf);
}

/* ---- the game's own DLL, from a copy ------------------------------------------------ */
typedef intptr_t (*fn2_t)(intptr_t, intptr_t);
typedef intptr_t (*fn6_t)(intptr_t, intptr_t, intptr_t, intptr_t, intptr_t, intptr_t);
typedef intptr_t (*fn10_t)(intptr_t, intptr_t, intptr_t, intptr_t, intptr_t,
                           intptr_t, intptr_t, intptr_t, intptr_t, intptr_t);
typedef intptr_t (*fn14_t)(intptr_t, intptr_t, intptr_t, intptr_t, intptr_t, intptr_t, intptr_t,
                           intptr_t, intptr_t, intptr_t, intptr_t, intptr_t, intptr_t, intptr_t);
static fn10_t o_Compress;
static fn14_t o_Decompress;
static fn2_t o_GetCompressedBufferSizeNeeded, o_CompressOptions_GetDefault, o_DecoderMemorySizeNeeded;
static fn6_t o_GetCompressedStepForRawStep;
static INIT_ONCE g_once = INIT_ONCE_STATIC_INIT;

static BOOL CALLBACK load_orig(PINIT_ONCE once, PVOID param, PVOID *ctx) {
    WCHAR src[MAX_PATH + 32], dst[MAX_PATH + 96];
    WCHAR *slash;
    HMODULE h = NULL;
    DWORD n = GetEnvironmentVariableW(L"MADEIRA_OODLE_ORIGINAL_PATH", src, MAX_PATH);
    (void)once; (void)param; (void)ctx;
    if (!n) {
        n = GetModuleFileNameW(NULL, src, MAX_PATH);
        slash = wcsrchr(src, L'\\');
        if (n && n < MAX_PATH && slash) lstrcpyW(slash + 1, L"oo2core_6_win64.dll");
        else n = 0;
    }
    if (!n || n >= MAX_PATH) {
        mlog("[oodle-native] the game's folder is unknown: calls the native decoder does not take fail\n");
        return TRUE;
    }
    n = GetTempPathW(MAX_PATH, dst);
    if (!n || n >= MAX_PATH) lstrcpyW(dst, L"C:\\windows\\temp\\");
    swprintf(dst + wcslen(dst), 80, L"madeira-oo2core6-%lu-%llu.dll",
             GetCurrentProcessId(), (unsigned long long)GetTickCount64());
    /* Never accept a stale DLL from a previous game version after a failed copy. */
    if (!CopyFileW(src, dst, TRUE)) {
        mlog("[oodle-native] could not copy the game's oo2core_6_win64.dll (error %lu): calls the native decoder does not take fail\n",
             GetLastError());
        return TRUE;
    }
    if (!(h = LoadLibraryW(dst))) {
        mlog("[oodle-native] the copy of the game's oo2core_6_win64.dll did not load (error %lu)\n", GetLastError());
        return TRUE;
    }
    o_Compress = (fn10_t)GetProcAddress(h, "OodleLZ_Compress");
    o_Decompress = (fn14_t)GetProcAddress(h, "OodleLZ_Decompress");
    o_GetCompressedBufferSizeNeeded = (fn2_t)GetProcAddress(h, "OodleLZ_GetCompressedBufferSizeNeeded");
    o_CompressOptions_GetDefault = (fn2_t)GetProcAddress(h, "OodleLZ_CompressOptions_GetDefault");
    o_DecoderMemorySizeNeeded = (fn2_t)GetProcAddress(h, "OodleLZDecoder_MemorySizeNeeded");
    o_GetCompressedStepForRawStep = (fn6_t)GetProcAddress(h, "OodleLZ_GetCompressedStepForRawStep");
    mlog("[oodle-native] the game's own oo2core_6_win64.dll is loaded from a copy for the calls the native decoder does not take\n");
    return TRUE;
}
static void need_orig(void) { InitOnceExecuteOnce(&g_once, load_orig, NULL, NULL); }

/* ---- counters --------------------------------------------------------------------- */
static volatile LONG g_native, g_fallback, g_logged_fallbacks;
static volatile LONG64 g_native_bytes, g_native_ticks;
static int g_on;
static INIT_ONCE g_config_once = INIT_ONCE_STATIC_INIT;

static BOOL CALLBACK load_config(PINIT_ONCE once, PVOID param, PVOID *ctx) {
        (void)once; (void)param; (void)ctx;
        char v[8];
        DWORD n = GetEnvironmentVariableA("MADEIRA_OODLE_NATIVE", v, sizeof v);
        int on = !(n && n < sizeof v && v[0] == '0');
        mlog("[oodle-native] madeira-oodle: OodleLZ_Decompress %s (env MADEIRA_OODLE_NATIVE=0 hands every call to the game's own DLL)\n",
             on ? "decodes natively (ooz: Kraken, Mermaid, Selkie, Leviathan)" : "goes to the game's own DLL");
        g_on = on;
        return TRUE;
}
static int native_on(void) {
    InitOnceExecuteOnce(&g_config_once, load_config, NULL, NULL);
    return g_on;
}

static void note_native(intptr_t bytes, LONG64 ticks) {
    LONG n = InterlockedIncrement(&g_native);
    LONG64 b = InterlockedExchangeAdd64(&g_native_bytes, bytes) + bytes, t = InterlockedExchangeAdd64(&g_native_ticks, ticks) + ticks;
    if ((n & (n - 1)) == 0 || (n % 2048) == 0) {
        LARGE_INTEGER f;
        double ms;
        QueryPerformanceFrequency(&f);
        ms = 1000.0 * (double)t / (double)f.QuadPart;
        mlog("[oodle-native] %ld native decodes, %.1f MB in %.0f ms (%.0f MB/s); %ld went to the game's DLL\n",
             n, b / 1048576.0, ms, ms > 0 ? b / 1048576.0 / (ms / 1000.0) : 0.0, g_fallback);
    }
}

/* ---- exports ---------------------------------------------------------------------- */
extern "C" intptr_t OodleLZ_Decompress(intptr_t comp, intptr_t compLen, intptr_t raw, intptr_t rawLen,
                                       intptr_t fuzzSafe, intptr_t checkCRC, intptr_t verbosity,
                                       intptr_t decBufBase, intptr_t decBufSize, intptr_t callback,
                                       intptr_t callbackData, intptr_t decoderMemory, intptr_t decoderMemorySize,
                                       intptr_t threadPhase) {
    const char *why = NULL;
    /* enums arrive in the low 32 bits of their slot */
    int phase = (int)threadPhase;
    if (!native_on()) why = "switched off";
    else if (!comp || !raw || compLen <= 0 || rawLen <= 0 || rawLen > 0x7fff0000 || compLen > 0x7fff0000) why = "sizes";
    else if (decBufBase && decBufBase != raw) why = "history window";
    else if (phase != 3) why = "threaded phase";
    else if (callback) why = "progress callback";
    else if ((int)checkCRC) why = "CRC requested";
    if (!why && (size_t)compLen + PAD <= SLOT_MAX && (size_t)rawLen + PAD <= SLOT_MAX) {
        struct slot *s = slot_take();
        if (s) {
            LARGE_INTEGER t0, t1;
            long long n = -1;
            QueryPerformanceCounter(&t0);
            if (!s->dec) s->dec = madeira_ooz_decoder_create();
            unsigned char *in = slot_room(&s->in, &s->in_cap, (size_t)compLen + PAD);
            unsigned char *out = slot_room(&s->out, &s->out_cap, (size_t)rawLen + PAD);
            int ready = s->dec && in && out;
            if (ready) {
                memcpy(in, (const void *)comp, (size_t)compLen);
                memset(in + compLen, 0, PAD);
                n = madeira_ooz_decompress_dec(s->dec, in, (size_t)compLen, out, (size_t)rawLen);
                if (n == rawLen) memcpy((void *)raw, out, (size_t)rawLen);
            }
            slot_give(s);
            if (n == rawLen) {
                QueryPerformanceCounter(&t1);
                note_native(rawLen, t1.QuadPart - t0.QuadPart);
                return rawLen;
            }
            why = ready ? "stream the native decoder does not take" : "no memory";
        }
    }
    if (!why) {
        LARGE_INTEGER t0, t1;
        QueryPerformanceCounter(&t0);
        unsigned char *in = (unsigned char *)malloc((size_t)compLen + PAD);
        unsigned char *out = (unsigned char *)malloc((size_t)rawLen + PAD);
        if (in && out) {
            long long n;
            memcpy(in, (const void *)comp, (size_t)compLen);
            memset(in + compLen, 0, PAD);
            n = madeira_ooz_decompress(in, (size_t)compLen, out, (size_t)rawLen);
            if (n == rawLen) {
                memcpy((void *)raw, out, (size_t)rawLen);
                free(in); free(out);
                QueryPerformanceCounter(&t1);
                note_native(rawLen, t1.QuadPart - t0.QuadPart);
                return rawLen;
            }
            why = "stream the native decoder does not take";
        } else why = "no memory";
        free(in); free(out);
    }
    {
        LONG k = InterlockedIncrement(&g_fallback);
        if (InterlockedIncrement(&g_logged_fallbacks) <= 32 || (k % 1024) == 0)
            mlog("[oodle-native] call %ld to the game's DLL: %s (comp %lld bytes, raw %lld, phase %d)\n",
                 k, why, (long long)compLen, (long long)rawLen, phase);
    }
    need_orig();
    if (!o_Decompress) return 0;
    return o_Decompress(comp, compLen, raw, rawLen, fuzzSafe, checkCRC, verbosity, decBufBase, decBufSize,
                        callback, callbackData, decoderMemory, decoderMemorySize, threadPhase);
}

extern "C" intptr_t OodleLZ_Compress(intptr_t a1, intptr_t a2, intptr_t a3, intptr_t a4, intptr_t a5,
                                     intptr_t a6, intptr_t a7, intptr_t a8, intptr_t a9, intptr_t a10) {
    need_orig();
    return o_Compress ? o_Compress(a1, a2, a3, a4, a5, a6, a7, a8, a9, a10) : 0;
}
extern "C" intptr_t OodleLZ_GetCompressedBufferSizeNeeded(intptr_t a1, intptr_t a2) {
    need_orig();
    return o_GetCompressedBufferSizeNeeded ? o_GetCompressedBufferSizeNeeded(a1, a2) : 0;
}
extern "C" intptr_t OodleLZ_CompressOptions_GetDefault(intptr_t a1, intptr_t a2) {
    need_orig();
    return o_CompressOptions_GetDefault ? o_CompressOptions_GetDefault(a1, a2) : 0;
}
extern "C" intptr_t OodleLZDecoder_MemorySizeNeeded(intptr_t a1, intptr_t a2) {
    need_orig();
    return o_DecoderMemorySizeNeeded ? o_DecoderMemorySizeNeeded(a1, a2) : 0;
}
extern "C" intptr_t OodleLZ_GetCompressedStepForRawStep(intptr_t a1, intptr_t a2, intptr_t a3,
                                                        intptr_t a4, intptr_t a5, intptr_t a6) {
    need_orig();
    return o_GetCompressedStepForRawStep ? o_GetCompressedStepForRawStep(a1, a2, a3, a4, a5, a6) : 0;
}

extern "C" BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved) {
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) DisableThreadLibraryCalls(inst);
    return TRUE;
}
