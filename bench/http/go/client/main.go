// The Go net/http benchmark client: `client URL REQUESTS CONCURRENCY`.
// Sends REQUESTS GETs to URL from CONCURRENCY goroutines over keep-alive,
// reading each body whole, and prints the requests per second and the
// latency percentiles in microseconds.
package main

import (
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

func main() {
	url := os.Args[1]
	total, _ := strconv.Atoi(os.Args[2])
	concurrency, _ := strconv.Atoi(os.Args[3])
	transport := &http.Transport{
		MaxIdleConns:        concurrency,
		MaxIdleConnsPerHost: concurrency,
		ForceAttemptHTTP2:   false,
	}
	client := &http.Client{Transport: transport}
	latencies := make([]time.Duration, total)
	var next atomic.Int64
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < concurrency; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				i := int(next.Add(1)) - 1
				if i >= total {
					return
				}
				t := time.Now()
				resp, err := client.Get(url)
				if err != nil {
					log.Fatal(err)
				}
				io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
				if resp.StatusCode != 200 {
					log.Fatal(resp.Status)
				}
				latencies[i] = time.Since(t)
			}
		}()
	}
	wg.Wait()
	elapsed := time.Since(start)
	sort.Slice(latencies, func(a, b int) bool { return latencies[a] < latencies[b] })
	at := func(p float64) int64 { return latencies[int(p*float64(total-1))].Microseconds() }
	fmt.Printf("rps=%.0f p50_us=%d p99_us=%d p999_us=%d\n",
		float64(total)/elapsed.Seconds(), at(0.5), at(0.99), at(0.999))
}
