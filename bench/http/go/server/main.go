// The Go net/http benchmark server: `server PORT [CERT KEY]`. Serves the
// routes bench/http/turkey/Server.gob lists. HTTP/2 is turned off, so a TLS
// client gets HTTP/1.1 as from every other server here.
package main

import (
	"bytes"
	"crypto/tls"
	"io"
	"log"
	"net/http"
	"os"
)

const piece = 16384
const pieces = 64

func main() {
	if len(os.Args) != 2 && len(os.Args) != 4 {
		log.Fatal("usage: server PORT [CERT KEY]")
	}
	hello := []byte("Hello, world!\n")
	chunk := bytes.Repeat([]byte("x"), piece)
	mebibyte := bytes.Repeat([]byte("x"), piece*pieces)

	mux := http.NewServeMux()
	mux.HandleFunc("GET /hello", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		w.Write(hello)
	})
	mux.HandleFunc("GET /1m", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Write(mebibyte)
	})
	mux.HandleFunc("POST /upload", func(w http.ResponseWriter, r *http.Request) {
		buf := make([]byte, 65536)
		_, err := io.CopyBuffer(io.Discard, io.LimitReader(r.Body, 2<<20), buf)
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "text/plain")
		w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("GET /stream", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		for i := 0; i < pieces; i++ {
			if _, err := w.Write(chunk); err != nil {
				return
			}
		}
	})

	s := &http.Server{
		Addr:         "127.0.0.1:" + os.Args[1],
		Handler:      mux,
		TLSNextProto: map[string]func(*http.Server, *tls.Conn, http.Handler){},
	}
	if len(os.Args) == 2 {
		log.Fatal(s.ListenAndServe())
	}
	log.Fatal(s.ListenAndServeTLS(os.Args[2], os.Args[3]))
}
