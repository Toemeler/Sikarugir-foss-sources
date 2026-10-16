#!/usr/bin/env python3
"""Make synthetic independent-seek fixtures using the user's Oodle DLL.

The DLL is only read/executed. No game data is included in the generated
fixtures. Every stream is decoded by that same DLL and checked before saving.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import random
import struct

parser = argparse.ArgumentParser()
parser.add_argument('--dll', default=r'C:\Games\Elden Ring\ZIP\ELDEN.RING.v1.16.2\ELDEN RING\oo2core_6_win64.dll')
parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'data/oodle')
args = parser.parse_args()
library = C.WinDLL(args.dll)
compress = library.OodleLZ_Compress
compress.restype = C.c_ssize_t
compress.argtypes = [C.c_int, C.c_void_p, C.c_ssize_t, C.c_void_p, C.c_int,
                    C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_ssize_t]
decode = library.OodleLZ_Decompress
decode.restype = C.c_ssize_t
decode.argtypes = [C.c_void_p, C.c_ssize_t, C.c_void_p, C.c_ssize_t, C.c_int,
                  C.c_int, C.c_int, C.c_void_p, C.c_ssize_t, C.c_void_p,
                  C.c_void_p, C.c_void_p, C.c_ssize_t, C.c_int]
defaults = library.OodleLZ_CompressOptions_GetDefault
defaults.restype = C.c_void_p
defaults.argtypes = [C.c_int, C.c_int]

rng = random.Random(0x4d414445495241)
raw = bytearray()
for block in range(4):
    # Different seed bytes in every block expose a forgotten dictionary reset.
    seed = ('Independent Madeira seek block %d: ' % block).encode('ascii')
    repeated = (seed * (0x30000 // len(seed) + 1))[:0x30000]
    noise = bytes(rng.getrandbits(8) for _ in range(0x10000))
    raw += repeated + noise
raw = bytes(raw[:3 * 0x40000 + 17])
digest = hashlib.sha256(raw).hexdigest()
pending = []
for name, codec, level in [('kraken', 8, 4), ('mermaid', 9, 4),
                            ('selkie', 11, 4), ('leviathan', 13, 6)]:
    options = C.create_string_buffer(C.string_at(defaults(codec, level), 48), 48)
    struct.pack_into('<ii', options, 8, 1, 0x40000)
    encoded = C.create_string_buffer(len(raw) + len(raw) // 8 + 65536)
    n = compress(codec, raw, len(raw), encoded, level, options, None, None, None, 0)
    assert 0 < n <= len(encoded), (name, n)
    restored = C.create_string_buffer(len(raw) + 64)
    result = decode(encoded, n, restored, len(raw), 1, 0, 0, None, 0, None, None, None, 0, 3)
    assert result == len(raw) and restored.raw[:len(raw)] == raw, name
    filename = 'independent-seek-786449.%s%d.oz' % (name, level)
    pending.append((filename, encoded.raw[:n]))
    print('%s: %d -> %d; original decoder SHA-256 verified' % (filename, len(raw), n))

args.output.mkdir(parents=True, exist_ok=True)
manifest_path = args.output / 'manifest.json'
manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
replaced = {filename for filename, _ in pending}
manifest = [entry for entry in manifest if entry['file'] not in replaced]
for filename, encoded in pending:
    (args.output / filename).write_bytes(encoded)
    manifest.append({'file': filename, 'raw_size': len(raw), 'raw_sha256': digest,
                     'seek_chunk_reset': True, 'seek_chunk_size': 0x40000})
manifest_path.write_text(json.dumps(manifest, indent=1) + '\n')
