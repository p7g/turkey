// Opens N keep-alive connections to a server, sends GET /hello on each and
// reads the response, then holds them all open, idle, for SECONDS:
// `holdconns HOST:PORT N SECONDS`. Prints "ready" once every connection has
// had its response.
package main

import (
	"bufio"
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
	conns := make([]net.Conn, n)
	var wg sync.WaitGroup
	sem := make(chan struct{}, 64)
	for i := 0; i < n; i++ {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int) {
			defer wg.Done()
			defer func() { <-sem }()
			c, err := net.Dial("tcp", os.Args[1])
			if err != nil {
				log.Fatal(err)
			}
			fmt.Fprintf(c, "GET /hello HTTP/1.1\r\nHost: localhost\r\n\r\n")
			resp, err := http.ReadResponse(bufio.NewReader(c), nil)
			if err != nil {
				log.Fatal(err)
			}
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
			conns[i] = c
		}(i)
	}
	wg.Wait()
	fmt.Println("ready")
	seconds, _ := strconv.Atoi(os.Args[3])
	time.Sleep(time.Duration(seconds) * time.Second)
}
