"""Prints oha's JSON report as a line in the format of bench/http/wrk.lua.

    python3 bench/http/oha.py REPORT.json

Requests oha cut off at the end of its duration are not errors; every other
entry in its error distribution, and every response other than 200, is.
"""

import json
import sys

report = json.load(open(sys.argv[1]))
summary = report["summary"]
latency = report["latencyPercentiles"]
statuses = report.get("statusCodeDistribution", {})
errors = sum(n for status, n in statuses.items() if status != "200")
errors += sum(n for kind, n in report.get("errorDistribution", {}).items() if kind != "aborted due to deadline")


def micros(seconds):
    return int((seconds or 0) * 1e6)


print(
    f"rps={summary['requestsPerSec']:.0f} p50_us={micros(latency['p50'])} p99_us={micros(latency['p99'])}"
    f" p999_us={micros(latency['p99.9'])} mibps={summary['sizePerSec'] / 1048576:.1f} errors={errors}"
)
