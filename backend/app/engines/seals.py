import hashlib


def merkle_root(leaves):
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256(bytes.fromhex(level[i]) + bytes.fromhex(level[i + 1])).hexdigest()
            for i in range(0, len(level), 2)
        ]
    return level[0]


def merkle_proof(leaves, index):
    if index < 0 or index >= len(leaves):
        raise ValueError("Invalid evidence position")
    path, level = [], list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sibling = index ^ 1
        path.append({"side": "left" if sibling < index else "right", "hash": level[sibling]})
        level = [
            hashlib.sha256(bytes.fromhex(level[i]) + bytes.fromhex(level[i + 1])).hexdigest()
            for i in range(0, len(level), 2)
        ]
        index //= 2
    return path


def verify_proof(leaf, path, root):
    try:
        current = bytes.fromhex(leaf)
        for item in path:
            sibling = bytes.fromhex(item["hash"])
            if len(sibling) != 32 or item["side"] not in {"left", "right"}:
                return False
            current = hashlib.sha256(
                sibling + current if item["side"] == "left" else current + sibling
            ).digest()
        return current.hex() == root
    except (ValueError, KeyError, TypeError):
        return False
