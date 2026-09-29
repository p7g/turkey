"""Allocation counts by site, from a build that counts them.

    TURKEY_ALLOC_SITES=1 boot native prog.gob > prog.s 2> sites.txt
    cc -o prog prog.s && ./prog          # writes turkey-alloc-sites.bin
    python3 scripts/alloc_sites.py sites.txt turkey-alloc-sites.bin [--top N]

Prints the allocations by opcode, by reason, by module, by function and by
site; and the calls that may allocate (through a closure or a dictionary, or
into the runtime), which are counted but are not allocations themselves.
See src/Turkey/Sites.gob.
"""
import collections, struct, sys

table_path, bin_path = sys.argv[1], sys.argv[2]
top = int(sys.argv[sys.argv.index('--top') + 1]) if '--top' in sys.argv else 40

sites = {}
for line in open(table_path, errors='replace'):
    if not line.startswith('site\t'):
        continue
    parts = line.rstrip('\n').split('\t')
    sid, fn, op, reason, origin = parts[1:6]
    sites[int(sid)] = (fn, op, reason, origin)

data = open(bin_path, 'rb').read()
n = struct.unpack_from('<q', data, 0)[0]
counts = struct.unpack_from('<%dq' % n, data, 8)

def module(fn):
    return fn.split('#')[0] if '#' in fn else fn

direct_ops = {'object.new', 'array.new', 'cell.new', 'closure.new', 'box'}
rows = []
for sid in range(n):
    c = counts[sid]
    if c == 0:
        continue
    fn, op, reason, origin = sites[sid]
    rows.append((c, sid, fn, op, reason, origin))

direct = [r for r in rows if r[3] in direct_ops]
calls = [r for r in rows if r[3] not in direct_ops]
total = sum(r[0] for r in direct)
print(f'sites {n}, executed {len(rows)}; direct allocations {total:,}; '
      f'possibly-allocating calls executed {sum(r[0] for r in calls):,} (not counted as allocations)')

def table(title, key, rows, limit=top):
    agg = collections.Counter()
    nsites = collections.Counter()
    for r in rows:
        agg[key(r)] += r[0]
        nsites[key(r)] += 1
    print(f'\n== {title}')
    for k, c in agg.most_common(limit):
        print(f'{c:>14,} {100.0 * c / max(total, 1):5.1f}%  {nsites[k]:>5} sites  {k}')

table('by opcode', lambda r: r[3], direct)
table('by reason', lambda r: (r[3], r[4]), direct)
table('by module', lambda r: module(r[2]), direct, 30)
table('by function', lambda r: r[2], direct, 40)
print(f'\n== top sites')
for c, sid, fn, op, reason, origin in sorted(direct, reverse=True)[:top]:
    print(f'{c:>14,} {100.0 * c / total:5.1f}%  {op:11} {reason[:60]:60} {fn}  {origin}')
table('calls that may allocate, by callee kind', lambda r: (r[3], r[4][:50]), calls, 15)
