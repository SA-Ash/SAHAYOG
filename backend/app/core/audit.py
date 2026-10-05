def audit(actor, action: str, payload: dict) -> None:
    """Task 10 integration seam. Intentionally no persistence or integrity claims yet.

    Do not pass passwords, TOTP secrets, cookies, or raw screenshot bytes here.
    Task 10 replaces this function with append-only, hash-chained persistence.
    """
