#!/usr/bin/env python3
"""Compile the runtime's active-root resource collection with host stand-ins.

An overwritten root may refer to a resource already freed before replay. No
recorded resource pointer may be visited for residency. Exercise live VA reuse,
separate graphics/compute roots, read/write aliases, null/unmapped values and
all 64 parameters. No Metal device or iOS process is needed.
"""
from pathlib import Path
import argparse, os, shutil, subprocess, tempfile

p = argparse.ArgumentParser()
p.add_argument('--compiler')
p.add_argument('--sanitize', action='store_true')
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
s = (root/'madeira-d3d12/src/pe/madeira_d3d12.c').read_text(encoding='utf-8')
assert 'mad_list_note_used' not in s and 'e->l->used' not in s
assert 'mad_root_resources(dev, e->rs, e->root, roots)' in s
assert 'mad_root_resources(dev, e->crs, e->croot, roots)' in s
start = s.index('struct mad_root_use {')
end = s.index('\n}\n', start) + 3
cut = s[start:end]
head = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t UINT64, obj_handle_t;
typedef uint32_t UINT32;
#define MAD_ROOT_PARAM_MAX 64
enum { MADEIRA_IR_PARAM_TABLE, MADEIRA_IR_PARAM_CONSTANTS, MADEIRA_IR_PARAM_CBV,
       MADEIRA_IR_PARAM_SRV, MADEIRA_IR_PARAM_UAV };
enum { WMTResourceUsageRead=1, WMTResourceUsageWrite=2 };
struct mad_rootsig { unsigned nparams; struct { unsigned type; } params[64]; };
struct mad_resource { UINT64 gpu_address; obj_handle_t buffer; };
struct mad_device { struct mad_resource *live[64]; unsigned count, lookups; };
static struct mad_resource *mad_resolve_address(struct mad_device *d, UINT64 addr, UINT64 *off) {
    unsigned i; d->lookups++;
    for (i=d->count; i; i--) if (d->live[i-1]->gpu_address==addr) {
        *off=0; return d->live[i-1];
    }
    return NULL;
}
'''
tail = r'''
static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main(void) {
    struct mad_device d={0}; struct mad_rootsig rs={0};
    struct mad_root_use out[64]; struct mad_resource resources[64];
    UINT64 graphics[64]={0}, compute[64]={0}; unsigned i,n;
    /* An old binding's object dies, then its VA is reused by another buffer. */
    struct mad_resource *old=malloc(sizeof *old);
    old->gpu_address=0x1000; old->buffer=777;
    graphics[0]=old->gpu_address; free(old);
    resources[0]=(struct mad_resource){0x1000,888}; d.live[0]=&resources[0]; d.count=1;
    rs.nparams=1; rs.params[0].type=MADEIRA_IR_PARAM_CBV;
    CHECK(mad_root_resources(&d,&rs,graphics,out)==1 && out[0].buffer==888);
    /* Overwriting with an unmapped root must never consult the old pointer. */
    graphics[0]=0xdead0000;
    CHECK(mad_root_resources(&d,&rs,graphics,out)==0);
    for(i=0;i<64;i++) {
        resources[i]=(struct mad_resource){0x1000+(UINT64)i*0x1000,100+i};
        d.live[i]=&resources[i]; graphics[i]=resources[i].gpu_address;
        rs.params[i].type=MADEIRA_IR_PARAM_CBV;
    }
    d.count=64; rs.nparams=64;
    n=mad_root_resources(&d,&rs,graphics,out); CHECK(n==64);
    for(i=0;i<64;i++) CHECK(out[i].buffer==100+i && out[i].usage==WMTResourceUsageRead);
    /* Both sides of the old 32-root boundary, and the final root, survive. */
    CHECK(out[35].buffer==135 && out[63].buffer==163);
    compute[63]=graphics[5]; rs.params[63].type=MADEIRA_IR_PARAM_UAV;
    CHECK(mad_root_resources(&d,&rs,compute,out)==1 && out[0].buffer==105 && out[0].usage==3);
    /* Constants/tables are not raw resource addresses, even if their bits match. */
    memset(graphics,0,sizeof graphics); graphics[0]=graphics[1]=resources[0].gpu_address;
    rs.params[0].type=MADEIRA_IR_PARAM_TABLE; rs.params[1].type=MADEIRA_IR_PARAM_CONSTANTS;
    CHECK(mad_root_resources(&d,&rs,graphics,out)==0);
    rs.params[0].type=MADEIRA_IR_PARAM_SRV; rs.params[1].type=MADEIRA_IR_PARAM_UAV;
    CHECK(mad_root_resources(&d,&rs,graphics,out)==1 && out[0].usage==3);
    /* A texture cannot back a root buffer descriptor. */
    resources[0].buffer=0; CHECK(mad_root_resources(&d,&rs,graphics,out)==0);
    CHECK(mad_root_resources(NULL,&rs,graphics,out)==0);
    CHECK(mad_root_resources(&d,NULL,graphics,out)==0);
    CHECK(mad_root_resources(&d,&rs,NULL,out)==0);
    CHECK(mad_root_resources(&d,&rs,graphics,NULL)==0);
    /* Oversized metadata is clamped to the real 64-entry root storage. */
    resources[0].buffer=100; rs.nparams=1000;
    CHECK(mad_root_resources(&d,&rs,graphics,out)==1);
    for(i=0;i<10000;i++) {
        unsigned j=i%64; memset(graphics,0,sizeof graphics);
        rs.params[j].type=(i%2) ? MADEIRA_IR_PARAM_SRV : MADEIRA_IR_PARAM_UAV;
        graphics[j]=resources[j].gpu_address;
        CHECK(mad_root_resources(&d,&rs,graphics,out)==1);
        CHECK(out[0].buffer==resources[j].buffer);
        CHECK(out[0].usage==((i%2)?1:3));
    }
    printf("PASS: %u active-root residency checks\n",checks); return 0;
}
'''
cc = a.compiler or shutil.which('clang') or shutil.which('gcc') or shutil.which('cc')
if not cc: raise SystemExit('A C compiler is required; no runtime checks were run')
cmd = [cc] + (['cc'] if Path(cc).stem == 'zig' else [])
with tempfile.TemporaryDirectory() as tmp:
    tmp=Path(tmp); src=tmp/'root.c'; exe=tmp/('root.exe' if os.name=='nt' else 'root')
    src.write_text(head+cut+tail,encoding='utf-8')
    flags=['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if a.sanitize else []
    subprocess.run(cmd+['-std=c11','-O1','-g','-Wall','-Wextra','-Werror']+flags+[str(src),'-o',str(exe)],check=True)
    subprocess.run([str(exe)],check=True)
print('PASS: recording retains no resource-pointer history; both bind points use current roots')
