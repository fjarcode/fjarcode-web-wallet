# FJAR Wallet

Browser-based non-custodial wallet for FJAR, built with Django and Electrum.

## Overview

Current wallet features include:
- Create wallet and recover wallet flows
- Optional 12-word or 24-word seed generation during create
- Seed confirmation flow before entering wallet
- Password-protected unlock screen for active wallet sessions
- Optional passkey unlock (Face ID / Touch ID) with password fallback
- Separate Logout and Disconnect actions
- Send, Receive, Transactions, Addresses, Settings, and Status views
- Electrum-backed balance/history and real transaction broadcast
- Receive QR with centered FJAR logo and themed styling

## Security Model

- Non-custodial: users are fully responsible for seed phrase custody
- Seed phrases are not stored in SQL tables
- Wallet flow data is cache-backed with TTL (memory backend by default)
- Session engine uses signed cookies (no seed phrase is stored in session cookie)
- Create and recover require wallet password (minimum 6 chars)
- Passkey enrollment is per-device and optional
- Unlock timeout is configurable (default 15 minutes)
- Session can expire on browser close
- Disconnect clears active wallet flow state and session

## Wallet UX Details

- Create flow supports 12/24 seed selection
- Re-clicking 12 or 24 regenerates a new seed phrase
- Create/recover can optionally continue to Settings for passkey setup
- Send flow supports Low / Medium(Auto) / High fee selection
- Send amount field includes MAX action using spendable confirmed balance
- Coinbase rewards are tracked with maturity awareness
- Immature balance is surfaced in Send and marked in Transactions

## Send Pipeline

The send pipeline is:
1. Prepare
2. Confirm
3. Sign
4. Broadcast (`blockchain.transaction.broadcast`)

On success, network txid is stored and shown in Transactions.

## Project Structure

- `config/` Django settings and URL routes
- `wallet/views.py` main wallet/page flow controllers
- `wallet/services/electrum.py` Electrum RPC integration
- `wallet/services/addresses.py` address/key derivation helpers
- `wallet/services/sender.py` transaction prepare/sign/broadcast helpers
- `templates/` HTML templates
- `static/` CSS, JS, and assets
- `deploy/systemd/` and `deploy/nginx/` deployment templates

## Local Development

```bash
cd /root/fjar_wallet
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py runserver 0.0.0.0:8080
```

## Environment

See `.env.example` for defaults.

Common variables:
- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG`
- `DJANGO_ALLOWED_HOSTS`
- `ENABLE_HTTPS_SECURITY`
- `WALLET_CACHE_TTL_SECONDS`
- `WALLET_UNLOCK_TTL_SECONDS`
- `SESSION_COOKIE_AGE_SECONDS`
- `SESSION_EXPIRE_AT_BROWSER_CLOSE`
- `ELECTRUM_SERVERS`
- `ELECTRUM_TIMEOUT_SECONDS`
- `WALLET_SEND_DEBUG`

## Electrum

Default servers:
- `electrumx03.fjarcode.com:50001:t` (primary local TCP)
- `electrumx01.fjarcode.com:50002:s` (SSL fallback)
- `electrumx02.fjarcode.com:50002:s` (SSL fallback)

Server format:
- `host:port:mode`
- `mode=t` for plain TCP (typical localhost)
- `mode=s` for SSL

Connectivity check:

```bash
cd /root/fjar_wallet
. .venv/bin/activate
python manage.py electrum_ping
```

## Runtime Storage

- Runtime mode is database-free by default
- Wallet flow state is cache-backed (default cache backend is in-memory)
- Sessions are signed cookies
- No wallet/user SQL tables are required for normal operation

Security notes:
- Default cache is `LocMemCache` to avoid writing wallet flow state to server disk.
- If you run multiple Gunicorn workers, each worker has its own in-memory cache.
	For consistent wallet flow state with `LocMemCache`, run a single worker.

### ElectrumX TLS (Let's Encrypt)

For an ElectrumX service on `electrumx03.fjarcode.com:50002`, issue the certificate for the hostname and use it in ElectrumX SSL settings.

Initial certificate issue:

```bash
sudo certbot certonly --standalone --preferred-challenges tls-alpn-01 -d electrumx03.fjarcode.com
```

Common certificate paths:
- `/etc/letsencrypt/live/electrumx03.fjarcode.com/fullchain.pem`
- `/etc/letsencrypt/live/electrumx03.fjarcode.com/privkey.pem`

Enable automatic renewal with restart hook:

```bash
sudo install -m 0755 deploy/systemd/electrumx-cert-deploy.sh /usr/local/sbin/electrumx-cert-deploy.sh
sudo cp deploy/systemd/electrumx-cert-renew.service /etc/systemd/system/
sudo cp deploy/systemd/electrumx-cert-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now electrumx-cert-renew.timer
```

## Deployment

Templates:
- `deploy/systemd/gunicorn.service`
- `deploy/nginx/fjar_wallet.conf`

Typical rollout:
1. Install dependencies
2. Collect static
3. Reload/restart gunicorn
4. Reload/restart nginx

## Disclaimer

This software is non-custodial. If users lose their seed phrase, funds cannot be recovered by the service.
