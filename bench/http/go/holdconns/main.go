// Opens N keep-alive connections to a server, over TLS if the fourth
// argument is `tls`, sends GET /hello on each and reads the response, then
// holds them all open, idle, for SECONDS: `holdconns HOST:PORT N SECONDS
// [tls]`. Prints "ready" once every connection has had its response.
//
// Then closes them all with a reset (SO_LINGER 0). A normal close would
// leave N address pairs in TIME_WAIT for 30 s on macOS, holding N of its
// 16,384 ephemeral ports, and a second run within that time could not open
// its own N.
package main

import (
	"bufio"
	"crypto/tls"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"
)

func main() {
	n, _ := strconv.Atoi(os.Args[2])
	conns := make([]*net.TCPConn, n)
	var wg sync.WaitGroup
	sem := make(chan struct{}, 64)
	for i := 0; i < n; i++ {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int) {
			defer wg.Done()
			defer func() { <-sem }()
			raw, err := net.Dial("tcp", os.Args[1])
			if err != nil {
				log.Fatal(err)
			}
			var c net.Conn = raw
			if len(os.Args) > 4 && os.Args[4] == "tls" {
				t := tls.Client(raw, &tls.Config{InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}})
				if err := t.Handshake(); err != nil {
					log.Fatal(err)
				}
				c = t
			}
			fmt.Fprintf(c, "GET /hello HTTP/1.1\r\nHost: localhost\r\n\r\n")
			resp, err := http.ReadResponse(bufio.NewReader(c), nil)
			if err != nil {
				log.Fatal(err)
			}
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			conns[i] = raw.(*net.TCPConn)
		}(i)
	}
	wg.Wait()
	fmt.Println("ready")
	seconds, _ := strconv.Atoi(os.Args[3])
	time.Sleep(time.Duration(seconds) * time.Second)
	for _, c := range conns {
		c.SetLinger(0)
		c.Close()
	}
}
