/* tfstep-x64.c — how a trap-flag single step reaches the program.
 *
 * Copy protections (Themida: Seamless Co-op's ersc.dll) set EFLAGS.TF and check
 * the EXCEPTION_SINGLE_STEP that follows: which instruction it stops after, the
 * address, the EFlags and debug registers in the context, and how many times the
 * handler runs. Each case below sets TF a different way and a vectored handler
 * records every exception it causes. Run it on a real x64 Windows PC and under
 * Madeira: the lines that differ are what FEX/Wine deliver differently.
 *
 * Every case then runs a second time with no vectored handler: the case code gets
 * a function table entry (RtlAddFunctionTable) whose unwind info names a
 * frame-based handler (UNW_FLAG_EHANDLER), the way ersc.dll's .pdata names its
 * handler. RtlDispatchException has to find that entry, unwind to the frame and
 * call the x64 handler, and take its EXCEPTION_DISPOSITION back. Those lines start
 * with "[seh ". A top-level filter catches what no handler takes, so a case whose
 * frame handler is never called shows up as via=filter instead of ending the
 * program. The expected addresses are the vectored ones: the CPU stops at the same
 * place whoever handles it. Windows 10 (i7-10750H) calls the frame-based handler
 * once per exception with EstablisherFrame = the context's Rsp (frame=rsp+0), also
 * in "call" where the step stops on the callee's ret: 13/13 there as well.
 *
 * The case bytes come from ml64 (the MASM source is in each comment); they are
 * copied into an RWX buffer, so offsets below are from the start of each case.
 * "expect" is real hardware (Windows 10, measured); every line also goes to
 * tfstep-x64.log next to the exe.
 *
 * Build: ./build.sh tfstep-x64   (or MSVC: cl /O2 tfstep-x64.c)
 */

#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <intrin.h>

/* SETTF = pushfq; or qword ptr [rsp], 100h; popfq   (9c 48 81 0c 24 00 01 00 00 9d) */

/* +00 xor eax,eax  +02 SETTF (popfq at +0b)  +0c nop  +0d nop  +0e nop  +0f ret */
static const unsigned char c_popf[] = {
    0x33, 0xc0, 0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x90, 0x90, 0x90, 0xc3};
/* +00 SETTF (popfq at +09)  +0a pushfq  +0b pop rax  +0c mov [rcx],rax  +0f nop  +10 ret */
static const unsigned char c_pushf[] = {
    0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x9c, 0x58, 0x48, 0x89, 0x01, 0x90, 0xc3};
/* +00 mov ax,ss  +03 SETTF (popfq at +0c)  +0d mov ss,ax  +10 nop  +11 nop  +12 nop  +13 ret */
static const unsigned char c_movss[] = {
    0x66, 0x8c, 0xd0, 0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x66, 0x8e, 0xd0,
    0x90, 0x90, 0x90, 0xc3};
/* +00 SETTF  +0a jmp short +0d  +0c nop  +0d nop  +0e nop  +0f ret */
static const unsigned char c_jmp[] = {
    0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0xeb, 0x01, 0x90, 0x90, 0x90, 0xc3};
/* +00 SETTF  +0a call +11  +0f nop  +10 ret  +11 ret */
static const unsigned char c_call[] = {
    0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0xe8, 0x02, 0x00, 0x00, 0x00, 0x90,
    0xc3, 0xc3};
/* +00 push rbx  +01 xor eax,eax  +03 xor ecx,ecx  +05 SETTF (popfq at +0e)  +0f cpuid  +11 nop
 * +12 pop rbx  +13 nop  +14 ret */
static const unsigned char c_cpuid[] = {
    0x53, 0x33, 0xc0, 0x33, 0xc9, 0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x0f,
    0xa2, 0x90, 0x5b, 0x90, 0xc3};
/* +00 SETTF  +0a rdtsc  +0c nop  +0d nop  +0e ret */
static const unsigned char c_rdtsc[] = {
    0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x0f, 0x31, 0x90, 0x90, 0xc3};
/* The handler sets TF again after every step (a tracer):
 * +00 SETTF  +0a nop  +0b mov eax,1  +10 add eax,2  +13 pushfq  +14 popfq (TF stays set)
 * +15 jmp short +18  +17 nop  +18 lea eax,[rax+1]  +1b pushfq  +1c and dword ptr [rsp],0FFFFFEFFh
 * +23 popfq (clears TF)  +24 nop  +25 ret */
