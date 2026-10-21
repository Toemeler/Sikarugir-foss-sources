/* sehctx-x64.c — what a frame-based exception handler sees besides the context.
 *
 * Seamless Co-op's ersc.dll (Themida) gets its trap-flag SINGLE_STEP in a frame
 * handler named by its unwind info. Under Madeira build 79 that handler returns
 * 0xffffffff (no EXCEPTION_DISPOSITION) without touching the context, so ntdll
 * raises STATUS_INVALID_DISPOSITION, which reaches the same handler again until
 * the stack overflows; the context it gets matches Windows 10 in every field
 * tfstep-x64 checks. So it reacts to something else it can see. This test logs
 * all of that from a frame handler of its own on the same kind of single step:
 *
 *   - its arguments and where they lie relative to its stack pointer;
 *   - every DISPATCHER_CONTEXT field, and the unwind context it points to;
 *   - its return address and the module it is in (on Windows, ntdll's
 *     RtlpExecuteHandlerForException), and a backtrace of its callers;
 *   - the stack pointer's alignment and the TEB stack limits around it;
 *   - EFlags, MXCSR and the x87 control word at entry.
 *
 * Cases: "frame" (only the frame handler), "veh+vch" (also a vectored exception
 * handler and a vectored continue handler: the order Windows calls them in), and
 * "minus1" last: the frame handler returns 0xffffffff once, as ersc.dll's does,
 * and logs what Windows does next (then the process ends from the unhandled
 * exception filter). Run it on a real x64 Windows PC and under Madeira; every
 * line also goes to sehctx-x64.log next to the exe.
 *
 * Build: x86_64-w64-mingw32-clang -O2 -o sehctx-x64.exe sehctx-x64.c
 */

#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>

/* +00 pushfq  +01 or qword ptr [rsp],100h  +09 popfq  +0a nop  +0b nop  +0c nop  +0d ret */
static const unsigned char c_step[] = {0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d,
                                       0x90, 0x90, 0x90, 0xc3};

#define UNWIND_OFF 0x800
#define TRAMP_OFF  0x820
#define TF 0x100u
#ifndef UNW_FLAG_EHANDLER
#define UNW_FLAG_EHANDLER 1
#endif

enum { MODE_NORMAL, MODE_MINUS1 };

/* Written by seh_entry before any C code runs (global: the asm names them). */
ULONG64 g_entry_ret, g_entry_rsp, g_entry_rfl;
DWORD g_entry_mxcsr;
WORD g_entry_fpcw;

static unsigned char *g_buf;
static HANDLE g_fh = INVALID_HANDLE_VALUE;
static RUNTIME_FUNCTION g_rf;
static volatile int g_mode, g_calls;
static char g_seq[256];

typedef struct {
    int valid;
    ULONG64 ret, rsp, rfl;
    DWORD mxcsr;
    WORD fpcw;
    DWORD code, flags, nparams;
    ULONG64 addr;
    ULONG64 rec, frame, ctx, dc;
    ULONG64 ctx_rip, ctx_rsp;
    DWORD ctx_flags, ctx_efl;
    DISPATCHER_CONTEXT d;
    ULONG64 uw_rip, uw_rsp;
    ULONG64 stack_base, stack_limit;
    USHORT nbt;
    void *bt[16];
} snap_t;

static snap_t g_snap;

static void lg(const char *fmt, ...)
{
    char buf[1024];
    DWORD w;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n >= (int)sizeof(buf)) n = sizeof(buf) - 1;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), buf, (DWORD)n, &w, NULL);
    if (g_fh != INVALID_HANDLE_VALUE) { WriteFile(g_fh, buf, (DWORD)n, &w, NULL); FlushFileBuffers(g_fh); }
}

static void seq(const char *s)
{
    size_t n = strlen(g_seq);
    if (n + strlen(s) + 2 < sizeof(g_seq)) { strcat(g_seq, n ? " " : ""); strcat(g_seq, s); }
}

