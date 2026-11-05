#!/bin/sh
# Prepare an arm64 Ubuntu 24.04 machine to run bench/http/run.sh: the VM
# that bench/http/lima.yaml describes, or any other. Run it as the user who
# will run the benchmark, from anywhere in this checkout:
#
#   sh bench/http/linux-setup.sh
#
# It installs, with sudo, Ubuntu's wrk, nginx, OpenSSL, GNU time, strace,
# perf and Ruby; and as this user Go, Node and a Temurin JDK under
# ~/toolchains, Rust through rustup, oha through cargo, a Python venv with
# uvicorn, uvloop, httptools and gunicorn, and Puma. The versions match the
# ones the CI workflow uses. Then it clones this checkout's current commit
# into ~/turkey and writes ~/bench-env.sh, which puts all of it on PATH:
#
#   . ~/bench-env.sh && cd ~/turkey && sh scripts/build.sh --fixpoint && sh bench/http/run.sh

set -eu

GO=1.25.6
NODE=23.11.1
JDK=25

SRC=$(cd "$(dirname "$0")/../.." && pwd)
T=$HOME/toolchains
mkdir -p "$T"

sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
    build-essential git curl ca-certificates wrk nginx libssl-dev openssl time strace \
    ruby ruby-dev python3-venv linux-tools-common "linux-tools-$(uname -r)"
sudo systemctl disable --now nginx || true

if [ ! -x "$T/go/bin/go" ]; then
    curl -fsSL "https://go.dev/dl/go$GO.linux-arm64.tar.gz" | tar -xz -C "$T"
fi
if [ ! -x "$T/node/bin/node" ]; then
    mkdir -p "$T/node"
    curl -fsSL "https://nodejs.org/dist/v$NODE/node-v$NODE-linux-arm64.tar.xz" | tar -xJ -C "$T/node" --strip-components 1
fi
if [ ! -x "$T/jdk/bin/java" ]; then
    mkdir -p "$T/jdk"
    curl -fsSL "https://api.adoptium.net/v3/binary/latest/$JDK/ga/linux/aarch64/jdk/hotspot/normal/eclipse" |
        tar -xz -C "$T/jdk" --strip-components 1
fi
if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal
fi
"$HOME/.cargo/bin/cargo" install --locked --quiet oha
if [ ! -x "$HOME/venv/bin/python3" ]; then
    python3 -m venv "$HOME/venv"
fi
"$HOME/venv/bin/pip" install --quiet uvicorn uvloop httptools gunicorn
gem install --user-install --no-document puma

cat > "$HOME/bench-env.sh" <<EOF
export PATH="$T/go/bin:$T/node/bin:$T/jdk/bin:$HOME/.cargo/bin:$HOME/venv/bin:$(ruby -e 'print Gem.user_dir')/bin:\$PATH"
export TURKEY_CC=gcc
ulimit -n 65536
EOF

if [ ! -d "$HOME/turkey" ]; then
    git clone --quiet "$SRC" "$HOME/turkey"
fi
git -C "$HOME/turkey" fetch --quiet "$SRC" HEAD
git -C "$HOME/turkey" checkout --quiet --detach FETCH_HEAD
echo "ready: . ~/bench-env.sh && cd ~/turkey"
