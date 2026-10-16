"""Kraken/Mermaid/Selkie/Leviathan test streams from the game's own oo2core_6_win64.dll (Windows copy,
read-only), for the native decoder's host check. Inputs are synthetic or Madeira's own GPL sources."""
import ctypes, hashlib, json, os, random, struct, sys
DLL = r'C:\Games\Elden Ring\ZIP\ELDEN.RING.v1.16.2\ELDEN RING\oo2core_6_win64.dll'
OUT = sys.argv[1]
src_root = sys.argv[2]
os.makedirs(OUT, exist_ok=True)
oo = ctypes.WinDLL(DLL)
comp = oo.OodleLZ_Compress
comp.restype = ctypes.c_ssize_t
comp.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int,
                 ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
dec = oo.OodleLZ_Decompress
dec.restype = ctypes.c_ssize_t
dec.argtypes = [ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_ssize_t, ctypes.c_int]
rng = random.Random(1245620)
def text():
    out = b''
    for dp, dn, fn in sorted(os.walk(src_root)):
        for f in sorted(fn):
            if f.endswith(('.c', '.h', '.swift', '.py')):
                out += open(os.path.join(dp, f), 'rb').read()
            if len(out) > 3 << 20: return out[:3 << 20]
    return out
def structured(n):
    b = bytearray()
    x = 0.0
    while len(b) < n:
        x += rng.uniform(-1, 1)
        b += struct.pack('<fIhH', x, len(b) // 16, rng.randint(-50, 50), 0xBEEF if rng.random() < .3 else rng.randint(0, 65535))
    return bytes(b[:n])
def mixed(n):
    b = bytearray()
    while len(b) < n:
        k = rng.random()
        if k < .3: b += bytes(rng.getrandbits(8) for _ in range(rng.randint(1, 300)))
        elif k < .6 and len(b) > 64:
            o = rng.randint(1, min(len(b), 1 << 20)); l = rng.randint(3, 400)
            for i in range(l): b.append(b[len(b) - o])
        elif k < .8: b += bytes([rng.getrandbits(8)]) * rng.randint(1, 2000)
        else: b += structured(rng.randint(16, 4000))
    return bytes(b[:n])
T = text()
inputs = {
    'tiny1': b'A', 'tiny17': b'0123456789abcdefg', 'zeros1m': bytes(1 << 20),
    'random300k': bytes(rng.getrandbits(8) for _ in range(300000)),
    'text3m': T, 'text262145': T[:262145], 'structured2m': structured(2 << 20),
    'mixed1m': mixed(1 << 20), 'mixed5m': mixed(5 << 20),
}
codecs = {'kraken': 8, 'mermaid': 9, 'selkie': 11, 'leviathan': 13}
plan = [('kraken', l) for l in (1, 4, 6, 8, 9)] + [('mermaid', 4), ('mermaid', 8), ('selkie', 4), ('leviathan', 6), ('leviathan', 8)]
manifest = []
for name, raw in inputs.items():
    for cname, level in plan:
        if name in ('mixed5m', 'text3m') and level not in (6, 8): continue
        rb = ctypes.create_string_buffer(raw, len(raw))
        cb = ctypes.create_string_buffer(len(raw) + 65536 + len(raw) // 8)
        n = comp(codecs[cname], rb, len(raw), cb, level, None, None, None, None, 0)
        assert n > 0, (name, cname, level)
        out = ctypes.create_string_buffer(len(raw) + 64)
        m = dec(cb, n, out, len(raw), 1, 0, 0, None, 0, None, None, None, 0, 3)
        assert m == len(raw) and out.raw[:len(raw)] == raw, (name, cname, level, m)
        fn = '%s.%s%d.oz' % (name, cname, level)
        open(os.path.join(OUT, fn), 'wb').write(cb.raw[:n])
        manifest.append({'file': fn, 'raw_size': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest()})
        print(fn, len(raw), '->', n)
json.dump(manifest, open(os.path.join(OUT, 'manifest.json'), 'w'), indent=1)
print('vectors:', len(manifest), 'bytes:', sum(os.path.getsize(os.path.join(OUT, m['file'])) for m in manifest))
