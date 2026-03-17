import json
import sys
from datetime import datetime, timezone
import urllib.parse
import urllib.request


BLOCKSCOUT_BASE_SEPOLIA = "https://base-sepolia.blockscout.com/api/v2"


def _is_hex_address(value: str) -> bool:
    return isinstance(value, str) and value.startswith("0x") and len(value) == 42


def _fetch_txs(address: str, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 50))
    url = f"{BLOCKSCOUT_BASE_SEPOLIA}/addresses/{address}/transactions?{urllib.parse.urlencode({'items_count': str(limit)})}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    items = data.get("items")
    if not isinstance(items, list):
        raise RuntimeError(f"Blockscout error ({str(data)[:200]})")
    return items


def _wei_to_eth(value: object) -> float:
    s = str(value or "0")
    if not s.isdigit():
        return 0.0
    try:
        return int(s) / 1e18
    except Exception:
        return 0.0


def _score_wallet(address: str, txs: list[dict]) -> dict:
    tx_count = len(txs)
    times: list[datetime] = []
    values = []

    for tx in txs:
        ts = tx.get("timestamp")
        if isinstance(ts, str) and ts:
            try:
                raw = ts.strip()
                if raw.endswith("Z"):
                    raw = raw[:-1] + "+00:00"
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                times.append(dt)
            except Exception:
                pass
        values.append(_wei_to_eth(tx.get("value")))

    times.sort()
    span_hours = None
    if len(times) >= 2:
        span_seconds = (times[-1] - times[0]).total_seconds()
        if span_seconds > 0:
            span_hours = span_seconds / 3600.0

    tx_per_hour = (tx_count / span_hours) if span_hours else None
    rounded = [round(v, 6) for v in values]
    unique_ratio = (len(set(rounded)) / len(rounded)) if rounded else None
    repeat_ratio = (1.0 - unique_ratio) if unique_ratio is not None else None
    small_fraction = (sum(1 for v in values if v <= 0.02) / len(values)) if values else None
    large_fraction = (sum(1 for v in values if v >= 5.0) / len(values)) if values else None

    score = 0
    reasons: list[str] = []

    if tx_count >= 50:
        score += 20
        reasons.append("Very high number of transactions")
    elif tx_count >= 20:
        score += 10
        reasons.append("High number of transactions")

    if tx_per_hour is not None:
        if tx_per_hour >= 30:
            score += 30
            reasons.append("High transaction frequency (burst)")
        elif tx_per_hour >= 10:
            score += 15
            reasons.append("Elevated transaction frequency")

    if span_hours is not None and tx_count >= 10:
        if span_hours <= 0.25:
            score += 25
            reasons.append("Many transactions in a short time window")
        elif span_hours <= 1.0:
            score += 10
            reasons.append("Activity is time-concentrated")

    if repeat_ratio is not None:
        if repeat_ratio >= 0.6 and tx_count >= 10:
            score += 20
            reasons.append("Repeated amounts/patterns")
        elif repeat_ratio >= 0.4 and tx_count >= 10:
            score += 10
            reasons.append("Partially repeated amounts")

    if small_fraction is not None and small_fraction >= 0.7 and tx_count >= 10:
        score += 10
        reasons.append("Large share of micro-transactions")

    if large_fraction is not None and large_fraction >= 0.7 and tx_count >= 10:
        score += 5
        reasons.append("Large share of high-value transfers")

    if tx_count < 5:
        reasons.append("Not enough data; low-confidence estimate")

    score = max(0, min(score, 100))
    assessment = "Bot" if score >= 60 else "Human"

    return {
        "wallet": address,
        "assessment": assessment,
        "score": score,
        "tx_count": tx_count,
        "span_hours": span_hours,
        "tx_per_hour": tx_per_hour,
        "repeat_ratio": repeat_ratio,
        "small_fraction": small_fraction,
        "large_fraction": large_fraction,
        "reasons": reasons[:6],
    }


def main(argv: list[str]) -> int:
    addresses = [a.strip() for a in argv[1:] if a.strip()]
    if not addresses:
        print("Usage: py -3 check_wallets.py 0x... 0x... [more]")
        return 2

    for a in addresses:
        if not _is_hex_address(a):
            print(f"Invalid address: {a}")
            return 2

    results = []
    for address in addresses:
        txs = _fetch_txs(address, limit=200)
        results.append(_score_wallet(address, txs))

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