/* "module+offset", or what VirtualQuery says about an address no module covers. */
static const char *where(ULONG64 a, char *out, size_t len)
{
    HMODULE m = NULL;
    char path[MAX_PATH];
    MEMORY_BASIC_INFORMATION mbi;

    if (!a) { snprintf(out, len, "0"); return out; }
    if (a >= (ULONG64)g_buf && a < (ULONG64)g_buf + 4096) {
        snprintf(out, len, "buf+%llx", a - (ULONG64)g_buf);
        return out;
    }
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (const char *)a, &m) && m && GetModuleFileNameA(m, path, MAX_PATH)) {
        const char *b = strrchr(path, '\\');
        snprintf(out, len, "%s+%llx", b ? b + 1 : path, a - (ULONG64)m);
        return out;
    }
    if (VirtualQuery((void *)a, &mbi, sizeof(mbi)))
        snprintf(out, len, "%llx(no module: base=%p type=%lx prot=%lx)", a, mbi.AllocationBase,
                 mbi.Type, mbi.Protect);
    else
        snprintf(out, len, "%llx(not mapped)", a);
    return out;
}

/* The handler the unwind info names: records the stack pointer, the return address,
 * EFlags, MXCSR and the x87 control word before any C code changes them. */
EXCEPTION_DISPOSITION seh_handler(EXCEPTION_RECORD *r, void *frame, CONTEXT *c, DISPATCHER_CONTEXT *dc);
void seh_entry(void);
__asm__(".globl seh_entry\n"
        "seh_entry:\n\t"
        "movq (%rsp), %rax\n\t"
        "movq %rax, g_entry_ret(%rip)\n\t"
        "movq %rsp, g_entry_rsp(%rip)\n\t"
        "pushfq\n\t"
        "popq %rax\n\t"
        "movq %rax, g_entry_rfl(%rip)\n\t"
        "stmxcsr g_entry_mxcsr(%rip)\n\t"
        "fnstcw g_entry_fpcw(%rip)\n\t"
        "jmp seh_handler\n");

EXCEPTION_DISPOSITION seh_handler(EXCEPTION_RECORD *r, void *frame, CONTEXT *c, DISPATCHER_CONTEXT *dc)
{
    char b[32];
    int n = ++g_calls;

    snprintf(b, sizeof(b), "frame:%lx/%lx", r->ExceptionCode, r->ExceptionFlags);
    seq(b);
    if (r->ExceptionFlags & (EXCEPTION_UNWINDING | EXCEPTION_EXIT_UNWIND)) return ExceptionContinueSearch;
    if (n > 6) return ExceptionContinueSearch;

    if (!g_snap.valid) {
        snap_t *s = &g_snap;
        NT_TIB *tib = (NT_TIB *)NtCurrentTeb();
        s->valid = 1;
        s->ret = g_entry_ret; s->rsp = g_entry_rsp; s->rfl = g_entry_rfl;
        s->mxcsr = g_entry_mxcsr; s->fpcw = g_entry_fpcw;
        s->code = r->ExceptionCode; s->flags = r->ExceptionFlags; s->nparams = r->NumberParameters;
        s->addr = (ULONG64)r->ExceptionAddress;
        s->rec = (ULONG64)r; s->frame = (ULONG64)frame; s->ctx = (ULONG64)c; s->dc = (ULONG64)dc;
        s->ctx_rip = c->Rip; s->ctx_rsp = c->Rsp; s->ctx_flags = c->ContextFlags; s->ctx_efl = c->EFlags;
        s->d = *dc;
        if (dc->ContextRecord) { s->uw_rip = dc->ContextRecord->Rip; s->uw_rsp = dc->ContextRecord->Rsp; }
        s->stack_base = (ULONG64)tib->StackBase; s->stack_limit = (ULONG64)tib->StackLimit;
        s->nbt = RtlCaptureStackBackTrace(0, 16, s->bt, NULL);
    }

    if (r->ExceptionCode != EXCEPTION_SINGLE_STEP) return ExceptionContinueSearch;
    c->EFlags &= ~TF;
    if (g_mode == MODE_MINUS1 && n == 1) return (EXCEPTION_DISPOSITION)0xffffffff;
    return ExceptionContinueExecution;
}

