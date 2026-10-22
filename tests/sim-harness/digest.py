#!/usr/bin/env python3
"""digest.py - the part of a harness run worth reading, for the job log and summary.

    digest.py <logs dir> <arm64ec-windows dir> <llvm bin dir>

For every <exe>.log in <logs dir> (run.sh's output: the whole Madeira session log):
the harness result, the guest's own log files, the [guest-seh] lines, a census of the
exceptions FEX and ntdll report (with every SINGLE_STEP's address as module+offset),
the protection requests, the host faults the unix side could not handle, with their
pc as module+offset and, for xtajit64.dll and ntdll.dll, the function from the DLL's
symbol table (llvm-symbolizer), and the end of the log.
"""
import collections, glob, os, re, struct, subprocess, sys

LOGS, DLLS, LLVM = sys.argv[1], sys.argv[2], sys.argv[3]
out = []
def p(s=''): out.append(s)

def image_info(path):
    try:
        d = open(path, 'rb').read(4096)
        pe = struct.unpack_from('<I', d, 0x3c)[0]
        magic = struct.unpack_from('<H', d, pe + 24)[0]
        base = struct.unpack_from('<Q', d, pe + 24 + 24)[0] if magic == 0x20b else struct.unpack_from('<I', d, pe + 24 + 28)[0]
        return base, struct.unpack_from('<I', d, pe + 24 + 56)[0]
    except Exception:
        return None, None

ALIASES = {'libarm64ecfex.dll': 'xtajit64.dll'}

def symbolize(dll, rva):
    path = os.path.join(DLLS, ALIASES.get(dll, dll))
    base, _ = image_info(path)
    if base is None: return ''
    try:
        r = subprocess.run([os.path.join(LLVM, 'llvm-symbolizer'), '--obj=' + path, '--functions=linkage',
                            '--no-inlines', hex(base + rva)], capture_output=True, text=True, timeout=30)
        fn = r.stdout.strip().splitlines()
        if fn and fn[0] not in ('??', ''): return fn[0] + (' ' + fn[1] if len(fn) > 1 and not fn[1].startswith('??') else '')
        r = subprocess.run([os.path.join(LLVM, 'llvm-nm'), '--defined-only', '-n', path], capture_output=True, text=True, timeout=30)
        best = None
        for line in r.stdout.splitlines():
            f = line.split()
            if len(f) >= 3 and f[1] in 'tTwW':
                a = int(f[0], 16)
                if a <= base + rva: best = (a, f[2])
                else: break
        return '%s+0x%x (nearest symbol)' % (best[1], base + rva - best[0]) if best else ''
    except Exception as e:
        return 'symbolizer: %s' % e

