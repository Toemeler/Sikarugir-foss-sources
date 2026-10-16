#!/usr/bin/env python3
"""Execute the real Windows Oodle shim against a mock original DLL.

This checks the export ABI, native buffer isolation, fallback gates, argument
forwarding and simultaneous first calls. It does not redistribute or require
RAD's DLL. Run on 64-bit Windows with llvm-mingw, for example:
  python tests/host/check-oodle-shim.py --compiler path/to/x86_64-w64-mingw32-clang++
Decoder correctness is separately checked by check-oodle-native.py.
"""
from pathlib import Path
import argparse
import concurrent.futures
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'madeira-oodle' / 'src'
NAMES = {
    'OodleLZ_Compress': 10,
    'OodleLZ_Decompress': 14,
    'OodleLZ_GetCompressedBufferSizeNeeded': 2,
    'OodleLZ_CompressOptions_GetDefault': 2,
    'OodleLZ_GetCompressedStepForRawStep': 6,
    'OodleLZDecoder_MemorySizeNeeded': 2,
}
IDS = dict(zip(NAMES, range(1, 7)))


def digest(name, values):
    return (IDS[name] * 1009 + sum((i + 1) * (v & ((1 << 64) - 1))
                                for i, v in enumerate(values))) & 0x7fffffff


def mock_source():
    result = ['#include <stdint.h>', 'extern "C" {']
    for name, argc in NAMES.items():
        params = ', '.join('intptr_t a%d' % i for i in range(argc))
        values = ' + '.join('((uintptr_t)a%d * %du)' % (i, i + 1) for i in range(argc))
        result.append('__declspec(dllexport) intptr_t %s(%s) { return (%du + %s) & 0x7fffffff; }'
                      % (name, params, IDS[name] * 1009, values))
    result.append('}')
    return '\n'.join(result)


STUB = r'''
#include <windows.h>
#include <stdint.h>
#include <string.h>
static volatile LONG calls, bad_padding;
extern "C" __declspec(dllexport) LONG TestDecodeCalls() { return calls; }
extern "C" __declspec(dllexport) LONG TestBadPadding() { return bad_padding; }
extern "C" long long madeira_ooz_decompress(const unsigned char *src, size_t n,
                                            unsigned char *dst, size_t raw) {
    InterlockedIncrement(&calls);
    for (size_t i = 0; i < 64; i++) if (src[n + i]) InterlockedIncrement(&bad_padding);
    // Exercise the exact tail allowance the production decoder receives.
    memset(dst, 0xa5, raw + 64);
    return src[0] == 0xff ? -1 : src[0] == 0xfe ? (long long)raw - 1 : (long long)raw;
}
'''


def load_shim(path):
    dll = ctypes.CDLL(str(path))
    for name, argc in NAMES.items():
        f = getattr(dll, name)
        f.argtypes = [ctypes.c_ssize_t] * argc
        f.restype = ctypes.c_ssize_t
    dll.TestDecodeCalls.restype = ctypes.c_long
    dll.TestBadPadding.restype = ctypes.c_long
    return dll


