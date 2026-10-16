#!/usr/bin/env python3
"""Compare ooz's portable SSE2 subset with real x86 SSE2, byte for byte.

Run on an x86-64 host with clang++ (or pass --compiler). The two separate
translation units avoid changing the portable implementation under test.
"""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--compiler')
args = parser.parse_args()
if platform.machine().lower() not in ('amd64', 'x86_64'):
    raise SystemExit('This oracle test requires an x86-64 host')
root = Path(__file__).resolve().parents[2]
compiler = args.compiler or os.environ.get('CXX') or shutil.which('clang++') or shutil.which('c++')
if not compiler:
    raise SystemExit('A C++ compiler is required')

harness = r'''
#include "ooz_compat.h"
static unsigned state = 0x12345678;
static unsigned next() { state ^= state << 13; state ^= state >> 17; state ^= state << 5; return state; }
static void emit(FILE *f, __m128i v) { unsigned char b[16]; _mm_storeu_si128((__m128i *)b, v); fwrite(b, 1, 16, f); }
static void emit32(FILE *f, unsigned v) { fwrite(&v, 1, 4, f); }
int main(int argc, char **argv) {
  FILE *f = fopen(argv[1], "wb"); if (!f) return 2;
  for (int k = 0; k < 1024; ++k) {
    unsigned char aa[32], bb[32], out[32];
    for (int i = 0; i < 32; ++i) { aa[i] = next(); bb[i] = next(); }
    /* Deliberately unaligned sources, as used by the decoder. */
    __m128i a = _mm_loadu_si128((const __m128i *)(aa + 1));
    __m128i b = _mm_loadu_si128((const __m128i *)(bb + 3));
    emit(f, a); emit(f, b);
    emit(f, _mm_loadl_epi64((const __m128i *)(aa + 1)));
    memset(out, 0x5a, sizeof(out)); _mm_storel_epi64((__m128i *)(out + 1), a); fwrite(out, 1, 32, f);
    memset(out, 0x5a, sizeof(out)); _mm_storeh_pi((__m64 *)(out + 1), _mm_castsi128_ps(a)); fwrite(out, 1, 32, f);
    emit(f, _mm_set1_epi8((char)next())); emit(f, _mm_set1_epi32((int)next()));
    emit(f, _mm_add_epi8(a, b)); emit(f, _mm_sub_epi8(a, b));
    emit(f, _mm_and_si128(a, b)); emit(f, _mm_xor_si128(a, b));
    emit(f, _mm_max_epu8(a, b)); emit(f, _mm_cmpeq_epi8(a, b));
    emit32(f, _mm_movemask_epi8(a)); emit32(f, _mm_movemask_epi8(b));
    emit(f, _mm_unpacklo_epi8(a, b)); emit(f, _mm_unpackhi_epi8(a, b));
    // IMMEDIATES
    unsigned value = next(); unsigned long bit = 0x77;
    emit32(f, _BitScanReverse(&bit, value)); emit32(f, (unsigned)bit);
    emit32(f, _BitScanForward(&bit, value)); emit32(f, (unsigned)bit);
    emit32(f, _BitScanReverse(&bit, 0)); emit32(f, (unsigned)bit);
    emit32(f, _BitScanForward(&bit, 0)); emit32(f, (unsigned)bit);
    emit32(f, _byteswap_ulong(value)); emit32(f, _byteswap_ushort(value));
    uint64 wide = ((uint64)value << 32) | next(); wide = _byteswap_uint64(wide); fwrite(&wide, 1, 8, f);
    for (int n = -64; n <= 64; ++n) emit32(f, _rotl(value, n));
    _mm_prefetch((const char *)aa, _MM_HINT_T0);
  }
  /* The LZ copier intentionally interleaves vector loads and stores, including
   * overlapping regions. A single memcpy of the whole run would be wrong. */
  for (int distance = -63; distance <= 63; ++distance) {
    unsigned char memory[256];
    for (int i = 0; i < 256; ++i) memory[i] = next();
    unsigned char *source = memory + 96, *destination = source + distance;
    _mm_storeu_si128((__m128i *)(destination + 0), _mm_loadu_si128((const __m128i *)(source + 0)));
    _mm_storeu_si128((__m128i *)(destination + 16), _mm_loadu_si128((const __m128i *)(source + 16)));
    _mm_storeu_si128((__m128i *)(destination + 32), _mm_loadu_si128((const __m128i *)(source + 32)));
    _mm_storeu_si128((__m128i *)(destination + 48), _mm_loadu_si128((const __m128i *)(source + 48)));
    fwrite(memory, 1, sizeof(memory), f);
  }
  for (int index = 0; index < 8; ++index) {
    int history[16];
    for (int i = 0; i < 16; ++i) history[i] = (int)next();
    __m128i temporary = _mm_loadu_si128((const __m128i *)&history[index + 4]);
    _mm_storeu_si128((__m128i *)&history[index + 1], _mm_loadu_si128((const __m128i *)&history[index]));
    _mm_storeu_si128((__m128i *)&history[index + 5], temporary);
    fwrite(history, 1, sizeof(history), f);
  }
  return fclose(f) ? 3 : 0;
}
'''
immediates = '\n'.join(
    ['emit(f, _mm_slli_si128(a, %d));' % n for n in range(256)] +
    ['emit(f, _mm_srli_epi16(a, %d));' % n for n in range(256)] +
    ['emit(f, _mm_shuffle_epi32(a, %d));' % n for n in range(256)])
harness = harness.replace('// IMMEDIATES', immediates)
with tempfile.TemporaryDirectory(prefix='oodle-intrinsics-') as tmp:
    tmp = Path(tmp)
    source = tmp / 'intrinsics.cpp'
    source.write_text(harness)
    outputs = {}
    for name, extra in [('portable', []), ('x86-sse2', ['-DMADEIRA_OOZ_X86_REFERENCE_SSE'])]:
        exe = tmp / (name + ('.exe' if os.name == 'nt' else ''))
        subprocess.run([compiler, '-O2', '-std=gnu++14', '-fno-strict-aliasing', '-nostdlib++',
                        '-I', str(root / 'madeira-oodle/src'), *extra, str(source), '-o', str(exe)], check=True)
        output = tmp / (name + '.bin')
        subprocess.run([str(exe), str(output)], check=True)
        outputs[name] = output.read_bytes()
    actual, expected = outputs['portable'], outputs['x86-sse2']
    if actual != expected:
        offset = next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
        raise SystemExit('FAIL: portable intrinsic differs from SSE2 at output byte %d' % offset)
    print('PASS: portable intrinsics match real SSE2 (%d output bytes; 1024 random pairs, all 256 immediates, overlapping LZ copies)' % len(actual))
