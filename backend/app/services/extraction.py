import hashlib
import io
import re
import uuid
import warnings

from PIL import Image, UnidentifiedImageError

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import AppError
from app.models.entities import CaseAttachment
from app.services.validation import address_info

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 12_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
PATTERNS = {
    "tx_hash": r"(?<![a-zA-Z0-9])(?:0x)?[a-fA-F0-9]{64}(?![a-zA-Z0-9])",
    "evm_address": r"(?<![a-zA-Z0-9])0x[a-fA-F0-9]{40}(?![a-zA-Z0-9])",
    "tron_address": r"(?<![a-zA-Z0-9])T[1-9A-HJ-NP-Za-km-z]{33}(?![a-zA-Z0-9])",
    "bitcoin_address": r"(?<![a-zA-Z0-9])(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[ac-hj-np-z02-9]{11,87})(?![a-zA-Z0-9])",
    "amount": r"(?<![a-zA-Z0-9])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:USDT|USDC|ETH|BTC|TRX|BNB)(?![a-zA-Z0-9])",
}


def extract_candidates(text: str, confidence: float, source: str):
    candidates = []
    for kind, pattern in PATTERNS.items():
        for match in re.finditer(pattern, text, flags=re.IGNORECASE if kind in {"amount", "bitcoin_address"} else 0):
            value = match.group().strip()
            checksum = None
            if kind.endswith("address"):
                chain = {"evm_address": "ethereum", "tron_address": "tron", "bitcoin_address": "bitcoin"}[kind]
                try:
                    _, checksum = address_info(chain, value)
                except ValueError:
                    checksum = False
            candidates.append({"type": kind, "value": value, "checksum_ok": checksum, "confidence": round(max(0, min(1, confidence)), 3), "source": source, "evidence": [{"signal": "qr_decode" if source == "qr" else "ocr_regex", "note": "Officer confirmation required; confidence is a heuristic, not calibrated"}]})
    return candidates


def extract_image(raw: bytes):
    if not raw or len(raw) > MAX_BYTES:
        raise AppError("IMAGE_TOO_LARGE", "Upload a PNG/JPEG image no larger than 5 MB", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
            if image.format not in {"PNG", "JPEG"}:
                raise AppError("INVALID_IMAGE", "Only PNG and JPEG images are supported", 415)
            if image.width * image.height > MAX_PIXELS:
                raise AppError("IMAGE_TOO_LARGE", "Image exceeds the 12 megapixel limit", 413)
            image.load()
            image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AppError("INVALID_IMAGE", "Image is corrupt or too large", 422) from exc
    import cv2
    import numpy as np
    import pytesseract

    grayscale = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    threshold = cv2.adaptiveThreshold(grayscale, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
    candidates, notices = [], []
    try:
        words = pytesseract.image_to_data(threshold, config="--psm 6", output_type=pytesseract.Output.DICT, timeout=15)
        lines = {}
        for i, word in enumerate(words["text"]):
            if word.strip():
                key = (words["block_num"][i], words["par_num"][i], words["line_num"][i])
                lines.setdefault(key, []).append((word, max(0, float(words["conf"][i])) / 100))
        for line in lines.values():
            candidates.extend(extract_candidates(" ".join(w for w, _ in line), sum(c for _, c in line) / len(line), "ocr"))
    except (pytesseract.TesseractNotFoundError, RuntimeError):
        notices.append("OCR unavailable or timed out; install Tesseract or retry a clearer image")
    try:
        from pyzbar.pyzbar import decode
        for symbol in decode(image):
            candidates.extend(extract_candidates(symbol.data.decode("utf-8", errors="replace"), 1.0, "qr"))
    except (ImportError, OSError):
        notices.append("QR decoding unavailable; install the libzbar system library")
    unique = {}
    for item in sorted(candidates, key=lambda c: c["confidence"], reverse=True):
        unique.setdefault((item["type"], item["value"]), item)
    result = {"candidates": list(unique.values()), "notices": notices, "review_required": True, "source": "image_extraction"}
    # Strip metadata and re-encode instead of preserving untrusted uploaded bytes.
    output = io.BytesIO()
    image.save(output, format="PNG")
    return result, output.getvalue()


def stage_image(db, user, raw):
    result, sanitized = extract_image(raw)
    attachment_id = uuid.uuid4()
    directory = get_settings().upload_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{attachment_id}.png"
    path.write_bytes(sanitized)
    path.chmod(0o600)
    row = CaseAttachment(id=attachment_id, file_path=path.name, sha256=hashlib.sha256(raw).hexdigest(), extracted_json=result, uploaded_by=user.id)
    db.add(row)
    try:
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    audit(user.id, "case.image_extracted", {"attachment_id": str(attachment_id), "sha256": row.sha256})
    return {"attachment_id": attachment_id, "sha256": row.sha256, **result}
