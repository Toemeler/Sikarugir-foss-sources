/* txm_shim.c - make the Simulator refuse execute permission the way an iPad does.
 *
 * On a device TXM lets code run only from the debugger-granted JIT pool: a
 * mprotect/mmap/vm_protect asking for EXECUTE anywhere else does not get it. The
 * Simulator runs on the macOS kernel, which grants it. Wine's unix side checks
 * for exactly that (virtual_ios.c mprotect_exec: "Try normal mprotect first
 * (works on non-TXM devices)"), so in the Simulator every PE image would run in
 * place, unpatched, instead of from its pool copy with the x18 patcher applied --
 * a different program from the one the device runs.
 *
 * Everything is linked statically into the harness executable, so these
 * definitions take the place of libSystem's for all of it; the real calls are
 * reached through dlsym(RTLD_NEXT). Inside the pool (harness_main.m sets the
 * range before it makes the pool RX) nothing changes. Everywhere else EXECUTE is
 * dropped from the request and the call goes ahead; a vm_remap that would carry
 * EXECUTE out of the pool loses it, as on a device ("Remapping those pages
 * elsewhere doesn't preserve execute permission", virtual_ios.c).
 * SIM_TXM=0 turns it off. */
#include <dlfcn.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

extern kern_return_t mach_vm_protect(vm_map_t, mach_vm_address_t, mach_vm_size_t, boolean_t, vm_prot_t);
extern kern_return_t mach_vm_map(vm_map_t, mach_vm_address_t *, mach_vm_size_t, mach_vm_offset_t, int,
                                 mem_entry_name_port_t, memory_object_offset_t, boolean_t,
                                 vm_prot_t, vm_prot_t, vm_inherit_t);

uintptr_t sim_txm_pool_lo, sim_txm_pool_hi;
static int g_off = -1;
static unsigned long g_dropped;

static int txm_on(void)
{
    if (g_off < 0) { const char *v = getenv("SIM_TXM"); g_off = v && v[0] == '0'; }
    return !g_off;
}

static int in_pool(uintptr_t a, size_t n)
{
    return sim_txm_pool_hi && a >= sim_txm_pool_lo && a + n <= sim_txm_pool_hi && a + n >= a;
}

static void note(const char *call, uintptr_t a, size_t n)
{
    unsigned long k = __atomic_add_fetch(&g_dropped, 1, __ATOMIC_RELAXED);
    if (k <= 8 || (k & (k - 1)) == 0)
        dprintf(2, "[sim-txm] #%lu %s %#lx+%#zx: EXECUTE refused outside the JIT pool (as TXM)\n",
                k, call, (unsigned long)a, n);
}

#define REAL(ret, name, ...) \
    static ret (*real)(__VA_ARGS__); \
    if (!real) real = (ret (*)(__VA_ARGS__))dlsym(RTLD_NEXT, name)

int mprotect(void *addr, size_t len, int prot)
{
    REAL(int, "mprotect", void *, size_t, int);
    if ((prot & PROT_EXEC) && txm_on() && !in_pool((uintptr_t)addr, len)) {
        note("mprotect", (uintptr_t)addr, len);
        prot &= ~PROT_EXEC;
    }
    return real(addr, len, prot);
}

void *mmap(void *addr, size_t len, int prot, int flags, int fd, off_t off)
{
    REAL(void *, "mmap", void *, size_t, int, int, int, off_t);
    if ((prot & PROT_EXEC) && txm_on() && !((flags & MAP_FIXED) && in_pool((uintptr_t)addr, len))) {
        note("mmap", (uintptr_t)addr, len);
        prot &= ~PROT_EXEC;
    }
    return real(addr, len, prot, flags, fd, off);
}

kern_return_t vm_protect(vm_map_t task, vm_address_t addr, vm_size_t size, boolean_t set_max, vm_prot_t prot)
{
    REAL(kern_return_t, "vm_protect", vm_map_t, vm_address_t, vm_size_t, boolean_t, vm_prot_t);
    if (!set_max && (prot & VM_PROT_EXECUTE) && txm_on() && task == mach_task_self() && !in_pool(addr, size)) {
        note("vm_protect", addr, size);
        prot &= ~VM_PROT_EXECUTE;
    }
    return real(task, addr, size, set_max, prot);
}

