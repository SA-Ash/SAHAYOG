import random
import statistics

from app.engines.signals import calibrate, fit_isotonic


def metrics(samples):
    predicted = sum(score >= 0.5 for score, _ in samples)
    positives = sum(y for _, y in samples)
    tp = sum(score >= 0.5 and y for score, y in samples)
    precision = tp / predicted if predicted else 0
    recall = tp / positives if positives else 0
    bins = []
    for i in range(10):
        rows = [(s, y) for s, y in samples if i / 10 <= s < (i + 1) / 10 or i == 9 and s == 1]
        if rows:
            bins.append(
                {
                    "from": i / 10,
                    "count": len(rows),
                    "confidence": statistics.mean(s for s, _ in rows),
                    "accuracy": statistics.mean(y for _, y in rows),
                }
            )
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
        "ece": sum(b["count"] * abs(b["confidence"] - b["accuracy"]) for b in bins)
        / max(1, len(samples)),
        "bins": bins,
        "samples": len(samples),
    }


def evaluate(samples, seed=42):
    if len(samples) < 10:
        raise ValueError("At least 10 independent examples required")
    rng = random.Random(seed)
    rows = list(samples)
    rng.shuffle(rows)
    cut = len(rows) // 2
    fitted = fit_isotonic(rows[:cut])
    heldout = rows[cut:]
    calibrated = [(calibrate(s, fitted), y) for s, y in heldout]
    boot = [metrics(rng.choices(calibrated, k=len(calibrated))) for _ in range(500)]
    ci = {
        key: [sorted(b[key] for b in boot)[12], sorted(b[key] for b in boot)[487]]
        for key in ("precision", "recall", "f1", "ece")
    }
    return {
        "training_samples": cut,
        "heldout": metrics(heldout),
        "calibrated_heldout": metrics(calibrated),
        "bootstrap_95_ci": ci,
        "calibration": fitted,
        "seed": seed,
    }