static const unsigned char c_keep[] = {
    0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x90, 0xb8, 0x01, 0x00, 0x00, 0x00,
    0x83, 0xc0, 0x02, 0x9c, 0x9d, 0xeb, 0x01, 0x90, 0x8d, 0x40, 0x01, 0x9c, 0x81, 0x24, 0x24, 0xff,
    0xfe, 0xff, 0xff, 0x9d, 0x90, 0xc3};
/* TF set from the handler's context (NtContinue), not by the program:
 * +00 int3 (handler: Rip = +01, EFlags |= TF)  +01 nop  +02 nop  +03 nop  +04 ret */
static const unsigned char c_bp[] = {0xcc, 0x90, 0x90, 0x90, 0xc3};
/* Flags across the exception: PF and AF set by arithmetic, then read back after the handler.
 * +00 mov al,2Fh  +02 add al,1 (PF, AF)  +04 SETTF (popfq at +0d)  +0e nop  +0f pushfq  +10 pop rax
 * +11 mov [rcx],rax  +14 nop  +15 ret */
static const unsigned char c_flags[] = {
    0xb0, 0x2f, 0x04, 0x01, 0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x90, 0x9c,
    0x58, 0x48, 0x89, 0x01, 0x90, 0xc3};
/* DF across the exception (Windows runs the handler with DF clear and gives DF back after).
 * +00 std  +01 SETTF (popfq at +0a)  +0b nop  +0c pushfq  +0d pop rax  +0e cld  +0f mov [rcx],rax
 * +12 nop  +13 ret */
static const unsigned char c_df[] = {
    0xfd, 0x9c, 0x48, 0x81, 0x0c, 0x24, 0x00, 0x01, 0x00, 0x00, 0x9d, 0x90, 0x9c, 0x58, 0xfc, 0x48,
    0x89, 0x01, 0x90, 0xc3};
/* +00 icebp (int1, F1)  +01 nop  +02 nop  +03 ret */
static const unsigned char c_icebp[] = {0xf1, 0x90, 0x90, 0xc3};
/* +00 int 1 (CD 01, not allowed from user mode)  +02 nop  +03 nop  +04 ret */
static const unsigned char c_int1[] = {0xcd, 0x01, 0x90, 0x90, 0xc3};

enum { M_CLEAR, M_KEEP, M_BP };

#define MAXEV 16
#define TF 0x100u
#define DF 0x400u
#define ARITH 0x8d5u        /* CF PF AF ZF SF OF */

typedef struct { DWORD code; int addr, alt; } expect_t;   /* alt: also accepted (0 = none) */

typedef struct {
    const char *name;
    const unsigned char *code;
    size_t len;
    int mode;
    int skip;               /* bytes to step over a non-single-step exception */
    unsigned ctx_mask, ctx_want;    /* context EFlags at the first exception */
    unsigned out_mask, out_want;    /* the value the case stores through rcx */
    int nexp;
    expect_t exp[MAXEV];
} case_t;

#define SS EXCEPTION_SINGLE_STEP
/* cpuid: bare metal traps at +11; under a hypervisor (Hyper-V/VBS, which runs Seamless Co-op
 * fine) CPUID is intercepted and the trap comes one instruction later, at +12. */
static const case_t CASES[] = {
    {"popf",  c_popf,  sizeof(c_popf),  M_CLEAR, 1, ARITH | TF, 0x44, 0, 0, 1, {{SS, 0x0d}}},
    {"pushf", c_pushf, sizeof(c_pushf), M_CLEAR, 1, 0, 0, TF, TF, 1, {{SS, 0x0b}}},
    {"movss", c_movss, sizeof(c_movss), M_CLEAR, 1, 0, 0, 0, 0, 1, {{SS, 0x11}}},
    {"jmp",   c_jmp,   sizeof(c_jmp),   M_CLEAR, 1, 0, 0, 0, 0, 1, {{SS, 0x0d}}},
    {"call",  c_call,  sizeof(c_call),  M_CLEAR, 1, 0, 0, 0, 0, 1, {{SS, 0x11}}},
    {"cpuid", c_cpuid, sizeof(c_cpuid), M_CLEAR, 1, 0, 0, 0, 0, 1, {{SS, 0x11, 0x12}}},
    {"rdtsc", c_rdtsc, sizeof(c_rdtsc), M_CLEAR, 1, 0, 0, 0, 0, 1, {{SS, 0x0c}}},
    {"keep",  c_keep,  sizeof(c_keep),  M_KEEP,  1, 0, 0, 0, 0, 10,
     {{SS, 0x0b}, {SS, 0x10}, {SS, 0x13}, {SS, 0x14}, {SS, 0x15},
      {SS, 0x18}, {SS, 0x1b}, {SS, 0x1c}, {SS, 0x23}, {SS, 0x24}}},
    {"flags", c_flags, sizeof(c_flags), M_CLEAR, 1, ARITH | TF, 0x14, ARITH | TF, 0x14, 1, {{SS, 0x0f}}},
    {"df",    c_df,    sizeof(c_df),    M_CLEAR, 1, DF | TF, DF, DF | TF, DF, 1, {{SS, 0x0c}}},
    {"bp",    c_bp,    sizeof(c_bp),    M_BP,    1, 0, 0, 0, 0, 2, {{EXCEPTION_BREAKPOINT, 0x00}, {SS, 0x02}}},
    {"icebp", c_icebp, sizeof(c_icebp), M_CLEAR, 1, 0, 0, 0, 0, 1, {{SS, 0x01}}},
    {"int1",  c_int1,  sizeof(c_int1),  M_CLEAR, 2, 0, 0, 0, 0, 1, {{EXCEPTION_ACCESS_VIOLATION, 0x00}}},
};
#define NCASES ((int)(sizeof(CASES) / sizeof(CASES[0])))

