from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
import os

# OpenGradient is available only if installed
OG_AVAILABLE = False  # Disabled until properly configured

BLOCKSCOUT_BASE_SEPOLIA = "https://base-sepolia.blockscout.com/api/v2"

def _wei_to_eth(value):
    """Convert Wei to ETH"""
    s = str(value or "0")
    if not s.isdigit():
        return 0.0
    try:
        return int(s) / 1e18
    except Exception:
        return 0.0

def _normalize_iso(ts: object) -> str | None:
    """Normalize ISO timestamp to UTC format"""
    if not isinstance(ts, str) or not ts.strip():
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _normalize_txs(items: list[dict]) -> list[dict]:
    """Normalize transactions from Blockscout API"""
    out: list[dict] = []
    for tx in items:
        if not isinstance(tx, dict):
            continue
        ts = _normalize_iso(tx.get("timestamp"))
        if not ts:
            continue
        # Convert Wei to ETH for frontend (which expects ETH, not Wei)
        value_wei = tx.get("value")
        value_eth = _wei_to_eth(value_wei) if value_wei is not None else 0.0
        out.append(
            {
                "tx_hash": tx.get("hash"),
                "value": value_eth,  # Pass ETH value to frontend
                "timestamp": ts,
            }
        )
    return out

def _get_features(txs):
    """Extract features from transactions for ML model"""
    tx_count = len(txs)
    times = []
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
        values.append(_wei_to_eth(tx.get("value")))  # Convert wei to eth for AI model

    times.sort()
    span_hours = 0.0
    if len(times) >= 2:
        span_seconds = (times[-1] - times[0]).total_seconds()
        if span_seconds > 0:
            span_hours = span_seconds / 3600.0

    tx_per_hour = (tx_count / span_hours) if span_hours else 0.0
    rounded = [round(v, 6) for v in values]
    unique_ratio = (len(set(rounded)) / len(rounded)) if rounded else 0.0
    repeat_ratio = (1.0 - unique_ratio) if rounded else 0.0
    small_fraction = (sum(1 for v in values if v <= 0.02) / len(values)) if values else 0.0
    large_fraction = (sum(1 for v in values if v >= 5.0) / len(values)) if values else 0.0

    return [float(tx_count), float(tx_per_hour), float(span_hours), float(repeat_ratio), float(small_fraction), float(large_fraction)]


# Vercel Python Handler (Serverless Function)
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Parse URL parameters
        parsed_path = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed_path.query)
        address = query.get("address", [""])[0].strip()

        # Validate address
        if not address or not address.startswith("0x") or len(address) != 42:
            self.send_response(400)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "invalid_address"}).encode())
            return

        # Parse limit parameter
        limit_raw = query.get("limit", ["50"])[0]
        try:
            limit = int(limit_raw)
        except Exception:
            limit = 50
        limit = max(1, min(limit, 500))
        
        # Step 1: Fetch transaction history from Blockscout
        try:
            url = f"{BLOCKSCOUT_BASE_SEPOLIA}/addresses/{address}/transactions?size={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, method="GET")
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "blockscout_error", "details": str(e)}).encode())
            return

        items = data.get("items", [])
        if not isinstance(items, list):
            self.send_response(502)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "blockscout_invalid_response"}).encode())
            return

        txs = _normalize_txs(items)
        raw_features = _get_features(txs)

        response_data = {
            "ok": True,
            "chain": "base-sepolia",
            "source": "blockscout",
            "address": address,
            "tx_count": len(txs),
            "txs": txs,
            "features": {
                "tx_count": raw_features[0],
                "tx_per_hour": raw_features[1],
                "span_hours": raw_features[2],
                "repeat_ratio": raw_features[3],
                "small_fraction": raw_features[4],
                "large_fraction": raw_features[5]
            },
            "assessment": "Unknown",
            "score": 0,
            "reasons": []
        }

        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Step 2: OpenGradient AI Inference
        private_key = os.environ.get("PRIVATE_KEY")
        model_id = os.environ.get("OPENGRADIENT_MODEL_ID", "4-vJc69O2zGJTG")

        # If client is installed and wallet key exists, run inference
        if OG_AVAILABLE and private_key:
            try:
                # Initialize client
                client = og.Client(private_key)
                
                # Run trained model on blockchain
                # Pass feature array X with shape [1, 6]
                result = client.run_inference(model_id=model_id, inputs=[raw_features])

                # Parse response (according to our logic: 1 = Bot, 0 = Human)
                is_bot = int(result[0]) == 1
                response_data["assessment"] = "Bot" if is_bot else "Human"
                response_data["score"] = 99 if is_bot else 10
                response_data["reasons"] = [f"Verified by OpenGradient AI (Model {model_id})"]

            except Exception as e:
                response_data["assessment"] = "Error"
                response_data["score"] = 0
                response_data["reasons"] = [f"OG Inference Error: {str(e)}"]
                response_data["og_available"] = True
                response_data["has_key"] = bool(private_key)

        else:
            # Fallback - local simulation if no key (or library not installed)
            if len(txs) > 0:
                score = 0
                if raw_features[0] >= 50:
                    score += 20
                if raw_features[1] >= 10:
                    score += 15
                if raw_features[3] >= 0.6:
                    score += 20
                if raw_features[4] >= 0.7:
                    score += 10

                response_data["score"] = score
                response_data["assessment"] = "Bot" if score >= 60 else "Human"
                response_data["reasons"] = ["Locally simulated (Add PRIVATE_KEY to Vercel env for real AI inference)"]
                response_data["og_available"] = OG_AVAILABLE
                response_data["has_key"] = bool(private_key)
            else:
                response_data["assessment"] = "Unknown"
                response_data["score"] = 0
                response_data["reasons"] = ["No transactions found"]

        self.wfile.write(json.dumps(response_data).encode())
