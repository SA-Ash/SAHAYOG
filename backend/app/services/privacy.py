import hashlib
import hmac
import secrets

from nacl.bindings import (
    crypto_core_ed25519_from_uniform,
    crypto_core_ed25519_is_valid_point,
    crypto_core_ed25519_scalar_reduce,
    crypto_scalarmult_ed25519_noclamp,
)

from app.services.validation import address_info


def identity(chain, address):
    return f"{chain}:{address_info(chain, address)[0]}".encode()


def salted_hash(salt, chain, address):
    return hmac.new(bytes.fromhex(salt), identity(chain, address), hashlib.sha256).hexdigest()


def scalar():
    return crypto_core_ed25519_scalar_reduce(secrets.token_bytes(64))


def point(chain, address):
    return crypto_core_ed25519_from_uniform(hashlib.sha256(identity(chain, address)).digest())


def multiply(secret, value):
    value = bytes.fromhex(value) if isinstance(value, str) else value
    if len(value) != 32 or not crypto_core_ed25519_is_valid_point(value):
        raise ValueError("Invalid PSI point")
    return crypto_scalarmult_ed25519_noclamp(secret, value).hex()
