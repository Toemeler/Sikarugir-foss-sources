#!/usr/bin/env python3
"""Build madeira-oodle's decoder (ooz kraken.cpp + ooz_compat.h) for this host and
decode every stream in tests/host/data/oodle, made by the game's own
oo2core_6_win64.dll (tests/host/gen-oodle-vectors.py): each must give exactly the
original bytes. Built twice: -O2 as shipped, and with AddressSanitizer, whose
buffers are the shim's (input and output each with 64 bytes of room).
Needs a C++ compiler (clang++ on the macOS runner)."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, subprocess, sys, tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--compiler')
parser.add_argument('--sanitize', action='store_true')
args = parser.parse_args()

root = Path(__file__).resolve().parents[2]
src = root / 'madeira-oodle' / 'src'
data = root / 'tests' / 'host' / 'data' / 'oodle'
cxx = args.compiler or os.environ.get('CXX') or shutil.which('clang++') or shutil.which('c++')
if not cxx:
    raise SystemExit('A C++ compiler is required; decoder tests were not run')
compiler = [cxx] + (['c++'] if Path(cxx).stem == 'zig' else [])
run_env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
if os.name == 'nt':
    # LLVM-MinGW's dynamically linked ASAN runtime lives beside its compiler.
    run_env['PATH'] = str(Path(cxx).resolve().parent) + os.pathsep + run_env.get('PATH', '')

harness = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern "C" long long madeira_ooz_decompress(const unsigned char *src, size_t src_len, unsigned char *dst, size_t dst_len);
extern "C" void *madeira_ooz_decoder_create(void);
extern "C" void madeira_ooz_decoder_destroy(void *decoder);
extern "C" long long madeira_ooz_decompress_dec(void *decoder, const unsigned char *src, size_t src_len,
                                                unsigned char *dst, size_t dst_len);
static unsigned char *read_file(const char *path, long *size) {
    FILE *f = fopen(path, "rb"); if (!f) return 0;
    fseek(f, 0, SEEK_END); *size = ftell(f); fseek(f, 0, SEEK_SET);
    unsigned char *p = (unsigned char *)malloc(*size + 64);
    if (!p || fread(p, 1, *size, f) != (size_t)*size) { fclose(f); return 0; }
    fclose(f); memset(p + *size, 0, 64); return p;
}
/* The shim's slots: one decoder kept across calls. Each line of the list is
 * "<encoded> <raw size> <output or reject>"; the decoder and the output buffer are
 * reused for every line, so a stream follows whatever the previous one (of any
 * codec, or a rejected one) left in the decoder and its scratch. */
static int reuse(const char *list) {
    char enc[4096], dst[4096]; unsigned long long raw;
    void *dec = madeira_ooz_decoder_create();
    FILE *l = fopen(list, "r");
    size_t cap = 0; unsigned char *out = 0;
    if (!dec || !l) return 2;
    while (fscanf(l, "%4095s %llu %4095s", enc, &raw, dst) == 3) {
        long n; unsigned char *in = read_file(enc, &n);
        if (!in) return 2;
        if (raw + 64 > cap) { free(out); cap = raw + 64; out = (unsigned char *)malloc(cap); if (!out) return 2; }
        long long r = madeira_ooz_decompress_dec(dec, in, n, out, raw);
        if (!strcmp(dst, "reject")) {
            if (r != -1) { fprintf(stderr, "reuse: %s returned %lld\n", enc, r); return 1; }
        } else {
            if (r != (long long)raw) { fprintf(stderr, "reuse: %s returned %lld for %llu\n", enc, r, raw); return 1; }
            FILE *o = fopen(dst, "wb"); if (!o || fwrite(out, 1, raw, o) != raw) return 2; fclose(o);
        }
        free(in);
    }
    fclose(l); free(out); madeira_ooz_decoder_destroy(dec);
    return 0;
}
static int same(const unsigned char *p, size_t n, unsigned char value) {
    for (size_t i = 0; i < n; ++i) if (p[i] != value) return 0;
    return 1;
}
int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[1], "--reuse")) return reuse(argv[2]);
    if (argc != 4) return 2;
    FILE *f = fopen(argv[1], "rb"); size_t raw = strtoull(argv[2], 0, 10);
    if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    if (n < 0) return 2;
    unsigned char *in_base = (unsigned char *)malloc(n + 192), *out_base = (unsigned char *)malloc(raw + 192);
    if (!in_base || !out_base) return 2;
    unsigned char *in = in_base + 64, *out = out_base + 64;
    memset(in_base, 0xa5, n + 192); memset(out_base, 0xa5, raw + 192);
    memset(out, 0xcd, raw + 64);
    if (fread(in, 1, n, f) != (size_t)n) return 2;
    fclose(f); memset(in + n, 0, 64);
    long long r = madeira_ooz_decompress(in, n, out, raw);
    if (!same(in_base, 64, 0xa5) || !same(in + n, 64, 0) || !same(in + n + 64, 64, 0xa5) ||
        !same(out_base, 64, 0xa5) || !same(out + raw + 64, 64, 0xa5)) {
        fprintf(stderr, "decoder modified a guard outside its 64-byte padding\n"); return 1;
    }
    if (!strcmp(argv[3], "reject")) {
        if (r != -1) { fprintf(stderr, "unsupported/truncated input returned %lld\n", r); return 1; }
    } else {
        if (r != (long long)raw) { fprintf(stderr, "decode returned %lld for %zu\n", r, raw); return 1; }
        f = fopen(argv[3], "wb"); if (!f) return 2;
        if (fwrite(out, 1, raw, f) != raw) return 2;
        fclose(f);
    }
    free(in_base); free(out_base); return 0;
}
'''
manifest = json.loads((data / 'manifest.json').read_text())
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'harness.cpp').write_text(harness)
    builds = {'O2': ['-O2']}
    if args.sanitize:
        builds['asan'] = ['-O1', '-g', '-fsanitize=address', '-fno-omit-frame-pointer']
    for name, flags in builds.items():
        exe = tmp / ('ooz-' + name + ('.exe' if os.name == 'nt' else ''))
        # Sequential 8-byte LZ match copies must not become wider parallel
        # stores: the next copy can read bytes just written by the last one.
        cmd = compiler + ['-std=gnu++14', '-fno-exceptions', '-fno-rtti', '-fno-strict-aliasing', '-nostdlib++', '-fno-slp-vectorize',
               '-Wno-c++11-narrowing', '-w', *flags, '-o', str(exe),
               str(src / 'kraken.cpp'), str(tmp / 'harness.cpp')]
        r = subprocess.run(cmd, capture_output=True, text=True)
        assert r.returncode == 0, 'build %s failed:\n%s' % (name, r.stderr[-4000:])
        for e in manifest:
            out = tmp / 'out.bin'
            trailing = tmp / 'trailing.oz'
            trailing.write_bytes((data / e['file']).read_bytes() + b'\x00\xffMadeira trailing input\x00\x7f')
            for kind, encoded in [('exact', data / e['file']), ('trailing', trailing)]:
                r = subprocess.run([str(exe), str(encoded), str(e['raw_size']), str(out)],
                                   capture_output=True, text=True, env=run_env)
                assert r.returncode == 0, '%s (%s/%s): %s' % (e['file'], name, kind, r.stderr[-2000:])
                got = hashlib.sha256(out.read_bytes()).hexdigest()
                assert got == e['raw_sha256'], '%s (%s/%s): wrong bytes' % (e['file'], name, kind)
        # Bounded headers only: upstream ooz is not an arbitrary-input fuzzer.
        rejected = [
            ('empty', b'', 17), ('one-byte-header', b'\x8c', 17),
            ('no-quantum-header', b'\x8c\x06', 17),
            ('short-quantum-1', b'\x8c\x06\x00', 17),
            ('short-quantum-2', b'\x8c\x06\x00\x00', 17),
            ('missing-payload', b'\x8c\x06\x00\x00\x00', 17),
            ('short-stored-block', b'\xcc\x06A', 17),
            ('unsupported-lzna', b'\x8c\x05', 17),
            ('unsupported-bitknit', b'\x8c\x0b', 17),
            ('unsupported-codec', b'\x8c\x01', 17),
            ('checksum-kraken', b'\x8c\x86', 17),
            ('checksum-mermaid', b'\x8c\x8a', 17),
            ('checksum-leviathan', b'\x8c\x8c', 17),
            ('zero-output', b'\xcc\x06A', 0),
        ]
        for label, contents, raw_size in rejected:
            encoded = tmp / 'reject.oz'; encoded.write_bytes(contents)
            r = subprocess.run([str(exe), str(encoded), str(raw_size), 'reject'], capture_output=True, text=True,
                               env=run_env)
            assert r.returncode == 0, '%s (%s): %s' % (label, name, r.stderr[-2000:])
        # One decoder for every stream in turn, forwards then backwards, with a rejected
        # stream between the two passes: what the shim's reused slots do.
        lines, outs = [], []
        bad = tmp / 'reuse-reject.oz'
        bad.write_bytes(b'\x8c\x06\x00\x00\x00')
        order = list(manifest) + [None] + list(reversed(manifest))
        for k, e in enumerate(order):
            if e is None:
                lines.append('%s 17 reject' % bad)
                continue
            o = tmp / ('reuse-%d.bin' % k)
            lines.append('%s %d %s' % (data / e['file'], e['raw_size'], o))
            outs.append((e, o))
        (tmp / 'reuse.txt').write_text('\n'.join(lines) + '\n')
        r = subprocess.run([str(exe), '--reuse', str(tmp / 'reuse.txt')], capture_output=True, text=True, env=run_env)
        assert r.returncode == 0, 'reuse (%s): %s' % (name, r.stderr[-2000:])
        for e, o in outs:
            assert hashlib.sha256(o.read_bytes()).hexdigest() == e['raw_sha256'], \
                '%s (%s/reuse): wrong bytes' % (e['file'], name)
        print('ok   %s: %d exact/trailing streams and guard canaries; %d bounded rejection cases; '
              '%d decodes through one reused decoder' % (name, len(manifest), len(rejected), len(outs)))
print('PASS: ooz Kraken/Mermaid/Selkie/Leviathan matches the game\'s DLL on %d streams (%s)'
      % (len(manifest), ', '.join(builds)))
