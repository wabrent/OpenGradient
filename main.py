import os
import asyncio
import json
import re
from datetime import datetime, timezone
from statistics import mean, median, pstdev
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import sys
import opengradient as og
from dotenv import load_dotenv

load_dotenv()

def _get_env_value(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None
    if value in {"your_private_key_here", "your_memsync_api_key_here"}:
        return None
    return value


def _normalize_private_key(private_key: str) -> str:
    private_key = private_key.strip()
    if not private_key.startswith("0x"):
        return f"0x{private_key}"
    return private_key


def _get_payer_address(llm) -> str | None:
    for attr in ("wallet_address", "address"):
        value = getattr(llm, attr, None)
        if isinstance(value, str) and value.startswith("0x") and len(value) > 10:
            return value

    account = getattr(llm, "account", None)
    if account is not None:
        value = getattr(account, "address", None)
        if isinstance(value, str) and value.startswith("0x") and len(value) > 10:
            return value

    return None


def _address_from_private_key(private_key: str) -> str | None:
    try:
        from eth_account import Account
    except Exception:
        return None

    try:
        account = Account.from_key(private_key)
        address = getattr(account, "address", None)
        if isinstance(address, str) and address.startswith("0x") and len(address) > 10:
            return address
        return None
    except Exception:
        return None


def _pick_tee_model():
    candidates = [
        "O4_MINI",
        "O4",
        "GPT_4O",
        "GPT_4O_MINI",
        "GPT_5",
        "CLAUDE_SONNET_4_6",
        "CLAUDE_3_5_SONNET",
    ]
    for name in candidates:
        model = getattr(og.TEE_LLM, name, None)
        if model is not None:
            return model
    raise RuntimeError("Не удалось выбрать модель из og.TEE_LLM. Проверьте версию opengradient.")


def _pick_x402_settlement_mode():
    enum_cls = getattr(og, "x402SettlementMode", None)
    if enum_cls is None:
        return None

    for name in ("SETTLE_BATCH", "SETTLE", "SETTLE_METADATA", "SETTLE_BATCHED"):
        value = getattr(enum_cls, name, None)
        if value is not None:
            return value

    return None


def _tx_explorer_url(tx_hash: str | None) -> str | None:
    if not tx_hash or not isinstance(tx_hash, str):
        return None
    if not tx_hash.startswith("0x"):
        return None
    if len(tx_hash) < 10:
        return None
    return f"https://sepolia.basescan.org/tx/{tx_hash}"


def _extract_tx_hash(obj) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj if obj.startswith("0x") else None
    for attr in ("tx_hash", "transaction_hash", "hash"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value.startswith("0x") and len(value) > 10:
            return value
    return None


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _max_tokens() -> int:
    value = _safe_int(_get_env_value("MAX_TOKENS") or _get_env_value("OG_MAX_TOKENS"))
    if value is None:
        return 120
    return max(1, min(value, 512))


def _truthy_env(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return False
    raw = raw.strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _dry_run_enabled() -> bool:
    return _truthy_env("DRY_RUN") or _truthy_env("OG_DRY_RUN")


def _parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return None
    return None


def _load_transactions_from_env() -> list[dict]:
    raw = _get_env_value("TRANSACTIONS_JSON") or _get_env_value("OG_TRANSACTIONS_JSON")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _default_mock_transactions() -> list[dict]:
    return [
        {"tx_hash": "0xabc123...", "value": 0.5, "timestamp": "2024-03-10T12:00:00Z"},
        {"tx_hash": "0xdef456...", "value": 10.2, "timestamp": "2024-03-11T14:30:00Z"},
        {"tx_hash": "0x789ghi...", "value": 0.01, "timestamp": "2024-03-12T09:15:00Z"},
    ]



def _score_wallet_locally(wallet_address: str, txs: list[dict]) -> dict:
    tx_count = len(txs)
    values: list[float] = []
    times: list[datetime] = []

    for tx in txs:
        values_value = _as_float(tx.get("value"))
        if values_value is not None:
            values.append(values_value)
        ts = _parse_iso_timestamp(tx.get("timestamp"))
        if ts is not None:
            times.append(ts)

    times.sort()
    span_seconds = (times[-1] - times[0]).total_seconds() if len(times) >= 2 else None
    span_hours = (span_seconds / 3600.0) if span_seconds and span_seconds > 0 else None
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
        reasons.append("Очень много транзакций")
    elif tx_count >= 20:
        score += 10
        reasons.append("Много транзакций")

    if tx_per_hour is not None:
        if tx_per_hour >= 30:
            score += 30
            reasons.append("Высокая частота транзакций (burst)")
        elif tx_per_hour >= 10:
            score += 15
            reasons.append("Повышенная частота транзакций")

    if span_hours is not None and tx_count >= 10:
        if span_hours <= 0.25:
            score += 25
            reasons.append("Много транзакций за короткое время")
        elif span_hours <= 1.0:
            score += 10
            reasons.append("Активность сконцентрирована во времени")

    if repeat_ratio is not None:
        if repeat_ratio >= 0.6 and tx_count >= 10:
            score += 20
            reasons.append("Повторяющиеся суммы/паттерны")
        elif repeat_ratio >= 0.4 and tx_count >= 10:
            score += 10
            reasons.append("Частично повторяющиеся суммы")

    if small_fraction is not None and small_fraction >= 0.7 and tx_count >= 10:
        score += 10
        reasons.append("Большая доля микротранзакций")

    if large_fraction is not None and large_fraction >= 0.7 and tx_count >= 10:
        score += 5
        reasons.append("Большая доля крупных переводов")

    if tx_count < 5:
        reasons.append("Мало данных, оценка неточная")

    score = max(0, min(score, 100))
    assessment = "Bot" if score >= 60 else "Human"

    value_stats = None
    if values:
        value_stats = {
            "mean": mean(values),
            "median": median(values),
            "stdev": pstdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
        }

    features = {
        "tx_count": tx_count,
        "span_hours": span_hours,
        "tx_per_hour": tx_per_hour,
        "unique_value_ratio": unique_ratio,
        "repeat_value_ratio": repeat_ratio,
        "small_value_fraction": small_fraction,
        "large_value_fraction": large_fraction,
        "value_stats": value_stats,
    }

    return {
        "wallet": wallet_address,
        "assessment": assessment,
        "score": score,
        "reasons": reasons[:6],
        "features": features,
        "mode": "local",
    }


def _local_assessment(wallet_address: str, txs: list[dict]) -> str:
    payload = _score_wallet_locally(wallet_address, txs)
    return json.dumps(payload, ensure_ascii=False)


def _render_index_html() -> bytes:
    html = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>Wallet Reputation (Local)</title>
    <style>
      body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:24px;max-width:860px}
      input,textarea,button{font:inherit}
      textarea{width:100%;min-height:140px}
      .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
      .muted{color:#666}
      pre{background:#0b1020;color:#e6e6e6;padding:12px;border-radius:8px;overflow:auto}
      .card{border:1px solid #ddd;border-radius:10px;padding:14px}
    </style>
  </head>
  <body>
    <h1>Reputation Analyzer</h1>
    <p class="muted">Локальный режим (без токенов): скоринг по эвристикам.</p>

    <div class="card">
      <div class="row">
        <label>Адрес:</label>
        <input id="address" style="flex:1;min-width:340px" placeholder="0x..." />
        <button id="run">Проверить</button>
      </div>
      <p class="muted">Опционально: вставь транзакции JSON (list of objects с value и timestamp).</p>
      <textarea id="txjson" placeholder='[{"value":0.01,"timestamp":"2024-03-12T09:15:00Z"}]'></textarea>
    </div>

    <h2>Результат</h2>
    <pre id="out">—</pre>

    <script>
      const out = document.getElementById('out');
      const addr = document.getElementById('address');
      const txjson = document.getElementById('txjson');
      document.getElementById('run').addEventListener('click', async () => {
        out.textContent = '...';
        const payload = { address: addr.value.trim(), transactions_json: txjson.value.trim() || null };
        const res = await fetch('/api/score', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
        const text = await res.text();
        try { out.textContent = JSON.stringify(JSON.parse(text), null, 2); }
        catch { out.textContent = text; }
      });
    </script>
  </body>
</html>
""".strip()
    return html.encode("utf-8")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _text_response(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _parse_body_json(handler: BaseHTTPRequestHandler) -> dict | None:
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except Exception:
        length = 0
    if length <= 0:
        return None
    raw = handler.rfile.read(length)
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _local_score_payload(address: str, txs: list[dict]) -> dict:
    return _score_wallet_locally(address, txs)


def run_web_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    tx_data_default = _load_transactions_from_env() or _default_mock_transactions()
    index_html = _render_index_html()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                # Serve the real index.html if it exists
                try:
                    with open("index.html", "rb") as f:
                        content = f.read()
                    _text_response(self, 200, content, "text/html; charset=utf-8")
                except Exception:
                    _text_response(self, 200, index_html, "text/html; charset=utf-8")
                return
            if parsed.path == "/api/txs":
                # Proxy to Blockscout (Base Sepolia)
                query = parse_qs(parsed.query)
                address = query.get("address", [""])[0]
                limit = min(50, _safe_int(query.get("limit", ["50"])[0]) or 50)
                if not address:
                    _json_response(self, 400, {"ok": False, "error": "missing_address"})
                    return
                try:
                    # Reuse fetch function logic
                    from check_wallets import _fetch_txs, _wei_to_eth
                    items = _fetch_txs(address, limit)
                    txs = []
                    for item in items:
                        txs.append({
                            "tx_hash": item.get("hash"),
                            "value": _wei_to_eth(item.get("value")),
                            "timestamp": item.get("timestamp")
                        })
                    _json_response(self, 200, {
                        "ok": True,
                        "chain": "base-sepolia",
                        "source": "blockscout",
                        "address": address,
                        "tx_count": len(txs),
                        "txs": txs
                    })
                except Exception as e:
                    _json_response(self, 502, {"ok": False, "error": str(e)})
                return
            if parsed.path == "/health":
                _json_response(self, 200, {"ok": True})
                return
            _json_response(self, 404, {"error": "not_found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/api/score":
                _json_response(self, 404, {"error": "not_found"})
                return

            body = _parse_body_json(self)
            if body is None:
                _json_response(self, 400, {"error": "invalid_json"})
                return

            address = (body.get("address") or "").strip()
            if not address:
                _json_response(self, 400, {"error": "missing_address"})
                return

            txs = tx_data_default
            raw_txs = body.get("transactions_json")
            if isinstance(raw_txs, str) and raw_txs.strip():
                try:
                    parsed_txs = json.loads(raw_txs)
                    if isinstance(parsed_txs, list):
                        txs = [x for x in parsed_txs if isinstance(x, dict)]
                except Exception:
                    _json_response(self, 400, {"error": "invalid_transactions_json"})
                    return

            _json_response(self, 200, _local_score_payload(address, txs))

        def log_message(self, format, *args):
            return

    server = HTTPServer((host, port), Handler)
    print(f"Web UI: http://{host}:{port}/")
    print("API: POST /api/score")
    server.serve_forever()


async def _ensure_opg_approval(llm) -> str | None:
    ensure_fn = getattr(llm, "ensure_opg_approval", None)
    if ensure_fn is None:
        return None
    try:
        result = ensure_fn(opg_amount=5.0)
        if asyncio.iscoroutine(result):
            result = await result
        return _extract_tx_hash(result)
    except Exception:
        return None


def _is_payment_required_error(e: Exception) -> bool:
    for attr in ("status_code", "http_status", "status"):
        value = getattr(e, attr, None)
        if value == 402:
            return True

    message = str(e).lower()
    if "payment required" in message or "402" in message:
        return True
    
    # Catch SDK-specific payment errors that might not have 402 in string
    if "payment" in message or "permit2" in message or "no payment requirements" in message:
        return True

    cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    if isinstance(cause, Exception):
        return _is_payment_required_error(cause)

    return False


def _extract_required_opg_amount(e: Exception) -> float | None:
    candidates = []

    for attr in (
        "required_opg",
        "required_amount",
        "required_payment_amount",
        "amount",
        "opg_amount",
    ):
        value = getattr(e, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            candidates.append(float(value))

    message = str(e)
    patterns = [
        r'(?i)\b(\d+(?:\.\d+)?)\s*opg\b',
        r'(?i)"opg"\s*:\s*"?(?P<num>\d+(?:\.\d+)?)"?',
        r'(?i)"amount"\s*:\s*"?(?P<num>\d+(?:\.\d+)?)"?',
        r"(?i)\brequired\b[^\d]{0,40}(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, message):
            num = m.groupdict().get("num") if hasattr(m, "groupdict") else None
            raw = num or (m.group(1) if m.groups() else None)
            if not raw:
                continue
            try:
                value = float(raw)
            except Exception:
                continue
            if value > 0:
                candidates.append(value)

    if candidates:
        return max(candidates)

    cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    if isinstance(cause, Exception) and cause is not e:
        return _extract_required_opg_amount(cause)

    return None


def _print_payment_help(payer_address: str | None, error: Exception | None = None) -> None:
    print("Ошибка 402 Payment Required: для запроса нужны тестовые токены $OPG и Permit2-approval.")
    if payer_address:
        print(f"Плательщик (адрес из PRIVATE_KEY): {payer_address}")
        print(f"BaseScan: https://sepolia.basescan.org/address/{payer_address}")
        print("Важно: пополнять нужно именно этот адрес (плательщик), а не анализируемый адрес.")
    print("Проверьте, что $OPG именно на Base Sepolia (chain id 84532), токен: 0x240b09731D96979f50B2C649C9CE10FcF9C7987F")
    print("Если баланс ETH на Base Sepolia = 0, approve может не пройти из-за газа.")
    if error is not None:
        required = _extract_required_opg_amount(error)
        if required is not None:
            print(f"По ответу провайдера может требоваться примерно: {required} OPG (или больше).")


async def _call_llm(llm, model, prompt: str, max_tokens: int, settlement_mode):
    completion_fn = getattr(llm, "completion", None)
    if callable(completion_fn):
        try:
            kwargs = {
                "model": model,
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": 0.0,
            }
            if settlement_mode is not None:
                kwargs["x402_settlement_mode"] = settlement_mode
            return await completion_fn(**kwargs)
        except TypeError:
            return await completion_fn(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.0,
            )

    chat_fn = getattr(llm, "chat", None)
    if not callable(chat_fn):
        raise RuntimeError("В установленном opengradient нет методов llm.chat / llm.completion.")

    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if settlement_mode is not None:
        kwargs["x402_settlement_mode"] = settlement_mode
    return await chat_fn(**kwargs)


async def _store_in_memsync(private_key: str, wallet_address: str, assessment_result: str, tx_hash: str | None) -> bool:
    memsync_cls = getattr(og, "MemSync", None)
    if memsync_cls is None:
        return False

    memsync_api_key = _get_env_value("MEMSYNC_API_KEY")
    init_kwargs = {"api_key": memsync_api_key} if memsync_api_key else {"private_key": private_key}

    try:
        memsync = memsync_cls(**init_kwargs)
        add_fn = getattr(memsync, "add_memory", None) or getattr(memsync, "add", None)
        if add_fn is None:
            return False

        payload = {
            "user_id": wallet_address,
            "content": f"Reputation Assessment: {assessment_result}",
            "metadata": {"source": "ReputationAnalyzer", "tx_hash": tx_hash},
        }

        result = add_fn(**payload)
        if asyncio.iscoroutine(result):
            await result
        return True
    except Exception:
        return False


async def analyze_wallet_reputation(wallet_address: str) -> tuple[str | None, str | None]:
    tx_data = _load_transactions_from_env() or _default_mock_transactions()

    if _dry_run_enabled():
        assessment_result = _local_assessment(wallet_address, tx_data)
        print(f"Адрес: {wallet_address}")
        print(f"Оценка: {assessment_result}")
        print("Tx Hash: N/A")
        print("MemSync: SKIP")
        return assessment_result, None

    private_key = _get_env_value("PRIVATE_KEY") or _get_env_value("OG_PRIVATE_KEY")
    if not private_key:
        assessment_result = _local_assessment(wallet_address, tx_data)
        print("PRIVATE_KEY не задан — выполняю локальный режим (без LLM/OPG).")
        print(f"Адрес: {wallet_address}")
        print(f"Оценка: {assessment_result}")
        print("Tx Hash: N/A")
        print("MemSync: SKIP")
        return assessment_result, None

    normalized_private_key = _normalize_private_key(private_key)
    llm = og.LLM(private_key=normalized_private_key)
    payer_address = _get_payer_address(llm) or _address_from_private_key(normalized_private_key)
    if payer_address:
        print(f"Плательщик (адрес из PRIVATE_KEY): {payer_address}")
        print(f"BaseScan: https://sepolia.basescan.org/address/{payer_address}")

    approve_tx_hash = await _ensure_opg_approval(llm)
    approve_url = _tx_explorer_url(approve_tx_hash)
    if approve_url:
        print(f"Permit2 approve tx: {approve_url}")

    context = f"Wallet Address: {wallet_address}\nRecent Transactions: {json.dumps(tx_data, ensure_ascii=False, separators=(',', ':'))}"
    prompt = (
        f"Проанализируй историю транзакций этого кошелька:\n{context}\n\n"
        "Определи: человек или бот. Верни строго JSON: "
        '{"assessment":"Human|Bot","reason":"..."}'
    )

    try:
        max_tokens = _max_tokens()
        chat_kwargs = {
            "model": _pick_tee_model(),
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        settlement_mode = _pick_x402_settlement_mode()
        response = await _call_llm(
            llm=llm,
            model=chat_kwargs["model"],
            prompt=prompt,
            max_tokens=max_tokens,
            settlement_mode=settlement_mode,
        )
    except Exception as e:
        if _is_payment_required_error(e):
            approve_tx_hash = await _ensure_opg_approval(llm)
            approve_url = _tx_explorer_url(approve_tx_hash)
            if approve_url:
                print(f"Permit2 approve tx: {approve_url}")
            try:
                response = await _call_llm(
                    llm=llm,
                    model=_pick_tee_model(),
                    prompt=prompt,
                    max_tokens=_max_tokens(),
                    settlement_mode=_pick_x402_settlement_mode(),
                )
            except Exception as e2:
                if _is_payment_required_error(e2):
                    _print_payment_help(payer_address, error=e2)
                    assessment_result = _local_assessment(wallet_address, tx_data)
                    print("Переход в локальный режим (без LLM/OPG) из-за 402.")
                    print(f"Адрес: {wallet_address}")
                    print(f"Оценка: {assessment_result}")
                    print("Tx Hash: N/A")
                    print("MemSync: SKIP")
                    return assessment_result, None
                print(f"Ошибка OpenGradient SDK: {e2}")
                return None, None
        print(f"Ошибка OpenGradient SDK: {e}")
        return None, None

    assessment_result = (
        getattr(response, "chat_output", None)
        or getattr(response, "completion_output", None)
        or str(response)
    )
    tx_hash = getattr(response, "tx_hash", None) or getattr(response, "transaction_hash", None)

    print(f"Адрес: {wallet_address}")
    print(f"Оценка: {assessment_result}")
    if tx_hash:
        print(f"Tx Hash: {tx_hash}")
        url = _tx_explorer_url(tx_hash)
        if url:
            print(f"Ссылка: {url}")
    else:
        print("Tx Hash: N/A")

    stored = await _store_in_memsync(private_key, wallet_address, assessment_result, tx_hash)
    print(f"MemSync: {'OK' if stored else 'SKIP'}")

    return assessment_result, tx_hash


if __name__ == "__main__":
    if "--serve" in sys.argv:
        host = "127.0.0.1"
        port = 8000
        for i, arg in enumerate(sys.argv):
            if arg == "--host" and i + 1 < len(sys.argv):
                host = sys.argv[i + 1]
            if arg == "--port" and i + 1 < len(sys.argv):
                try:
                    port = int(sys.argv[i + 1])
                except Exception:
                    port = 8000
        run_web_server(host=host, port=port)
    else:
        address = sys.argv[1] if len(sys.argv) > 1 else "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
        asyncio.run(analyze_wallet_reputation(address))