static LONG WINAPI veh(EXCEPTION_POINTERS *ep)
{
    char b[32];
    snprintf(b, sizeof(b), "veh:%lx", ep->ExceptionRecord->ExceptionCode);
    seq(b);
    return EXCEPTION_CONTINUE_SEARCH;
}

static LONG WINAPI vch(EXCEPTION_POINTERS *ep)
{
    char b[32];
    snprintf(b, sizeof(b), "vch:%lx", ep->ExceptionRecord->ExceptionCode);
    seq(b);
    return EXCEPTION_CONTINUE_SEARCH;
}

static void print_snap(const char *name)
{
    const snap_t *s = &g_snap;
    char w1[160], w2[160], w3[160], w4[160];

    lg("[%s] order: %s\n", name, g_seq[0] ? g_seq : "(no handler ran)");
    if (!s->valid) { lg("[%s] the frame handler never ran\n", name); return; }
    lg("[%s] exception code=%08lx flags=%lx nparams=%lu addr=%s\n", name, s->code, s->flags, s->nparams,
       where(s->addr, w1, sizeof(w1)));
    lg("[%s] entry rsp&15=%llx rfl=%08llx mxcsr=%08lx fpcw=%04x ret=%s\n", name, s->rsp & 15, s->rfl,
       s->mxcsr, s->fpcw, where(s->ret, w1, sizeof(w1)));
    lg("[%s] stack base-rsp=%llx rsp-limit=%llx (rsp inside: %d)\n", name, s->stack_base - s->rsp,
       s->rsp - s->stack_limit, s->rsp > s->stack_limit && s->rsp < s->stack_base);
    lg("[%s] args (offset from entry rsp): rec=%+lld frame=%+lld ctx=%+lld dc=%+lld; frame-ctx.Rsp=%+lld\n", name,
       (LONG64)(s->rec - s->rsp), (LONG64)(s->frame - s->rsp), (LONG64)(s->ctx - s->rsp), (LONG64)(s->dc - s->rsp),
       (LONG64)(s->frame - s->ctx_rsp));
    lg("[%s] ctx rip=%s rsp-entry=%+lld flags=%08lx efl=%08lx\n", name, where(s->ctx_rip, w1, sizeof(w1)),
       (LONG64)(s->ctx_rsp - s->rsp), s->ctx_flags, s->ctx_efl);
    lg("[%s] dc ControlPc=%s ImageBase=%s FunctionEntry=%s (table entry: %d) EstablisherFrame-frame=%+lld\n", name,
       where(s->d.ControlPc, w1, sizeof(w1)), where(s->d.ImageBase, w2, sizeof(w2)),
       where((ULONG64)s->d.FunctionEntry, w3, sizeof(w3)), s->d.FunctionEntry == &g_rf,
       (LONG64)(s->d.EstablisherFrame - s->frame));
    lg("[%s] dc TargetIp=%llx ContextRecord-ctx=%+lld LanguageHandler=%s HandlerData=%s\n", name, s->d.TargetIp,
       (LONG64)((ULONG64)s->d.ContextRecord - s->ctx), where((ULONG64)s->d.LanguageHandler, w1, sizeof(w1)),
       where((ULONG64)s->d.HandlerData, w2, sizeof(w2)));
    lg("[%s] dc HistoryTable=%s ScopeIndex=%lu Fill0=%lu; unwind ctx rip=%s rsp-ctx.Rsp=%+lld\n", name,
       s->d.HistoryTable ? "set" : "null", s->d.ScopeIndex, s->d.Fill0, where(s->uw_rip, w4, sizeof(w4)),
       (LONG64)(s->uw_rsp - s->ctx_rsp));
    for (int i = 0; i < s->nbt; i++) lg("[%s]   bt#%d %s\n", name, i, where((ULONG64)s->bt[i], w1, sizeof(w1)));
}

