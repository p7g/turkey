# Runs each built benchmark ROUNDS times, interleaved, and prints the minimum
# and median CPU time (user + sys, from getrusage, so a loaded machine perturbs it
# less than wall time), the output, and --gc-stats allocations and bytes
# (startup included). Optional NAME... restricts the set; BENCH_DIR names
# another directory of built binaries, all of which are run.
# Usage: python3 bench/iter/run.py [ROUNDS]
import glob, os, re, resource, statistics, subprocess, sys
rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
bin_dir = os.environ.get("BENCH_DIR", "build/bi")
names = sorted(os.path.basename(p)[:-4] for p in glob.glob("bench/iter/*.gob"))
if bin_dir != "build/bi":
    names = sorted(os.path.basename(p) for p in glob.glob(bin_dir + "/*") if "." not in os.path.basename(p))
if len(sys.argv) > 2:
    names = [n for n in names if n in sys.argv[2:]]
for n in names:  # one untimed run each, so the first exec's scan is not timed
    subprocess.run([bin_dir + "/" + n], capture_output=True)
times = {n: [] for n in names}
out = {}
for _ in range(rounds):
    for n in names:
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        r = subprocess.run([bin_dir + "/" + n], capture_output=True, text=True)
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        times[n].append(after.ru_utime - before.ru_utime + after.ru_stime - before.ru_stime)
        out[n] = r.stdout.strip() if r.returncode == 0 else "EXIT %d %s" % (r.returncode, r.stderr[:200])
for n in names:
    g = subprocess.run([bin_dir + "/" + n + ".gc"], capture_output=True, text=True).stderr
    m = re.search(r"allocations (\d+), bytes (\d+)", g)
    k = re.search(r"by kind: (.*)", g)
    a, b = (m.group(1), m.group(2)) if m else ("?", "?")
    print("%-9s min %6.3f s  med %6.3f  out %-12s allocs %-10s bytes %-12s %s" % (
        n, min(times[n]), statistics.median(times[n]), out[n], a, b, k.group(1) if k else ""))