enum { VIA_VEH, VIA_SEH, VIA_FILTER };
static const char *const VIA_NAME[] = {"veh", "seh", "filter"};

typedef struct {
    DWORD code, flags, nparams, eflags, ctxflags;
    unsigned handler_eflags;  /* the handler's own live EFLAGS */
    int via;                /* which handler got it */
    ULONG64 info0, info1;
    LONG64 addr, rip;       /* offsets from the case start */
    LONG64 estab;           /* frame-based: EstablisherFrame - context Rsp */
    ULONG64 dr0, dr1, dr2, dr3, dr6, dr7;
} event_t;

/* Frame-based mode: the unwind info and a jump to seh_handler live in the case
 * buffer, since a handler is named by a 32-bit offset from the table's base. */
#define UNWIND_OFF 0x800
#define TRAMP_OFF  0x820
#ifndef UNW_FLAG_EHANDLER
#define UNW_FLAG_EHANDLER 1
#endif

static unsigned char *g_buf;
static const case_t *volatile g_case;
static event_t g_ev[MAXEV];
static volatile int g_n, g_outside, g_lost;
static HANDLE g_fh = INVALID_HANDLE_VALUE;
static RUNTIME_FUNCTION g_rf;

static void lg(const char *fmt, ...)
{
    char buf[512];
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

/* Records the exception and edits the context to go on. Returns 1 when the
 * program should continue there, 0 when the exception is not the case's. */
static int on_exception(EXCEPTION_RECORD *r, CONTEXT *c, unsigned live, int via, LONG64 estab)
{
    const case_t *k = g_case;
    ULONG64 base = (ULONG64)g_buf, addr = (ULONG64)r->ExceptionAddress;

    if (!k) return 0;
    int inside = c->Rip >= base && c->Rip < base + k->len;
    int addr_inside = addr >= base && addr < base + k->len;

    if (!inside && !addr_inside) {
        if (r->ExceptionCode != EXCEPTION_SINGLE_STEP) return 0;
        /* A step that escaped the case (TF left set): stop it here. */
        g_outside++;
        c->EFlags &= ~TF;
        return 1;
    }

    if (g_n < MAXEV) {
        event_t *e = &g_ev[g_n];
        e->code = r->ExceptionCode;
        e->flags = r->ExceptionFlags;
        e->nparams = r->NumberParameters;
        e->info0 = r->NumberParameters > 0 ? r->ExceptionInformation[0] : 0;
        e->info1 = r->NumberParameters > 1 ? r->ExceptionInformation[1] : 0;
        e->addr = (LONG64)(addr - base);
        e->rip = (LONG64)(c->Rip - base);
        e->eflags = c->EFlags;
        e->ctxflags = c->ContextFlags;
        e->handler_eflags = live;
        e->via = via;
        e->estab = estab;
        e->dr0 = c->Dr0; e->dr1 = c->Dr1; e->dr2 = c->Dr2; e->dr3 = c->Dr3;
        e->dr6 = c->Dr6; e->dr7 = c->Dr7;
    } else {
        g_lost++;
    }
    g_n++;

    if (r->ExceptionCode == EXCEPTION_SINGLE_STEP && inside) {
        if (k->mode == M_KEEP && c->Rip - base < k->len - 2 && g_n < MAXEV) c->EFlags |= TF;
        else c->EFlags &= ~TF;
        return 1;
    }
    if (r->ExceptionCode == EXCEPTION_BREAKPOINT && k->mode == M_BP && addr_inside) {
        c->Rip = addr + 1;
        c->EFlags |= TF;
        return 1;
    }
    /* Anything else raised by the case: step over the instruction. */
    c->Rip = (addr_inside ? addr : c->Rip) + k->skip;
    c->EFlags &= ~TF;
    return 1;
}

static LONG CALLBACK handler(EXCEPTION_POINTERS *ep)
{
    unsigned live = (unsigned)__readeflags();
    return on_exception(ep->ExceptionRecord, ep->ContextRecord, live, VIA_VEH, 0)
               ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
}

/* The frame-based handler the case's unwind info names (reached through the jump
 * at TRAMP_OFF). Its return value is what ersc.dll's handler hands back too. */
static EXCEPTION_DISPOSITION seh_handler(EXCEPTION_RECORD *r, void *frame, CONTEXT *c, DISPATCHER_CONTEXT *dc)
{
    unsigned live = (unsigned)__readeflags();
    (void)dc;
    if (r->ExceptionFlags & (EXCEPTION_UNWINDING | EXCEPTION_EXIT_UNWIND)) return ExceptionContinueSearch;
    return on_exception(r, c, live, VIA_SEH, (LONG64)((ULONG64)frame - c->Rsp))
               ? ExceptionContinueExecution : ExceptionContinueSearch;
}

/* What no handler took: the frame-based handler was skipped, or a step escaped. */
static LONG WINAPI last_chance(EXCEPTION_POINTERS *ep)
{
    unsigned live = (unsigned)__readeflags();
    return on_exception(ep->ExceptionRecord, ep->ContextRecord, live, VIA_FILTER, 0)
               ? EXCEPTION_CONTINUE_EXECUTION : EXCEPTION_CONTINUE_SEARCH;
}

/* Unwind info for the case code: no prolog, no saved registers, a handler. */
static void add_seh_table(const case_t *k)
{
    unsigned char *u = g_buf + UNWIND_OFF, *t = g_buf + TRAMP_OFF;
    ULONG64 target = (ULONG64)seh_handler;
    DWORD rva = TRAMP_OFF;

    u[0] = 1 | (UNW_FLAG_EHANDLER << 3);    /* version 1 */
    u[1] = 0;                               /* SizeOfProlog */
    u[2] = 0;                               /* CountOfCodes */
    u[3] = 0;                               /* no frame register */
    memcpy(u + 4, &rva, 4);                 /* ExceptionHandler */
    memset(u + 8, 0, 8);                    /* language-specific data */
    t[0] = 0xff; t[1] = 0x25;               /* jmp qword ptr [rip+0] */
    memset(t + 2, 0, 4);
    memcpy(t + 6, &target, 8);
    FlushInstructionCache(GetCurrentProcess(), t, 14);

    g_rf.BeginAddress = 0;
    g_rf.EndAddress = (DWORD)k->len;
    g_rf.UnwindData = UNWIND_OFF;
}

static const char *code_name(DWORD code)
{
    switch (code) {
    case EXCEPTION_SINGLE_STEP: return "SINGLE_STEP";
    case EXCEPTION_BREAKPOINT: return "BREAKPOINT";
    case EXCEPTION_ACCESS_VIOLATION: return "ACCESS_VIOLATION";
    case EXCEPTION_ILLEGAL_INSTRUCTION: return "ILLEGAL_INSTRUCTION";
    case EXCEPTION_PRIV_INSTRUCTION: return "PRIV_INSTRUCTION";
    default: return "?";
    }
}

/* Runs every case once; via is VIA_VEH (vectored handler) or VIA_SEH (frame-based). */
static int run_cases(int via)
{
    const char *pre = via == VIA_SEH ? "seh " : "";
    int matched = 0;

    for (int i = 0; i < NCASES; i++) {
        const case_t *k = &CASES[i];
        ULONG64 out = 0;

        lg("[%s%s] running\n", pre, k->name);
        memset(g_buf, 0xcc, 4096);
        memcpy(g_buf, k->code, k->len);
        FlushInstructionCache(GetCurrentProcess(), g_buf, k->len);
        if (via == VIA_SEH) {
            add_seh_table(k);
            if (!RtlAddFunctionTable(&g_rf, 1, (DWORD64)g_buf)) {
                lg("[%s%s] SETUP FAILED: RtlAddFunctionTable\n", pre, k->name);
                continue;
            }
        }
        memset(g_ev, 0, sizeof(g_ev));
        g_n = g_outside = g_lost = 0;
        g_case = k;
        ((void (*)(ULONG64 *))g_buf)(&out);
        g_case = NULL;
        if (via == VIA_SEH) RtlDeleteFunctionTable(&g_rf);

        int ok = g_n == k->nexp && !g_outside;
        for (int j = 0; ok && j < k->nexp; j++)
            ok = g_ev[j].code == k->exp[j].code &&
                 (g_ev[j].addr == k->exp[j].addr || (k->exp[j].alt && g_ev[j].addr == k->exp[j].alt));
        int ctx_ok = !k->ctx_mask || (g_n > 0 && (g_ev[0].eflags & k->ctx_mask) == k->ctx_want);
        int out_ok = !k->out_mask || (out & k->out_mask) == k->out_want;
        int live_ok = 1, via_ok = 1;
        for (int j = 0; j < g_n && j < MAXEV; j++) {
            if (g_ev[j].handler_eflags & (TF | DF)) live_ok = 0;
            if (g_ev[j].via != via) via_ok = 0;
        }
        ok = ok && ctx_ok && out_ok && live_ok && via_ok;
        matched += ok;

        lg("[%s%s] %s: %d exception(s), %d after the case, expect", pre, k->name, ok ? "MATCH" : "DIFF", g_n, g_outside);
        for (int j = 0; j < k->nexp; j++) {
            lg(" %s@+%02x", code_name(k->exp[j].code), k->exp[j].addr);
            if (k->exp[j].alt) lg("|+%02x", k->exp[j].alt);
        }
        lg("\n");
        if (!ctx_ok)
            lg("  context EFlags %08lx: bits %03x should be %03x\n", g_n ? g_ev[0].eflags : 0, k->ctx_mask, k->ctx_want);
        if (!out_ok) lg("  read back %llx: bits %03x should be %03x\n", out, k->out_mask, k->out_want);
        if (!live_ok) lg("  the handler ran with TF or DF set (Windows clears both for it)\n");
        if (!via_ok) lg("  not every exception reached the %s handler\n", VIA_NAME[via]);
        for (int j = 0; j < g_n && j < MAXEV; j++) {
            const event_t *e = &g_ev[j];
            lg("  #%d %08lx %s addr=+%02llx rip=+%02llx eflags=%08lx flags=%lx params=%lu [%llx %llx] "
               "ctxflags=%08lx dr0-3=%llx/%llx/%llx/%llx dr6=%llx dr7=%llx handler-eflags=%08x via=%s",
               j, e->code, code_name(e->code), e->addr, e->rip, e->eflags, e->flags, e->nparams,
               e->info0, e->info1, e->ctxflags, e->dr0, e->dr1, e->dr2, e->dr3, e->dr6, e->dr7,
               e->handler_eflags, VIA_NAME[e->via]);
            if (e->via == VIA_SEH) lg(" frame=rsp%+lld", e->estab);
            lg("\n");
        }
        if (g_lost) lg("  (%d more not recorded)\n", g_lost);
        if (k->out_mask) lg("  read back %llx\n", out);
    }
    return matched;
}

int main(void)
{
    char path[MAX_PATH];
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    if (n > 4 && n < MAX_PATH) {
        memcpy(path + n - 4, ".log", 5);
        g_fh = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    }

    lg("tfstep-x64: trap-flag single-step delivery (%d cases, vectored then frame-based)\n", NCASES);
    g_buf = VirtualAlloc(NULL, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    void *veh = g_buf ? AddVectoredExceptionHandler(1, handler) : NULL;
    if (!veh) {
        lg("SETUP FAILED: buffer %p, error %lu\n", (void *)g_buf, GetLastError());
        return 2;
    }
    SetUnhandledExceptionFilter(last_chance);

    int matched = run_cases(VIA_VEH);
    lg("tfstep-x64: %d/%d cases match x86 hardware\n", matched, NCASES);

    RemoveVectoredExceptionHandler(veh);
    int seh_matched = run_cases(VIA_SEH);
    lg("tfstep-x64: seh %d/%d cases match x86 hardware (frame-based handler)\n", seh_matched, NCASES);

    if (g_fh != INVALID_HANDLE_VALUE) CloseHandle(g_fh);
    return matched == NCASES && seh_matched == NCASES ? 0 : 1;
}
