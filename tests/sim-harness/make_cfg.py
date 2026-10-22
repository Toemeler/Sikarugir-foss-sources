#!/usr/bin/env python3
"""make_cfg.py - the madeira.cfg a plan.json variant asks for.

    VARIANT='<variant json>' make_cfg.py <GameRecommendations.swift> <out madeira.cfg>

"cfg_preset": the id of a GameRecommendation (e.g. "elden-ring-ersc"): its config text,
exactly what the app applies for that game on a device. "cfg": lines appended after it
(later keys win). Writes nothing and exits 0 when the variant has neither.
"""
import json, os, re, sys

v = json.loads(os.environ.get('VARIANT', '{}'))
lines = []
preset = v.get('cfg_preset')
if preset:
    swift = open(sys.argv[1], encoding='utf-8').read()
    m = re.search(r'id:\s*"%s".*?config:\s*"""\n(.*?)"""' % re.escape(preset), swift, re.S)
    if not m:
        sys.exit('cfg_preset %r not found in %s' % (preset, sys.argv[1]))
    lines += [l.strip() for l in m.group(1).splitlines()]
lines += v.get('cfg', [])
if not lines:
    print('no madeira.cfg for this variant')
    sys.exit(0)
os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True)
with open(sys.argv[2], 'w') as f:
    f.write('\n'.join(lines) + '\n')
print('madeira.cfg (%s):' % sys.argv[2])
print(open(sys.argv[2]).read())
