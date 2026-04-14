#!/usr/bin/env bash
set -euo pipefail

src_dir="/etc/letsencrypt/live/electrumx03.fjarcode.com"
dst_dir="/etc/electrumx/certs"

install -d -m 0755 "$dst_dir"
install -m 0644 "$src_dir/fullchain.pem" "$dst_dir/fullchain.pem"
install -m 0640 -o root -g electrumx "$src_dir/privkey.pem" "$dst_dir/privkey.pem"

systemctl restart electrumx
