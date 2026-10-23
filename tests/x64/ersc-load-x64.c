/* ersc-load-x64.c — load Seamless Co-op's ersc.dll without the game.
 *
 * ersc.dll (Themida) sets EFLAGS.TF while it initializes and takes a SINGLE_STEP
 * (v1.9.9 on the iPad: at ersc.dll+0x30c390) that its frame handler (+0x31eed6) should
 * accept. Under Madeira the handler returns 0xffffffff and ntdll raises
 * STATUS_INVALID_DISPOSITION into it until the stack overflows. Wine patch 0002 logs
 * the handler's return value and context as [guest-seh] lines; this exe only gets
 * ersc.dll that far. ersc.dll is expected next to the exe (system32 in the harness).
 *
 *   ersc-load-x64 [self] [noinit] [steam]
 *       load ersc.dll's imports, then LoadLibraryA("ersc.dll") in this process, then
 *       call modengine_ext_init as Mod Engine 2 does, with a connector whose vtable
 *       entries log and return 0 ("noinit" skips it); wait 20 s, exit 0.
 *   ersc-load-x64 inject [steam]
 *       what ersc_launcher.exe did on the iPad: start eldenring.exe (a copy of this exe,
 *       next to it) suspended, and load ersc.dll into it with a remote thread starting at
 *       LoadLibraryA. The first remote thread runs the new process's loader init, as the
 *       injected thread did in the game. ersc.dll's imports are loaded the same way first
 *       (the game has them loaded; the prefix's lookup of their upper-case names fails).
 *       SteamAppId/SteamGameId are 1245620 in its environment. Waits for the thread,
 *       then 20 s more ("wait=N": N s), then ends the child. "resume" resumes the child's main
 *       thread once ersc.dll is loaded, as ersc_launcher.exe does before the game runs.
 *   ersc-load-x64 child
 *       what eldenring.exe runs if it is ever resumed: nothing (sleeps).
 *
 * "steam" sets STEAMSTUB_RUNNING=1 (inherited by eldenring.exe): the stub steam_api64
 * then reports a running Steam.
 * Every step is logged before it runs, to stdout and ersc-load-x64.log next to the exe.
 * ersc.dll imports steam_api64.dll (only SteamAPI_GetHSteamPipe); steam_api64-stub.c
 * stands in for it.
 *
 * Build: tests/x64/build.sh ersc-load-x64
 */

#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>

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

/* ersc.dll's imports, by the bundle's file names (see "inject" above). */
static const char *const c_deps[] = {"user32.dll", "ws2_32.dll", "advapi32.dll", "crypt32.dll",
                                     "wldap32.dll", "normaliz.dll", "steam_api64.dll"};
#define N_DEPS (sizeof(c_deps) / sizeof(c_deps[0]))

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

