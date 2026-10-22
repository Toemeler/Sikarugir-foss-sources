/* harness_glue.c - the few app symbols the harness defines itself.
 *
 * FEXBridge.mm is not linked: its pool needs a debugger (jit_pool_init), and the
 * guest's x64 emulator is xtajit64.dll's own FEXCore, not the app's. The launcher
 * only asks it for the pool's RX->RW distance, which harness_main.m has already
 * exported as MADEIRA_JIT_WRITE_OFFSET. */
#include <stdint.h>
#include <stdlib.h>

int64_t fex_get_jit_write_offset(void)
{
    const char *v = getenv("MADEIRA_JIT_WRITE_OFFSET");
    return v ? (int64_t)strtoull(v, NULL, 0) : 0;   /* the launcher prints it as 0x%llx */
}
