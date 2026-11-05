// Drives a server with a new connection per request:
// `connload URL CONNECTIONS SECONDS`. CONNECTIONS goroutines each connect,
// over TLS for an https URL, send one GET with `Connection: close`, read the
// response, and close. Prints a line in the format of bench/http/wrk.lua.
//
// wrk cannot measure this on macOS. A connection closed normally leaves its
// address pair in TIME_WAIT for 2 x MSL (30 s on macOS), and on loopback
// that pair holds the client's ephemeral port, so at most 16,384 ports per
// 30 s, about 550 connections per second, get through. Closing with
// SO_LINGER 0 sends a reset instead, which leaves no TIME_WAIT on either
// side. The reset goes out once the whole response has been read, so the
// server has done all its work, its own close included.
package main

import (
	"bufio"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

func main() {
	u, err := url.Parse(os.Args[1])
	if err != nil {
		panic(err)
	}
	conns, _ := strconv.Atoi(os.Args[2])
	seconds, _ := strconv.Atoi(os.Args[3])
	request := []byte("GET " + u.Path + " HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
	config := &tls.Config{InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}}
	var errors atomic.Int64
	var bytes atomic.Int64
	stop := time.Now().Add(time.Duration(seconds) * time.Second)
	results := make([][]time.Duration, conns)
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < conns; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for time.Now().Before(stop) {
				t := time.Now()
				n, err := once(u, request, config)
				if err != nil {
					errors.Add(1)
					continue
				}
				bytes.Add(n)
				results[w] = append(results[w], time.Since(t))
			}
		}(w)
	}
	wg.Wait()
	elapsed := time.Since(start).Seconds()
	var all []time.Duration
	for _, r := range results {
		all = append(all, r...)
	}
	sort.Slice(all, func(a, b int) bool { return all[a] < all[b] })
	at := func(p float64) int64 {
		if len(all) == 0 {
			return 0
		}
		return all[int(p*float64(len(all)-1))].Microseconds()
	}
	fmt.Printf("rps=%.0f p50_us=%d p99_us=%d p999_us=%d mibps=%.1f errors=%d\n",
		float64(len(all))/elapsed, at(0.5), at(0.99), at(0.999),
		float64(bytes.Load())/elapsed/1048576, errors.Load())
}

// Makes one connection, sends one request, reads its response whole, and
// closes with a reset. Returns the body's length.
func once(u *url.URL, request []byte, config *tls.Config) (int64, error) {
	raw, err := net.Dial("tcp", u.Host)
	if err != nil {
		return 0, err
	}
	raw.(*net.TCPConn).SetLinger(0)
	defer raw.Close()
	var c net.Conn = raw
	if u.Scheme == "https" {
		t := tls.Client(raw, config)
		if err := t.Handshake(); err != nil {
			return 0, err
		}
		c = t
	}
	if _, err := c.Write(request); err != nil {
		return 0, err
	}
	resp, err := http.ReadResponse(bufio.NewReader(c), nil)
	if err != nil {
		return 0, err
	}
	n, err := io.Copy(io.Discard, resp.Body)
	if err == nil && resp.StatusCode != 200 {
		err = fmt.Errorf("status %d", resp.StatusCode)
	}
	return n, err
}
