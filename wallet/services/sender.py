import json
from decimal import Decimal

from bitcash import PrivateKey
from bitcash.network.meta import Unspent
from bitcash.transaction import calc_txid

from wallet.services.addresses import derive_fjar_signing_material, to_bitcoincash_cashaddr
from wallet.services.electrum import ElectrumClient, ElectrumConnectionError


class WalletSendError(Exception):
    """Raised when transaction signing or broadcast fails."""


DUST_LIMIT_SATS = 546


def _resolve_signing_material(seed_phrase, from_fjar_address, max_scan=32):
    for idx in range(max_scan):
        material = derive_fjar_signing_material(seed_phrase, index=idx)
        if material['fjarcode_address'] == from_fjar_address:
            return material
    raise WalletSendError('Signing key does not match selected source address.')


def _to_bitcash_unspent(entry):
    confirmations = int(entry.get('confirmations', 0) or 0)
    return Unspent(
        amount=int(entry['value']),
        confirmations=confirmations,
        script=entry['script_pubkey'],
        txid=entry['txid'],
        txindex=int(entry['vout']),
    )


def prepare_send_preview(seed_phrase, from_fjar_address, to_fjar_address, amount_fjar, fee_rate_sat_vb):
    try:
        material = _resolve_signing_material(seed_phrase, from_fjar_address)

        client = ElectrumClient()
        utxos = client.list_unspent_for_cashaddr(
            from_fjar_address,
            min_confirmations=1,
            exclude_immature_coinbase=False,
        )
        if not utxos:
            raise WalletSendError('No spendable UTXOs found for source address.')

        key = PrivateKey.from_hex(material['private_key_hex'])
        from_bch = material['bitcoincash_address']
        to_bch = to_bitcoincash_cashaddr(to_fjar_address)
        outputs = [(to_bch, str(amount_fjar), 'bch')]
        unspents = [_to_bitcash_unspent(item) for item in utxos]

        prepared = PrivateKey.prepare_transaction(
            from_bch,
            outputs,
            fee=int(fee_rate_sat_vb),
            leftover=from_bch,
            combine=False,
            unspents=unspents,
        )

        data = json.loads(prepared)
        dust_outputs = [
            int(item[1])
            for item in data.get('outputs', [])
            if len(item) > 1 and 0 < int(item[1]) < DUST_LIMIT_SATS
        ]
        if dust_outputs:
            raise WalletSendError(
                'Amount creates a dust output. Lower the amount slightly and try again.'
            )

        input_total = sum(int(item.get('amount', 0)) for item in data.get('unspents', []))
        output_total = sum(int(item[1]) for item in data.get('outputs', []))
        fee_sats = max(input_total - output_total, 0)

        return {
            'prepared': prepared,
            'fee_sats': fee_sats,
            'selected_inputs': len(data.get('unspents', [])),
            'input_total_sats': input_total,
            'output_total_sats': output_total,
        }
    except WalletSendError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WalletSendError(f'Prepare send failed: {exc}') from exc


def sign_and_broadcast(seed_phrase, from_fjar_address, prepared):
    try:
        material = _resolve_signing_material(seed_phrase, from_fjar_address)

        key = PrivateKey.from_hex(material['private_key_hex'])
        tx_hex = key.sign_transaction(prepared)
        txid_local = calc_txid(tx_hex)

        client = ElectrumClient()
        try:
            txid_network = client.broadcast_transaction(tx_hex)
        except ElectrumConnectionError as exc:
            raise WalletSendError(str(exc)) from exc

        return {
            'tx_hex': tx_hex,
            'txid_local': txid_local,
            'txid_network': txid_network or txid_local,
        }
    except WalletSendError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WalletSendError(f'Sign/broadcast failed: {exc}') from exc
