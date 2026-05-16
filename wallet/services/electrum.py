import json
import socket
import ssl
import time
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from typing import Any

from cashaddress.convert import Address
from django.conf import settings


class ElectrumConnectionError(Exception):
    """Raised when Electrum servers are unreachable."""


class ElectrumClient:
    """Minimal Electrum JSON-RPC client with failover across configured servers."""

    def __init__(self, servers=None, timeout=None):
        self.servers = servers or settings.ELECTRUM_SERVERS
        self.timeout = timeout or settings.ELECTRUM_TIMEOUT_SECONDS
        self._request_id = 0

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    def _call_on_server(self, server, method, params):
        host = server['host']
        port = int(server['port'])
        use_ssl = bool(server.get('ssl', True))

        sock = socket.create_connection((host, port), timeout=self.timeout)
        try:
            conn = sock
            if use_ssl:
                context = ssl.create_default_context()
                conn = context.wrap_socket(sock, server_hostname=host)
            conn.settimeout(self.timeout)

            payload = {
                'id': self._next_id(),
                'method': method,
                'params': params,
            }
            message = json.dumps(payload) + '\n'
            conn.sendall(message.encode('utf-8'))

            response = b''
            while b'\n' not in response:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                response += chunk

            if not response:
                raise ElectrumConnectionError(f'Empty response from {host}:{port}')

            line = response.split(b'\n', 1)[0].decode('utf-8', errors='replace')
            data = json.loads(line)

            if data.get('error'):
                raise ElectrumConnectionError(
                    f"Electrum error from {host}:{port}: {data['error']}"
                )

            return data.get('result')
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def call(self, method, params=None):
        if params is None:
            params = []

        if not self.servers:
            raise ElectrumConnectionError('No Electrum servers configured.')

        last_error = None
        for server in self.servers:
            try:
                return self._call_on_server(server, method, params)
            except (OSError, ssl.SSLError, ValueError, json.JSONDecodeError, ElectrumConnectionError) as exc:
                last_error = exc
                continue

        raise ElectrumConnectionError(f'All Electrum servers failed: {last_error}')

    def server_version(self, client_name='fjar-wallet-web', protocol_version='1.4'):
        return self.call('server.version', [client_name, protocol_version])

    def ping(self):
        return self.call('server.ping', [])

    def _cashaddr_to_scripthash(self, cashaddr):
        parsed = Address.from_string(cashaddr)
        hash160 = bytes(parsed.payload)

        if parsed.version.startswith('P2PKH'):
            script = bytes.fromhex('76a914') + hash160 + bytes.fromhex('88ac')
        elif parsed.version.startswith('P2SH'):
            script = bytes.fromhex('a914') + hash160 + bytes.fromhex('87')
        else:
            raise ElectrumConnectionError(f'Unsupported address version: {parsed.version}')

        return sha256(script).digest()[::-1].hex()

    def _cashaddr_to_scriptpubkey_hex(self, cashaddr):
        parsed = Address.from_string(cashaddr)
        hash160 = bytes(parsed.payload)

        if parsed.version.startswith('P2PKH'):
            script = bytes.fromhex('76a914') + hash160 + bytes.fromhex('88ac')
        elif parsed.version.startswith('P2SH'):
            script = bytes.fromhex('a914') + hash160 + bytes.fromhex('87')
        else:
            raise ElectrumConnectionError(f'Unsupported address version: {parsed.version}')

        return script.hex()

    def _to_sats_from_bch_value(self, value):
        try:
            return int(Decimal(str(value)) * Decimal('100000000'))
        except Exception:  # noqa: BLE001
            return 0

    def _normalize_timestamp(self, raw_ts):
        if raw_ts in (None, ''):
            return None
        try:
            ts_int = int(raw_ts)
            if ts_int <= 0:
                return None
            return datetime.fromtimestamp(ts_int, tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    def _timestamp_from_block_header(self, height):
        if int(height) <= 0:
            return None
        try:
            header_hex = self.call('blockchain.block.header', [int(height)])
            if not header_hex or len(header_hex) < 152:
                return None
            # Header bytes 68:72 are block time (little-endian uint32).
            time_le_hex = header_hex[136:144]
            ts_int = int.from_bytes(bytes.fromhex(time_le_hex), byteorder='little', signed=False)
            if ts_int <= 0:
                return None
            return datetime.fromtimestamp(ts_int, tz=timezone.utc)
        except Exception:  # noqa: BLE001
            return None

    def _get_chain_height(self):
        try:
            payload = self.call('blockchain.headers.subscribe', [])
            return int((payload or {}).get('height', 0))
        except Exception:  # noqa: BLE001
            return 0

    def get_balance_for_cashaddr(self, cashaddr):
        scripthash = self._cashaddr_to_scripthash(cashaddr)
        result = self.call('blockchain.scripthash.get_balance', [scripthash])
        return {
            'confirmed': int(result.get('confirmed', 0)),
            'unconfirmed': int(result.get('unconfirmed', 0)),
        }

    def get_history_for_cashaddr(self, cashaddr):
        scripthash = self._cashaddr_to_scripthash(cashaddr)
        target_script = self._cashaddr_to_scriptpubkey_hex(cashaddr)
        result = self.call('blockchain.scripthash.get_history', [scripthash])
        chain_height = self._get_chain_height()

        if chain_height <= 0:
            confirmed_heights = [int(item.get('height', 0)) for item in (result or []) if int(item.get('height', 0)) > 0]
            chain_height = max(confirmed_heights) if confirmed_heights else 0

        history = []
        for item in result or []:
            height = int(item.get('height', 0))
            tx_hash = item.get('tx_hash', '')
            confirmations = (chain_height - height + 1) if (height > 0 and chain_height >= height) else 0

            amount_sats = 0
            tx_time = None
            is_coinbase = False
            try:
                verbose_tx = self.call('blockchain.transaction.get', [tx_hash, True])
                tx_time = self._normalize_timestamp(verbose_tx.get('blocktime') or verbose_tx.get('time'))

                for vin in verbose_tx.get('vin', []) or []:
                    if isinstance(vin, dict) and vin.get('coinbase'):
                        is_coinbase = True
                        break

                for out in verbose_tx.get('vout', []) or []:
                    script = ((out.get('scriptPubKey') or {}).get('hex') or '').lower()
                    if script == target_script:
                        amount_sats += self._to_sats_from_bch_value(out.get('value', 0))
            except Exception:  # noqa: BLE001
                tx_time = None

            if tx_time is None and height > 0:
                tx_time = self._timestamp_from_block_header(height)

            history.append(
                {
                    'tx_hash': tx_hash,
                    'height': height,
                    'confirmations': confirmations,
                    'status': 'unconfirmed' if height <= 0 else 'confirmed',
                    'amount_sats': amount_sats,
                    'amount_fjar': self._format_fjar(amount_sats),
                    'timestamp': tx_time,
                    'is_coinbase': is_coinbase,
                }
            )

        # Show newest first: unconfirmed first, then highest block height.
        history.sort(key=lambda tx: (tx['height'] > 0, -tx['height']))
        return history

    def _format_fjar(self, sats):
        value = Decimal(int(sats or 0)) / Decimal('100000000')
        formatted = format(value, 'f')
        if '.' in formatted:
            formatted = formatted.rstrip('0').rstrip('.')
        return formatted or '0'

    def list_unspent_for_cashaddr(
        self,
        cashaddr,
        *,
        min_confirmations=0,
        exclude_immature_coinbase=True,
        coinbase_maturity_confirmations=100,
    ):
        scripthash = self._cashaddr_to_scripthash(cashaddr)
        result = self.call('blockchain.scripthash.listunspent', [scripthash])
        script_hex = self._cashaddr_to_scriptpubkey_hex(cashaddr)
        chain_height = self._get_chain_height()
        coinbase_cache = {}

        utxos = []
        for item in result or []:
            txid = item.get('tx_hash', '')
            height = int(item.get('height', 0) or 0)
            confirmations = (chain_height - height + 1) if (height > 0 and chain_height >= height) else 0

            if confirmations < int(min_confirmations):
                continue

            if exclude_immature_coinbase and txid:
                is_coinbase = coinbase_cache.get(txid)
                if is_coinbase is None:
                    is_coinbase = False
                    try:
                        verbose_tx = self.call('blockchain.transaction.get', [txid, True])
                        for vin in verbose_tx.get('vin', []) or []:
                            if isinstance(vin, dict) and vin.get('coinbase'):
                                is_coinbase = True
                                break
                    except Exception:  # noqa: BLE001
                        is_coinbase = False
                    coinbase_cache[txid] = is_coinbase

                if is_coinbase and confirmations < int(coinbase_maturity_confirmations):
                    continue

            utxos.append(
                {
                    'txid': txid,
                    'vout': int(item.get('tx_pos', 0)),
                    'value': int(item.get('value', 0)),
                    'height': height,
                    'confirmations': confirmations,
                    'script_pubkey': script_hex,
                }
            )

        return utxos

    def broadcast_transaction(self, tx_hex):
        return self.call('blockchain.transaction.broadcast', [tx_hex])

    def _format_server_version(self, value):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            parts = [str(item).strip() for item in value if str(item).strip()]
            return ' / '.join(parts) if parts else None
        return str(value)

    def probe_servers(self):
        """Probe all configured Electrum servers and return connectivity details."""
        results = []
        for index, server in enumerate(self.servers):
            host = server['host']
            port = int(server['port'])
            is_backup = index > 0
            role = 'backup' if is_backup else 'primary'
            try:
                version = self._call_on_server(server, 'server.version', ['fjar-wallet-web', '1.4'])
                ping_started = time.perf_counter()
                self._call_on_server(server, 'server.ping', [])
                latency_ms = round((time.perf_counter() - ping_started) * 1000, 1)
                results.append(
                    {
                        'host': host,
                        'port': port,
                        'ok': True,
                        'role': role,
                        'is_backup': is_backup,
                        'latency_ms': latency_ms,
                        'version': self._format_server_version(version),
                        'error': '',
                    }
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {
                        'host': host,
                        'port': port,
                        'ok': False,
                        'role': role,
                        'is_backup': is_backup,
                        'latency_ms': None,
                        'version': None,
                        'error': str(exc),
                    }
                )

        return results