static void add_seh_table(void)
{
    unsigned char *u = g_buf + UNWIND_OFF, *t = g_buf + TRAMP_OFF;
    ULONG64 target = (ULONG64)seh_entry;
    DWORD rva = TRAMP_OFF;

    u[0] = 1 | (UNW_FLAG_EHANDLER << 3);    /* version 1 */
    u[1] = 0;                               /* SizeOfProlog */
    u[2] = 0;                               /* CountOfCodes */
    u[3] = 0;                               /* no frame register */
    memcpy(u + 4, &rva, 4);                 /* ExceptionHandler */
    memset(u + 8, 0x5a, 8);                 /* language-specific data (HandlerData points here) */
    t[0] = 0xff; t[1] = 0x25;               /* jmp qword ptr [rip+0] */
    memset(t + 2, 0, 4);
    memcpy(t + 6, &target, 8);
    g_rf.BeginAddress = 0;
    g_rf.EndAddress = sizeof(c_step);
    g_rf.UnwindData = UNWIND_OFF;
}

static void run_case(const char *name, int mode, int vectored)
{
    void *v1 = NULL, *v2 = NULL;

    memset(&g_snap, 0, sizeof(g_snap));
    g_seq[0] = 0;
    g_calls = 0;
    g_mode = mode;
    if (vectored) { v1 = AddVectoredExceptionHandler(1, veh); v2 = AddVectoredContinueHandler(1, vch); }
    lg("[%s] running\n", name);
    ((void (*)(void))g_buf)();
    if (v1) RemoveVectoredExceptionHandler(v1);
    if (v2) RemoveVectoredContinueHandler(v2);
    print_snap(name);
}

/* minus1 ends here on Windows if 0xffffffff is no disposition there either. */
static LONG WINAPI last_chance(EXCEPTION_POINTERS *ep)
{
    char b[32];
    snprintf(b, sizeof(b), "unhandled:%lx/%lx", ep->ExceptionRecord->ExceptionCode, ep->ExceptionRecord->ExceptionFlags);
    seq(b);
    print_snap("minus1");
    lg("[minus1] the process ends from the unhandled exception filter\n");
    if (g_fh != INVALID_HANDLE_VALUE) CloseHandle(g_fh);
    ExitProcess(3);
}

int main(void)
{
    char path[MAX_PATH];
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    if (n > 4 && n < MAX_PATH) {
        memcpy(path + n - 4, ".log", 5);
        g_fh = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    }
    lg("sehctx-x64: what a frame handler sees on a trap-flag single step\n");
    g_buf = VirtualAlloc(NULL, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!g_buf) { lg("SETUP FAILED: VirtualAlloc %lu\n", GetLastError()); return 2; }
    memset(g_buf, 0xcc, 4096);
    memcpy(g_buf, c_step, sizeof(c_step));
    add_seh_table();
    FlushInstructionCache(GetCurrentProcess(), g_buf, 4096);
    if (!RtlAddFunctionTable(&g_rf, 1, (DWORD64)g_buf)) { lg("SETUP FAILED: RtlAddFunctionTable\n"); return 2; }
    lg("buf=%p handler=%s\n", (void *)g_buf, "buf+820 -> seh_entry");

    run_case("frame", MODE_NORMAL, 0);
    run_case("veh+vch", MODE_NORMAL, 1);
    SetUnhandledExceptionFilter(last_chance);
    run_case("minus1", MODE_MINUS1, 0);
    lg("[minus1] returned to main: 0xffffffff was taken as a disposition\n");

    if (g_fh != INVALID_HANDLE_VALUE) CloseHandle(g_fh);
    return 0;
}