kern_return_t mach_vm_protect(vm_map_t task, mach_vm_address_t addr, mach_vm_size_t size, boolean_t set_max,
                              vm_prot_t prot)
{
    REAL(kern_return_t, "mach_vm_protect", vm_map_t, mach_vm_address_t, mach_vm_size_t, boolean_t, vm_prot_t);
    if (!set_max && (prot & VM_PROT_EXECUTE) && txm_on() && task == mach_task_self() && !in_pool(addr, size)) {
        note("mach_vm_protect", addr, size);
        prot &= ~VM_PROT_EXECUTE;
    }
    return real(task, addr, size, set_max, prot);
}

kern_return_t vm_map(vm_map_t task, vm_address_t *addr, vm_size_t size, vm_address_t mask, int flags,
                     mem_entry_name_port_t obj, vm_offset_t off, boolean_t copy, vm_prot_t cur,
                     vm_prot_t max, vm_inherit_t inh)
{
    REAL(kern_return_t, "vm_map", vm_map_t, vm_address_t *, vm_size_t, vm_address_t, int,
         mem_entry_name_port_t, vm_offset_t, boolean_t, vm_prot_t, vm_prot_t, vm_inherit_t);
    if ((cur & VM_PROT_EXECUTE) && txm_on() && task == mach_task_self()
        && !(!(flags & VM_FLAGS_ANYWHERE) && addr && in_pool(*addr, size))) {
        note("vm_map", addr ? *addr : 0, size);
        cur &= ~VM_PROT_EXECUTE;
    }
    return real(task, addr, size, mask, flags, obj, off, copy, cur, max, inh);
}

kern_return_t mach_vm_map(vm_map_t task, mach_vm_address_t *addr, mach_vm_size_t size, mach_vm_offset_t mask,
                          int flags, mem_entry_name_port_t obj, memory_object_offset_t off, boolean_t copy,
                          vm_prot_t cur, vm_prot_t max, vm_inherit_t inh)
{
    REAL(kern_return_t, "mach_vm_map", vm_map_t, mach_vm_address_t *, mach_vm_size_t, mach_vm_offset_t, int,
         mem_entry_name_port_t, memory_object_offset_t, boolean_t, vm_prot_t, vm_prot_t, vm_inherit_t);
    if ((cur & VM_PROT_EXECUTE) && txm_on() && task == mach_task_self()
        && !(!(flags & VM_FLAGS_ANYWHERE) && addr && in_pool(*addr, size))) {
        note("mach_vm_map", addr ? *addr : 0, size);
        cur &= ~VM_PROT_EXECUTE;
    }
    return real(task, addr, size, mask, flags, obj, off, copy, cur, max, inh);
}

kern_return_t vm_remap(vm_map_t task, vm_address_t *target, vm_size_t size, vm_address_t mask, int flags,
                       vm_map_t src_task, vm_address_t src, boolean_t copy, vm_prot_t *cur, vm_prot_t *max,
                       vm_inherit_t inh)
{
    REAL(kern_return_t, "vm_remap", vm_map_t, vm_address_t *, vm_size_t, vm_address_t, int, vm_map_t,
         vm_address_t, boolean_t, vm_prot_t *, vm_prot_t *, vm_inherit_t);
    kern_return_t kr = real(task, target, size, mask, flags, src_task, src, copy, cur, max, inh);
    if (kr == KERN_SUCCESS && cur && (*cur & VM_PROT_EXECUTE) && txm_on() && task == mach_task_self()
        && !in_pool(*target, size)) {
        static kern_return_t (*prot)(vm_map_t, vm_address_t, vm_size_t, boolean_t, vm_prot_t);
        if (!prot) prot = (kern_return_t (*)(vm_map_t, vm_address_t, vm_size_t, boolean_t, vm_prot_t))
                          dlsym(RTLD_NEXT, "vm_protect");
        note("vm_remap", *target, size);
        *cur &= ~VM_PROT_EXECUTE;
        prot(task, *target, size, FALSE, *cur);
    }
    return kr;
}

unsigned long sim_txm_dropped(void) { return g_dropped; }
