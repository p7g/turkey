-- Reports one wrk run as a line of key=value pairs for run.sh. `-- upload`
-- after wrk's own arguments makes every request a POST of 1 MiB.
function init(args)
    if args[1] == "upload" then
        wrk.method = "POST"
        wrk.body = string.rep("x", 1048576)
        wrk.headers["Content-Type"] = "application/octet-stream"
    end
end

function done(summary, latency, requests)
    local seconds = summary.duration / 1e6
    local e = summary.errors
    io.write(string.format(
        "rps=%.0f p50_us=%d p99_us=%d p999_us=%d mibps=%.1f errors=%d\n",
        summary.requests / seconds,
        latency:percentile(50), latency:percentile(99), latency:percentile(99.9),
        summary.bytes / seconds / 1048576,
        e.connect + e.read + e.write + e.status + e.timeout))
end
