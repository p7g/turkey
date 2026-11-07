//! The reqwest benchmark client: `client URL REQUESTS CONCURRENCY`. Sends
//! REQUESTS GETs to URL from CONCURRENCY tasks over keep-alive, reading each
//! body whole, and prints the requests per second and the latency
//! percentiles in microseconds. `TOKIO_WORKER_THREADS` sets the workers.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    let url = Arc::new(args[1].clone());
    let total: usize = args[2].parse().unwrap();
    let concurrency: usize = args[3].parse().unwrap();
    let client = reqwest::Client::builder()
        .pool_max_idle_per_host(concurrency)
        .http1_only()
        .build()
        .unwrap();
    let next = Arc::new(AtomicUsize::new(0));
    let start = Instant::now();
    let mut tasks = Vec::new();
    for _ in 0..concurrency {
        let (client, url, next) = (client.clone(), url.clone(), next.clone());
        tasks.push(tokio::spawn(async move {
            let mut mine = Vec::new();
            loop {
                if next.fetch_add(1, Ordering::Relaxed) >= total {
                    return mine;
                }
                let t = Instant::now();
                let resp = client.get(url.as_str()).send().await.unwrap();
                assert!(resp.status().is_success());
                resp.bytes().await.unwrap();
                mine.push(t.elapsed().as_micros() as u64);
            }
        }));
    }
    let mut latencies = Vec::with_capacity(total);
    for t in tasks {
        latencies.extend(t.await.unwrap());
    }
    let elapsed = start.elapsed();
    latencies.sort_unstable();
    let at = |p: f64| latencies[(p * (total - 1) as f64) as usize];
    println!(
        "rps={:.0} p50_us={} p99_us={} p999_us={}",
        total as f64 / elapsed.as_secs_f64(),
        at(0.5),
        at(0.99),
        at(0.999)
    );
}
