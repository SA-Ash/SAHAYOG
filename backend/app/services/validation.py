import re

import base58
from eth_utils import is_checksum_address

from app.models.entities import Chain


def bech32_valid(address: str) -> bool:
    """BIP-173/BIP-350 witness address checksum and witness-program validation."""
    if address.lower() != address and address.upper() != address:
        return False
    address = address.lower()
    if len(address) > 90 or not address.startswith("bc1"):
        return False
    charset = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    hrp, encoded = address.rsplit("1", 1)
    if len(encoded) < 7 or any(c not in charset for c in encoded):
        return False
    values = [charset.index(c) for c in encoded]
    chk = 1
    for value in [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, generator in enumerate([0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]):
            if (top >> i) & 1:
                chk ^= generator
    version = values[0]
    if version > 16 or chk != (1 if version == 0 else 0x2BC830A3):
        return False
    acc = bits = 0
    program = []
    for value in values[1:-6]:
        acc = (acc << 5) | value
        bits += 5
        while bits >= 8:
            bits -= 8
            program.append((acc >> bits) & 255)
    if bits >= 5 or (acc << (8 - bits)) & 255:
        return False
    return 2 <= len(program) <= 40 and (version != 0 or len(program) in {20, 32})


def address_info(chain: Chain | str, value: str) -> tuple[str, bool | None]:
    if chain in {"ethereum", "bnb", "polygon"}:
        if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
            raise ValueError("Invalid EVM address length or alphabet")
        body = value[2:]
        # Unchecksummed lower/upper addresses are valid inputs, but not checksum-verified.
        checked = is_checksum_address(value)
        if not checked and body != body.lower() and body != body.upper():
            raise ValueError("Invalid EIP-55 checksum")
        return value.lower(), True if checked else None
    if chain == "bitcoin" and value.lower().startswith("bc1"):
        if not bech32_valid(value):
            raise ValueError("Invalid Bitcoin bech32/bech32m address")
        return value.lower(), True
    try:
        payload = base58.b58decode_check(value)
    except ValueError as exc:
        raise ValueError("Invalid Base58Check checksum") from exc
    prefix = {0x41} if chain == "tron" else {0x00, 0x05}
    if len(payload) != 21 or payload[0] not in prefix:
        raise ValueError("Address is not on the selected chain's mainnet")
    return value, True


def normalize_hash(chain: Chain | str, value: str) -> str:
    if not re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", value):
        raise ValueError("Transaction hash must contain exactly 64 hexadecimal characters")
    raw = value.removeprefix("0x").lower()
    return "0x" + raw if chain in {"ethereum", "bnb", "polygon"} else raw
