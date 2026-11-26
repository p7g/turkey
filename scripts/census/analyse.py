import sys, re
G = 1e9
def gb(x): return f"{x/G:.3f}"
def pct(a, b): return f"{100*a/b:.1f}%" if b else "-"

def parse(path):
    cols = []
    cur = None
    for line in open(path, errors='replace'):
        if line.startswith('[census '):
            m = re.match(r'\[census (\d+) (\w+)\] at gc (\d+) regions (\d+) peak (\d+) spares (\d+) regiontable (\d+) markstack (\d+)(?: memoryleft (\d+))?', line)
            cur = dict(kind=m[2], c=int(m[3]), regions=int(m[4]), peak=int(m[5]), spares=int(m[6]), rtab=int(m[7]), mstack=int(m[8]), classes={}, births={})
            cols.append(cur)
        elif line.startswith('[class] ') and cur is not None:
            v = list(map(int, line.split()[1:]))
            cur['classes'][v[0]] = dict(slot=v[1], w=v[2:20], alloc=v[20], fromavail=v[21], new=v[22])
        elif line.startswith('[birth] ') and cur is not None:
            v = list(map(int, line.split()[1:]))
            cur['births'][v[0]] = v[1:]
        elif line.startswith('[gc] region bytes'):
            pass
    return cols

def breakdown(cols, i):
    k = cols[i]; nxt = cols[i+1] if i+1 < len(cols) else None
    sm = dict(live=0, req=0, dead=0, deadreq=0, young=0, youngreq=0, free=0, reserved=0, usable=0, stranded=0, freeclassalloc=0)
    lg = dict(live=0, req=0, dead=0, young=0, reserved=0)
    for c, d in k['classes'].items():
        w = d['w']
        if c == 26:
            lg.update(live=w[4], req=w[5], dead=w[7], young=w[8], reserved=w[6])
            continue
        sm['live'] += w[4]; sm['req'] += w[5]; sm['dead'] += w[7]; sm['deadreq'] += w[16]
        sm['young'] += w[8]; sm['youngreq'] += w[17]; sm['free'] += w[9]; sm['reserved'] += w[6]
        a = nxt['classes'].get(c, {}).get('alloc', 0) if nxt else 0
        u = min(w[9], a)
        sm['usable'] += u; sm['stranded'] += w[9] - u
        if a > 0: sm['freeclassalloc'] += w[9]
    return sm, lg

def peak_table(cols, i, native):
    k = cols[i]; sm, lg = breakdown(cols, i)
    R = k['regions']
    over = sm['reserved'] - sm['live'] - sm['dead'] - sm['young'] - sm['free']
    rows = []
    if native:
        rows += [("live (requested bytes)", sm['req']),
                 ("dead old (requested)", sm['deadreq']),
                 ("young garbage (requested)", sm['youngreq']),
                 ("size-class rounding (all allocated slots)", sm['live']-sm['req'] + sm['dead']-sm['deadreq'] + sm['young']-sm['youngreq'])]
    else:
        rows += [("live (slot bytes)", sm['live']), ("dead old (slot bytes)", sm['dead']), ("young garbage (slot bytes)", sm['young'])]
    rows += [("free slots, usable by the next cycle", sm['usable']),
             ("free slots, stranded", sm['stranded']),
             ("region header + tail", over),
             ("large: live", lg['live']), ("large: dead old", lg['dead']), ("large: young garbage", lg['young'])]
    s = f"gc {k['c']} ({k['kind']}), regions {gb(R)} GB, spares {gb(k['spares']*65536)} GB\n"
    s += "| part | GB | share |\n|---|---:|---:|\n"
    for n, v in rows: s += f"| {n} | {gb(v)} | {pct(v, R)} |\n"
    s += f"(free slots in classes the next cycle allocates into at all: {gb(sm['freeclassalloc'])} of {gb(sm['free'])} GB; large-object rounding {gb(lg['reserved']-lg['live']-lg['dead']-lg['young'])} GB; large requested {gb(lg['req'])})\n"
    return s

