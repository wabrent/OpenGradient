function setCors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

function isHexAddress(value) {
  return typeof value === "string" && /^0x[a-fA-F0-9]{40}$/.test(value);
}

function clampInt(value, fallback, min, max) {
  const n = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function toIsoFromUnixSeconds(value) {
  const n = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(n) || n <= 0) return null;
  return new Date(n * 1000).toISOString().replace(".000Z", "Z");
}

function normalizeIso(value) {
  if (typeof value !== "string" || !value) return null;
  const v = value.trim();
  const m = v.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.(\d+))?Z$/);
  if (!m) return null;
  const base = m[1];
  const frac = m[3] || "";
  if (!frac) return `${base}Z`;
  const ms = frac.slice(0, 3).padEnd(3, "0");
  return `${base}.${ms}Z`;
}

function toEthFromWei(value) {
  try {
    const s = String(value ?? "0");
    if (!/^\d+$/.test(s)) return 0;
    if (s === "0") return 0;
    const wei = BigInt(s);
    const whole = wei / 1000000000000000000n;
    const frac = wei % 1000000000000000000n;
    const fracStr = frac.toString().padStart(18, "0").replace(/0+$/, "");
    const asStr = fracStr ? `${whole.toString()}.${fracStr}` : whole.toString();
    const asNum = Number(asStr);
    return Number.isFinite(asNum) ? asNum : 0;
  } catch {
    return 0;
  }
}

async function fetchTxsBlockscout(address, limit) {
  const url = new URL(`https://base-sepolia.blockscout.com/api/v2/addresses/${address}/transactions`);
  url.searchParams.set("items_count", String(limit));

  const r = await fetch(url.toString(), { headers: { "Accept": "application/json" } });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    return { ok: false, error: `Blockscout HTTP ${r.status}`, details: text.slice(0, 500) };
  }

  const data = await r.json().catch(() => null);
  if (!data || typeof data !== "object") {
    return { ok: false, error: "Blockscout invalid JSON" };
  }

  if (!Array.isArray(data.items)) {
    return { ok: false, error: "Blockscout response missing items" };
  }

  const txs = data.items
    .map((tx) => {
      const ts = normalizeIso(tx.timestamp) || (typeof tx.timestamp === "string" ? tx.timestamp : null);
      return {
        tx_hash: tx.hash,
        value: toEthFromWei(tx.value),
        timestamp: ts,
      };
    })
    .filter((x) => typeof x.tx_hash === "string" && x.tx_hash.startsWith("0x") && typeof x.timestamp === "string");

  return { ok: true, txs, rawCount: data.items.length };
}

module.exports = async function handler(req, res) {
  setCors(res);
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }

  if (req.method !== "GET") {
    res.status(405).json({ ok: false, error: "method_not_allowed" });
    return;
  }

  const address = String(req.query?.address || "").trim();
  if (!isHexAddress(address)) {
    res.status(400).json({ ok: false, error: "invalid_address" });
    return;
  }

  const limit = clampInt(req.query?.limit, 200, 1, 500);
  const result = await fetchTxsBlockscout(address, limit);
  if (!result.ok) {
    res.status(502).json({
      ok: false,
      error: result.error,
      hint: "If this fails, try again later or lower the limit parameter.",
      details: result.details || null,
    });
    return;
  }

  res.status(200).json({
    ok: true,
    chain: "base-sepolia",
    source: "blockscout",
    address,
    tx_count: result.txs.length,
    txs: result.txs,
  });
};
