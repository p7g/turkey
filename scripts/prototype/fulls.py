import re, sys
# Prototype only: held and live after the last full collections, and the line
# census, from a --gc-stats log. Usage: fulls.py LOG
lines = open(sys.argv[1]).read().splitlines()
rows = []
census = {}
for i, l in enumerate(lines):
    m = re.match(r"\[gc (\d+) (full|minor)\].*held (\d+), live bytes (\d+)", l)
    if m: rows.append((int(m[1]), m[2], int(m[3]), int(m[4])))
    m = re.match(r"\[gc (\d+) lines\] free (\d+), conservative-lost (\d+), holes (.*)", l)
    if m: census[int(m[1])] = (int(m[2]), int(m[3]), m[4].split())
peak = max(r[2] for r in rows)
fulls = [r for r in rows if r[1] == "full"]
for n, k, h, lv in fulls[-6:]:
    c = census.get(n)
    extra = ""
    if c and c[0]:
        extra = f" free-lines {c[0]} lost {c[1]} ({100*c[1]/c[0]:.1f}% of free)"
    print(f"gc {n} {k}: held {h/1e9:.2f} GB live {lv/1e9:.2f} GB ({100*lv/h:.0f}%){extra}")
print(f"held peak after a sweep {peak/1e9:.2f} GB")