def ages(cols):
    kinds = {k['c']: k['kind'] for k in cols}
    out = []
    for k in cols:
        if k['kind'] != 'full': continue
        C = k['c']
        lb = [0]*4; lw = [0]*4; db = [0]*4; dt = [0]*4
        for b, (live, words, dead, young) in k['births'].items():
            bf = C - ((C - b) & 255)
            fulls = sum(1 for j in range(bf, C) if kinds.get(j) == 'full')
            minors = (C - bf) - fulls
            f = min(fulls, 3); lb[f] += live; lw[f] += words
            m = min(minors, 3); db[m] += dead
            t = min(C - bf, 4) - 1 if C - bf > 0 else 0
            dt[min(t,3)] += dead
        out.append((C, lb, lw, db, dt))
    return out

def pointer_free(k):
    rows = []
    T = dict(obj=0, pf=0, pfb=0, nothing=0, bytes=0, words=0)
    for c, d in sorted(k['classes'].items()):
        w = d['w']
        if w[15] == 0: continue
        rows.append((c, d['slot'], w[15], w[11], w[4], w[12], w[13], w[14]))
        T['obj'] += w[15]; T['pf'] += w[11]; T['pfb'] += w[12]; T['nothing'] += w[13]; T['bytes'] += w[4]; T['words'] += w[14]
    return rows, T

def main(path, native):
    cols = parse(path)
    print(f"## {path}: {len(cols)} collections, {sum(k['kind']=='full' for k in cols)} full")
    ip = max(range(len(cols)), key=lambda i: cols[i]['regions'])
    fulls = [i for i, k in enumerate(cols) if k['kind'] == 'full']
    before = [i for i in fulls if i < ip]; after = [i for i in fulls if i > ip]
    pick = ([before[-1]] if before else []) + [ip] + ([after[0]] if after else [])
    for i in sorted(set(pick)):
        print(peak_table(cols, i, native))
    print("### age at full collections: live bytes / traced words by full collections survived (0,1,2,3+); dead old bytes by minors survived (0,1,2,3+); dead old by total survived (1,2,3,4+)")
    tot = [[0]*4 for _ in range(4)]
    for C, lb, lw, db, dt in ages(cols):
        print(f"gc {C}: live GB {[gb(x) for x in lb]} words M {[round(x/1e6,1) for x in lw]} deadold GB {[gb(x) for x in db]} by total {[gb(x) for x in dt]}")
        for j in range(4): tot[0][j]+=lb[j]; tot[1][j]+=lw[j]; tot[2][j]+=db[j]; tot[3][j]+=dt[j]
    print(f"SUM: live {[pct(x,sum(tot[0])) for x in tot[0]]} words {[pct(x,sum(tot[1])) for x in tot[1]]} ({sum(tot[1])/1e6:.0f} M) deadold-by-minors {[pct(x,sum(tot[2])) for x in tot[2]]} ({gb(sum(tot[2]))} GB) deadold-by-total {[pct(x,sum(tot[3])) for x in tot[3]]}")
    # pointer-free, at the full nearest the peak, and summed over all fulls
    i = pick[-1] if cols[pick[-1]]['kind']=='full' else fulls[-1]
    rows, T = pointer_free(cols[i])
    print(f"### pointer-free at gc {cols[i]['c']} (full)")
    print("| class | slot | marked objects | pointer-free objects | marked MB | pointer-free MB | pf share of bytes | scan-nothing objects |\n|---:|---:|---:|---:|---:|---:|---:|---:|")
    for c, slot, obj, pf, b, pfb, nothing, words in rows:
        print(f"| {c} | {slot} | {obj} | {pf} ({pct(pf,obj)}) | {b/1e6:.1f} | {pfb/1e6:.1f} | {pct(pfb,b)} | {nothing} ({pct(nothing,obj)}) |")
    print(f"| all | | {T['obj']} | {T['pf']} ({pct(T['pf'],T['obj'])}) | {T['bytes']/1e6:.1f} | {T['pfb']/1e6:.1f} | {pct(T['pfb'],T['bytes'])} | {T['nothing']} ({pct(T['nothing'],T['obj'])}) |")
    A = dict(obj=0, pf=0, nothing=0, bytes=0, pfb=0)
    for j in fulls:
        _, t = pointer_free(cols[j])
        for key in A: A[key] += t[key]
    print(f"all fulls: objects {A['obj']}, pointer-free {pct(A['pf'],A['obj'])} of objects, {pct(A['pfb'],A['bytes'])} of bytes; scan nothing {pct(A['nothing'],A['obj'])}")

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] == 'native')
