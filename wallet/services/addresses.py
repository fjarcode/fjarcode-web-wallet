from bip_utils import Bip39SeedGenerator, Bip44, Bip44Changes, Bip44Coins
from cashaddress.convert import Address


def _to_fjarcode_cashaddr(bitcoincash_addr):
    parsed = Address.from_string(bitcoincash_addr)
    custom = Address(parsed.version, parsed.payload, prefix='fjarcode')
    return custom.cash_address()


def to_bitcoincash_cashaddr(addr):
    parsed = Address.from_string(addr)
    converted = Address(parsed.version, parsed.payload, prefix='bitcoincash')
    return converted.cash_address()


def to_fjarcode_cashaddr(addr):
    parsed = Address.from_string(addr)
    converted = Address(parsed.version, parsed.payload, prefix='fjarcode')
    return converted.cash_address()


def derive_fjar_addresses(seed_phrase, count=10):
    """Derive deterministic FJAR cashaddr addresses from BIP39 seed phrase."""
    seed_bytes = Bip39SeedGenerator(seed_phrase).Generate()
    bip44 = Bip44.FromSeed(seed_bytes, Bip44Coins.BITCOIN_CASH)
    ext = bip44.Purpose().Coin().Account(0).Change(Bip44Changes.CHAIN_EXT)

    addrs = []
    for idx in range(count):
        node = ext.AddressIndex(idx)
        bch_addr = node.PublicKey().ToAddress()
        addrs.append(
            {
                'index': idx,
                'address': _to_fjarcode_cashaddr(bch_addr),
            }
        )

    return addrs


def derive_fjar_signing_material(seed_phrase, index=0):
    """Derive address + private key (hex) for signing from BIP44 BCH path."""
    seed_bytes = Bip39SeedGenerator(seed_phrase).Generate()
    node = (
        Bip44.FromSeed(seed_bytes, Bip44Coins.BITCOIN_CASH)
        .Purpose()
        .Coin()
        .Account(0)
        .Change(Bip44Changes.CHAIN_EXT)
        .AddressIndex(index)
    )

    bch_address = node.PublicKey().ToAddress()
    return {
        'index': index,
        'bitcoincash_address': bch_address,
        'fjarcode_address': _to_fjarcode_cashaddr(bch_address),
        'private_key_hex': node.PrivateKey().Raw().ToHex(),
    }
