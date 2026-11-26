//! The hyper benchmark server: `server PORT [CERT KEY]`, on tokio's
//! multi-threaded runtime, whose worker count `TOKIO_WORKER_THREADS` sets.
//! Serves the routes bench/http/turkey/Server.gob lists, over HTTP/1.1.

use std::convert::Infallible;
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt, Full};
use hyper::body::{Body, Frame, Incoming};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use tokio::net::TcpListener;

const PIECE: usize = 16384;
const PIECES: usize = 64;

type Out = BoxBody<Bytes, Infallible>;

/// A body of `left` more copies of `chunk`, with no declared length, so
/// hyper sends it chunked.
struct Pieces {
    chunk: Bytes,
    left: usize,
}

impl Body for Pieces {
    type Data = Bytes;
    type Error = Infallible;
    fn poll_frame(mut self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<Option<Result<Frame<Bytes>, Infallible>>> {
        if self.left == 0 {
            return Poll::Ready(None);
        }
        self.left -= 1;
        Poll::Ready(Some(Ok(Frame::data(self.chunk.clone()))))
    }
}

struct Statics {
    chunk: Bytes,
    mebibyte: Bytes,
}

async fn handle(req: Request<Incoming>, s: Arc<Statics>) -> Result<Response<Out>, Infallible> {
    let full = |status, kind: &'static str, b: Bytes| {
        Response::builder()
            .status(status)
            .header("content-type", kind)
            .body(Full::new(b).boxed())
            .unwrap()
    };
    Ok(match (req.method(), req.uri().path()) {
        (&Method::GET, "/hello") => full(StatusCode::OK, "text/plain", Bytes::from_static(b"Hello, world!\n")),
        (&Method::GET, "/1m") => full(StatusCode::OK, "application/octet-stream", s.mebibyte.clone()),
        (&Method::POST, "/upload") => {
            let mut body = req.into_body();
            let mut read = 0usize;
            let mut failed = false;
            while let Some(frame) = body.frame().await {
                match frame {
                    Ok(f) => {
                        if let Some(d) = f.data_ref() {
                            read += d.len();
                        }
                        if read > 2 << 20 {
                            failed = true;
                            break;
                        }
                    }
                    Err(_) => {
                        failed = true;
                        break;
                    }
                }
            }
            if failed {
                full(StatusCode::BAD_REQUEST, "text/plain", Bytes::new())
            } else {
                full(StatusCode::OK, "text/plain", Bytes::from_static(b"ok\n"))
            }
        }
        (&Method::GET, "/stream") => Response::builder()
            .header("content-type", "application/octet-stream")
            .body(Pieces { chunk: s.chunk.clone(), left: PIECES }.boxed())
            .unwrap(),
        _ => full(StatusCode::NOT_FOUND, "text/plain", Bytes::new()),
    })
}

fn tls_config(cert: &str, key: &str) -> Arc<rustls::ServerConfig> {
    let certs = rustls_pemfile::certs(&mut std::io::BufReader::new(std::fs::File::open(cert).unwrap()))
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    let key = rustls_pemfile::private_key(&mut std::io::BufReader::new(std::fs::File::open(key).unwrap()))
        .unwrap()
        .unwrap();
    let mut c = rustls::ServerConfig::builder().with_no_client_auth().with_single_cert(certs, key).unwrap();
    c.alpn_protocols = vec![b"http/1.1".to_vec()];
    Arc::new(c)
}

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 2 && args.len() != 4 {
        panic!("usage: server PORT [CERT KEY]");
    }
    let statics = Arc::new(Statics {
        chunk: Bytes::from(vec![b'x'; PIECE]),
        mebibyte: Bytes::from(vec![b'x'; PIECE * PIECES]),
    });
    let tls = if args.len() == 4 {
        Some(tokio_rustls::TlsAcceptor::from(tls_config(&args[2], &args[3])))
    } else {
        None
    };
    let listener = TcpListener::bind(format!("127.0.0.1:{}", args[1])).await.unwrap();
    loop {
        let (stream, _) = match listener.accept().await {
            Ok(x) => x,
            Err(_) => continue,
        };
        let _ = stream.set_nodelay(true);
        let s = statics.clone();
        let tls = tls.clone();
        tokio::spawn(async move {
            let service = service_fn(move |req| handle(req, s.clone()));
            match tls {
                None => {
                    let _ = http1::Builder::new().serve_connection(TokioIo::new(stream), service).await;
                }
                Some(acceptor) => {
                    if let Ok(t) = acceptor.accept(stream).await {
                        let _ = http1::Builder::new().serve_connection(TokioIo::new(t), service).await;
                    }
                }
            }
        });
    }
}