def digest(log):
    text = open(log, 'rb').read().decode('utf-8', 'replace').splitlines()
    exe = os.path.basename(log)[:-4]
    p('=' * 100)
    p('### %s' % exe)
    for l in text:
        if l.startswith('[sim-harness] exe=') or l.startswith('[sim-harness] RESULT') or l.startswith('[run.sh]'):
            p(l)

    # Modules: guest VA (FEX "Load module"), and pool copies (jit-pool, seh-xlate).
    mods = {}      # name -> list of guest bases (one per process)
    pools = []     # (lo, hi, name, pe_base)
    for l in text:
        m = re.search(r'Load module (\S+) \([^)]*\): ([0-9A-Fa-f]+)', l)
        if m: mods.setdefault(m.group(1).lower(), []).append(int(m.group(2), 16))
        m = re.search(r'\[jit-pool\] image 0x([0-9a-f]+)\+0x([0-9a-f]+) \(([^)]+)\) . pool 0x([0-9a-f]+)', l)
        if m:
            pe, sz, name, pool = int(m.group(1), 16), int(m.group(2), 16), m.group(3).lower(), int(m.group(4), 16)
            pools.append((pool, pool + sz, name, pe))
        m = re.search(r'TRANSLATED -> ([0-9A-Fa-f]+)\s+module=L"([^"]+)"\+0x([0-9a-f]+)', l)
        if m:
            name, off = m.group(2).lower(), int(m.group(3), 16)
            lo = int(m.group(1), 16) - off
            _, soi = image_info(os.path.join(DLLS, ALIASES.get(name, name)))
            if not any(x[0] == lo for x in pools): pools.append((lo, lo + (soi or 0x400000), name, None))
        m = re.search(r'\[tls-life\].*module=L"([^"]+)" base=([0-9A-Fa-f]+)', l)
        if m: mods.setdefault(m.group(1).lower(), []).append(int(m.group(2), 16))
    sizes = {}
    for name in mods:
        _, soi = image_info(os.path.join(DLLS, name))
        sizes[name] = soi or 0x100000
    def where(a):
        for lo, hi, name, pe in pools:
            if lo <= a < hi:
                s = symbolize(name, a - lo) if ALIASES.get(name, name) in ('xtajit64.dll', 'ntdll.dll') else ''
                return '%s+0x%x (pool copy)%s' % (name, a - lo, ' ' + s if s else '')
        for name, bases in mods.items():
            for b in bases:
                if b <= a < b + sizes[name]:
                    s = symbolize(name, a - b) if name in ('xtajit64.dll', 'ntdll.dll') else ''
                    return '%s+0x%x%s' % (name, a - b, ' ' + s if s else '')
        return '?'
    if 'ersc.dll' in mods: p('ersc.dll at %s' % ', '.join(hex(b) for b in mods['ersc.dll']))

    for g in sorted(glob.glob(os.path.join(LOGS, 'guest-*.log'))):
        if os.path.basename(g)[6:-4] in (exe[:-4], 'eldenring'):
            p('--- %s' % os.path.basename(g))
            for l in open(g, errors='replace').read().splitlines()[:120]: p('  ' + l)

    seh = [l[l.index('[guest-seh]'):] for l in text if '[guest-seh]' in l]
    p('--- [guest-seh]: %d lines' % len(seh))
    for l in seh[:40]: p('  ' + l)
    if len(seh) > 50: p('  ...')
    for l in seh[max(40, len(seh) - 10):]: p('  ' + l)

    exc = collections.Counter(); first = {}
    steps = []
    for l in text:
        m = re.search(r'Exception: Code: ([0-9A-F]+) Address: ([0-9A-F]+)(.*)', l)
        if m:
            k = (m.group(1), re.sub(r'[0-9A-F]{6,}', 'X', m.group(3)).strip())
            exc[k] += 1; first.setdefault(k, m.group(2))
            if m.group(1) == '80000004' and len(steps) < 20: steps.append(int(m.group(2), 16))
    p('--- exceptions (FEX "Exception:" lines): code, detail, count, first address')
    for (c, d), n in exc.most_common(20): p('  %s %-40s x%-6d first %s' % (c, d[:40], n, first[(c, d)]))
    for a in steps: p('  SINGLE_STEP at %#x = %s' % (a, where(a)))
    codes = collections.Counter(re.search(r'code=([0-9a-f]{8})', l).group(1) for l in text if '[exc] ml811' in l)
    if codes: p('  dispatched to handlers ([exc]): ' + ', '.join('%s x%d' % kv for kv in codes.most_common()))

    req = [l for l in text if '[exec-req] #' in l and 'ecx-trace' not in l]
    p('--- protection requests ([exec-req]): %d' % len(req))
    for l in req[:20]: p('  ' + l[l.index('[exec-req]'):])

    p('--- host faults and dispatch failures')
    n = 0
    for l in text:
        m = re.search(r'SEGV at pc=0x([0-9a-f]+) addr=0x([0-9a-f]+)', l) or \
            re.search(r'UNHANDLED #(?:1|2|3|100|200|1000) pc=0x([0-9a-f]+) addr=0x([0-9a-f]+)', l)
        if m and n < 12:
            n += 1
            p('  %s' % l.strip()[:160])
            p('      pc %s' % where(int(m.group(1), 16)))
        elif any(k in l for k in ('unable to dispatch', 'stack-ovf]', '[veh] calling', 'NtTerminateProcess', 'abort_process')):
            p('  ' + l.strip()[:200])

    p('--- last 25 lines')
    tail = [l for l in text if l.strip() and not l.startswith(('[fault-tls]', '[fault-full]', '[fault-host]', '[x64-iat]'))]
    for l in tail[-25:]: p('  ' + l[:220])

for log in sorted(glob.glob(os.path.join(LOGS, '*.exe.log'))):
    digest(log)
print('\n'.join(out))
s = os.environ.get('GITHUB_STEP_SUMMARY')
if s:
    with open(s, 'a') as f: f.write('```\n' + '\n'.join(out)[:60000] + '\n```\n')
