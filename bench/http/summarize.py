"""Prints a Markdown table per scenario from run.sh's results: for each
implementation and connection count, the median of the repetitions and, in
brackets, their range.

    python3 bench/http/summarize.py [build/bench/http/results.tsv]
"""

import statistics
import sys
from collections import defaultdict

COLUMNS = {
    "rps": "req/s",
    "mibps": "MiB/s",
    "p50_us": "p50 µs",
    "p99_us": "p99 µs",
    "p999_us": "p99.9 µs",
    "rss_kb": "peak RSS MiB",
    "cpu_us": "CPU µs/req",
    "before_kb": "RSS after 1 req, MiB",
    "held_kb": "RSS with 10k idle, MiB",
    "errors": "errors",
}
ORDER = ["turkey", "go", "rust", "node", "nginx", "java", "asgi", "wsgi", "ruby", "curl"]


def fields(text):
    out = {}
    for part in text.split():
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k] = float(v)
            except ValueError:
                pass
    return out


def cell(key, values):
    if key.endswith("_kb"):
        values = [v / 1024 for v in values]
    med = statistics.median(values)
    lo, hi = min(values), max(values)
    digits = 1 if key in ("mibps", "cpu_us") or key.endswith("_kb") else 0
    fmt = f"{{:,.{digits}f}}"
    if lo == hi:
        return fmt.format(med)
    return f"{fmt.format(med)} [{fmt.format(lo)}–{fmt.format(hi)}]"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "build/bench/http/results.tsv"
    runs = defaultdict(list)
    noisy = defaultdict(int)
    for line in open(path):
        parts = line.rstrip("\n").split("\t")
        scenario, impl, conns = parts[:3]
        r = fields(" ".join(parts[4:]))
        # An idle run whose client could not open every connection
        # measured fewer than it says.
        if scenario == "idle" and r.get("ready") == 0:
            continue
        # An upload's bytes go to the server, and wrk counts only what it
        # reads, so its throughput is one MiB per request.
        if scenario == "upload" and "rps" in r:
            r["mibps"] = r["rps"]
        # The server's CPU time over the run, per request it served.
        if r.get("cpu_s") and r.get("rps") and r.get("secs"):
            r["cpu_us"] = r["cpu_s"] * 1e6 / (r["rps"] * r["secs"])
        runs[(scenario, impl, int(conns))].append(r)
        if parts[-1] == "noisy":
            noisy[(scenario, impl, int(conns))] += 1
    scenarios = []
    for scenario, _, _ in runs:
        if scenario not in scenarios:
            scenarios.append(scenario)
    for scenario in scenarios:
        keys = [(s, i, c) for (s, i, c) in runs if s == scenario]
        keys.sort(key=lambda k: (k[2], ORDER.index(k[1]) if k[1] in ORDER else 99))
        present = [c for c in COLUMNS if any(c in r for k in keys for r in runs[k])]
        if scenario not in ("download", "upload", "stream"):
            present = [c for c in present if c != "mibps"]
        if all(r.get("errors", 0) == 0 for k in keys for r in runs[k]):
            present = [c for c in present if c != "errors"]
        print(f"### {scenario}\n")
        print("| impl | conns | " + " | ".join(COLUMNS[c] for c in present) + " |")
        print("|---|---:|" + "---:|" * len(present))
        for k in keys:
            rs = runs[k]
            cells = [cell(c, [r[c] for r in rs if c in r]) if any(c in r for r in rs) else "" for c in present]
            mark = f" ({noisy[k]} noisy)" if noisy[k] else ""
            print(f"| {k[1]}{mark} | {k[2]} | " + " | ".join(cells) + " |")
        print()


main()