static int load_self(const char *dll, int do_init)
{
    for (unsigned i = 0; i < N_DEPS; i++) {
        HMODULE d = LoadLibraryA(c_deps[i]);
        say("[ersc-load] preload %s: %p (error %lu)", c_deps[i], (void *)d, d ? 0 : GetLastError());
    }
    say("[ersc-load] pid %lu, LoadLibraryA(\"%s\") ...", GetCurrentProcessId(), dll);
    HMODULE h = LoadLibraryA(dll);
    DWORD err = GetLastError();
    if (!h) {
        say("[ersc-load] LoadLibraryA failed: error %lu", err);
        return 1;
    }
    say("[ersc-load] loaded at %p", (void *)h);

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

/* LoadLibraryA(path) in the child on a new remote thread; returns the thread's exit code
 * (the low 32 bits of the HMODULE, 0 on failure), or -1 if the thread did not finish. */
static DWORD remote_load(HANDLE proc, const char *path, DWORD timeout_ms)
{
    SIZE_T len = strlen(path) + 1;
    void *mem = VirtualAllocEx(proc, NULL, len, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!mem || !WriteProcessMemory(proc, mem, path, len, NULL)) {
        say("[ersc-load] remote %s: VirtualAllocEx/WriteProcessMemory failed: error %lu", path, GetLastError());
        return 0;
    }
    FARPROC lla = GetProcAddress(GetModuleHandleA("kernel32.dll"), "LoadLibraryA");
    DWORD tid = 0;
    say("[ersc-load] remote LoadLibraryA(\"%s\") ...", path);
    HANDLE t = CreateRemoteThread(proc, NULL, 0, (LPTHREAD_START_ROUTINE)lla, mem, 0, &tid);
    if (!t) {
        say("[ersc-load] CreateRemoteThread failed: error %lu", GetLastError());
        return 0;
    }
    DWORD w = WaitForSingleObject(t, timeout_ms), code = (DWORD)-1;
    if (w == WAIT_OBJECT_0) GetExitCodeThread(t, &code);
    say("[ersc-load] remote LoadLibraryA(\"%s\") on thread %04lx: %s, exit code %08lx",
        path, tid, w == WAIT_OBJECT_0 ? "finished" : "still running", code);
    CloseHandle(t);
    return w == WAIT_OBJECT_0 ? code : (DWORD)-1;
}

static int g_resume, g_wait_ms = 20000;

static int inject(const char *dir)
{
    char exe[MAX_PATH], cmd[MAX_PATH + 16], dll[MAX_PATH];
    snprintf(exe, sizeof(exe), "%s\\eldenring.exe", dir);
    snprintf(cmd, sizeof(cmd), "\"%s\" child", exe);
    snprintf(dll, sizeof(dll), "%s\\ersc.dll", dir);
    SetEnvironmentVariableA("SteamAppId", "1245620");
    SetEnvironmentVariableA("SteamGameId", "1245620");

    STARTUPINFOA si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    say("[ersc-load] CreateProcess(%s, suspended) ...", cmd);
    if (!CreateProcessA(exe, cmd, NULL, NULL, FALSE, CREATE_SUSPENDED, NULL, dir, &si, &pi)) {
        say("[ersc-load] CreateProcess failed: error %lu", GetLastError());
        return 1;
    }
    say("[ersc-load] eldenring.exe pid %lu, main thread %04lx (suspended)", pi.dwProcessId, pi.dwThreadId);

    for (unsigned i = 0; i < N_DEPS; i++) remote_load(pi.hProcess, c_deps[i], 60000);
    DWORD r = remote_load(pi.hProcess, dll, 240000);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    say("[ersc-load] ersc.dll remote load %s; eldenring.exe %s (exit code %08lx)",
        r == (DWORD)-1 ? "did not finish" : r ? "succeeded" : "failed",
        code == STILL_ACTIVE ? "still running" : "ended", code);

    if (code == STILL_ACTIVE && g_resume) {
        say("[ersc-load] ResumeThread(eldenring.exe main thread %04lx), as ersc_launcher does ...", pi.dwThreadId);
        say("[ersc-load] ResumeThread -> %ld", (long)ResumeThread(pi.hThread));
    }
    if (code == STILL_ACTIVE) {
        say("[ersc-load] waiting %d s for ersc.dll's threads ...", g_wait_ms / 1000);
        if (WaitForSingleObject(pi.hProcess, g_wait_ms) == WAIT_OBJECT_0) {
            GetExitCodeProcess(pi.hProcess, &code);
            say("[ersc-load] eldenring.exe ended (exit code %08lx)", code);
        } else {
            say("[ersc-load] eldenring.exe still alive, ending it");
            TerminateProcess(pi.hProcess, 0);
        }
    }
    return 0;
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "self";
    if (!strcmp(mode, "child")) { Sleep(INFINITE); return 0; }

    char path[MAX_PATH], dir[MAX_PATH];
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    if (n > 4 && n < MAX_PATH) {
        memcpy(dir, path, n + 1);
        char *s = strrchr(dir, '\\');
        if (s) *s = 0;
        memcpy(path + n - 4, ".log", 5);
        g_fh = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, 0, NULL);
    } else {
        strcpy(dir, ".");
    }
    memcpy(g_vtbl, c_stubs, sizeof(g_vtbl));
    int noinit = 0;
    for (int i = 2; i < argc; i++) {
        if (!strcmp(argv[i], "steam")) SetEnvironmentVariableA("STEAMSTUB_RUNNING", "1");
        if (!strcmp(argv[i], "noinit")) noinit = 1;
        if (!strcmp(argv[i], "resume")) g_resume = 1;
        if (!strncmp(argv[i], "wait=", 5)) g_wait_ms = atoi(argv[i] + 5) * 1000;
    }
    say("[ersc-load] mode %s, dir %s%s", mode, dir, GetEnvironmentVariableA("STEAMSTUB_RUNNING", NULL, 0) ? ", Steam stub running" : "");

    if (!strcmp(mode, "inject")) return inject(dir);
    return load_self("ersc.dll", !noinit);
}