def worker(path, mode):
    dll = load_shim(path)
    if mode == 'real':
        data = ROOT / 'tests' / 'host' / 'data' / 'oodle'
        manifest = json.loads((data / 'manifest.json').read_text())
        for entry in manifest:
            encoded = (data / entry['file']).read_bytes()
            comp = ctypes.create_string_buffer(encoded)
            raw = ctypes.create_string_buffer(entry['raw_size'])
            result = dll.OodleLZ_Decompress(ctypes.addressof(comp), len(encoded),
                                          ctypes.addressof(raw), len(raw),
                                          1, 0, 0, 0, 0, 0, 0, 0, 0, 3)
            assert result == len(raw), (entry['file'], result, len(raw))
            assert hashlib.sha256(raw.raw).hexdigest() == entry['raw_sha256'], entry['file']
        assert dll.TestDecodeCalls() == 0
        print('ok   original DLL fallback: %d streams match original bytes' % len(manifest))
        return
    comp = (ctypes.c_ubyte * 32)(*range(32))
    raw = (ctypes.c_ubyte * 96)(*([0x5a] * 96))
    base = [ctypes.addressof(comp), len(comp), ctypes.addressof(raw), 64,
            1, 0, 0, 0, 0, 0, 0, 0, 0, 3]

    if mode == 'missing':
        # A failed copy must not reuse a previous DLL from this temp directory.
        assert dll.OodleLZ_Compress(*range(10)) == 0
        assert dll.OodleLZ_Decompress(*(base[:5] + [1] + base[6:])) == 0
        print('ok   missing original fails closed without stale-copy reuse')
        return

    # Race the very first original-DLL request. Every argument slot contributes
    # to a deterministic answer, including stack args 5..10 and 5..14.
    def forward(i):
        name = list(NAMES)[i % len(NAMES)]
        values = [0x112233445 + 37 * k + i for k in range(NAMES[name])]
        if name == 'OodleLZ_Decompress':
            values[9] = 1  # callback forces fallback; mock never dereferences.
        got = getattr(dll, name)(*values)
        assert got == digest(name, values), (name, got, digest(name, values))
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(forward, range(192)))
    assert dll.TestDecodeCalls() == 0
    copies = list(Path(os.environ['TEMP']).glob('madeira-oo2core6-*.dll'))
    assert len(copies) == 1, 'original must initialize once, not %d times' % len(copies)
    print('ok   all six exports and concurrent original initialization')

    if mode == 'off':
        assert dll.OodleLZ_Decompress(*base) == digest('OodleLZ_Decompress', base)
        os.environ['MADEIRA_OODLE_NATIVE'] = '1'
        assert dll.OodleLZ_Decompress(*base) == digest('OodleLZ_Decompress', base)
        assert dll.TestDecodeCalls() == 0
        print('ok   native=0 remains disabled after configuration initialization')
        return

    assert dll.OodleLZ_Decompress(*base) == 64
    assert bytes(raw[:64]) == bytes([0xa5] * 64)
    assert bytes(raw[64:]) == bytes([0x5a] * 32), 'native tail overwrote caller'
    assert bytes(comp) == bytes(range(32)), 'native modified caller input'
    assert dll.TestBadPadding() == 0, 'private input padding is not zeroed'
    # A base equal to output is a whole-buffer decode, not external history.
    values = base.copy()
    values[7] = values[2]
    values[8] = values[3]
    assert dll.OodleLZ_Decompress(*values) == 64

    gates = [
        (0, 0), (1, 0), (1, -1), (1, 0x80000000),
        (2, 0), (3, 0), (3, -1), (3, 0x80000000),
        (5, 1), (7, base[2] + 1), (9, 0x1234),
        (13, 0), (13, 1), (13, 2), (13, 4),
    ]
    before = dll.TestDecodeCalls()
    for index, value in gates:
        values = base.copy()
        values[index] = value
        assert dll.OodleLZ_Decompress(*values) == digest('OodleLZ_Decompress', values)
    assert dll.TestDecodeCalls() == before, 'unsupported call reached native decoder'
    # Both negative and short native returns use the untouched caller buffers
    # and all 14 original arguments for fallback.
    for marker in (0xff, 0xfe):
        comp[0] = marker
        raw[:] = [0x5a] * len(raw)
        assert dll.OodleLZ_Decompress(*base) == digest('OodleLZ_Decompress', base)
        assert bytes(raw) == bytes([0x5a] * len(raw)), 'failed native result escaped'
    os.environ['MADEIRA_OODLE_NATIVE'] = '0'
    comp[0] = 0
    assert dll.OodleLZ_Decompress(*base) == 64, 'configuration changed mid-session'
    print('ok   private buffers, %d fallback gates, rejected results and stable configuration' % len(gates))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler')
    parser.add_argument('--original', type=Path,
                        help='optionally verify fallback on all vectors using a private RAD DLL; never copied into the repo')
    parser.add_argument('--worker', choices=['on', 'off', 'missing', 'real'])
    parser.add_argument('--dll', type=Path)
    args = parser.parse_args()
    if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise SystemExit('This runtime ABI check requires 64-bit Windows; it was not run')
    if args.worker:
        worker(args.dll, args.worker)
        return
    compiler = args.compiler or os.environ.get('CXX') or shutil.which('x86_64-w64-mingw32-clang++') or shutil.which('clang++')
    if not compiler:
        raise SystemExit('An x64 Windows C++ compiler is required')
    with tempfile.TemporaryDirectory(prefix='madeira-oodle-shim-') as directory:
        directory = Path(directory)
        mock = directory / 'original.cpp'
        stub = directory / 'stub.cpp'
        mock.write_text(mock_source())
        stub.write_text(STUB)
        common = [compiler, '-shared', '-O2', '-std=gnu++14', '-fno-exceptions',
                  '-fno-rtti', '-nostdlib++']
        original = directory / 'mock-original.dll'
        shim = directory / 'shim.dll'
        subprocess.run(common + [str(mock), '-o', str(original)], check=True)
        subprocess.run(common + [str(SRC / 'oo2core_shim.cpp'), str(stub),
                                 str(SRC / 'oo2core_6_win64.def'), '-o', str(shim)], check=True)
        for mode in ('on', 'off', 'missing'):
            temp = directory / mode
            temp.mkdir()
            env = dict(os.environ, TEMP=str(temp), TMP=str(temp),
                       MADEIRA_OODLE_NATIVE='0' if mode == 'off' else '1',
                       MADEIRA_OODLE_ORIGINAL_PATH=str(original if mode != 'missing' else directory / 'absent.dll'))
            subprocess.run([sys.executable, __file__, '--worker', mode, '--dll', str(shim)],
                           check=True, env=env)
        if args.original:
            assert args.original.is_file(), 'private original DLL does not exist'
            temp = directory / 'real'
            temp.mkdir()
            env = dict(os.environ, TEMP=str(temp), TMP=str(temp), MADEIRA_OODLE_NATIVE='0',
                       MADEIRA_OODLE_ORIGINAL_PATH=str(args.original.resolve()))
            subprocess.run([sys.executable, __file__, '--worker', 'real', '--dll', str(shim)],
                           check=True, env=env)
    print('PASS: Oodle shim runtime ABI, forwarding, isolation and fallback checks')


if __name__ == '__main__':
    main()
