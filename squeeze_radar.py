#!/usr/bin/env python3
"""
SqueezeRadar — find crowded shorts BEFORE the squeeze.

A funding-rate radar for crypto perpetual futures. It scans public
exchange APIs (no keys, no accounts, no orders), ranks coins by how
negative their funding rate is, and scores each one 0-100 for
short-squeeze potential.

Two ways to use it:

    # CLI — ranked table of the most crowded shorts
    python squeeze_radar.py --top 20
    python squeeze_radar.py --symbol IDOLUSDT
    python squeeze_radar.py --top 10 --json
    python squeeze_radar.py --top 10 --watch 15     # re-scan every 15 min

    # MCP server (stdio) — Claude Desktop, Claude Code, Cursor,
    # Windsurf, VS Code, ChatGPT, any MCP client
    python squeeze_radar.py --mcp

    # List the MCP tools without starting the server
    python squeeze_radar.py --dump-tools

Honesty notes (read before you trade on this):

* Negative funding means shorts pay longs — the more negative, the
  more crowded the short side, the more fuel a squeeze has.
* Funding settles every 4-8 hours. It is a SLOW crowding filter,
  never a 15-minute entry trigger. A crowded short can stay crowded
  for days, and price can keep falling while funding is negative.
* The squeeze score is a heuristic, not a prediction. It can be
  wrong. Nothing here is financial advice.
* If an exchange is unreachable from your location (Binance returns
  HTTP 451 and Bybit returns HTTP 403 from some regions), the radar
  says so and keeps scanning the others. It never crashes the whole
  scan because one exchange is down, and it never invents numbers.

Exchanges (all public, keyless):
* Bitget — primary universe. One tickers call returns ~884
  USDT-futures tickers WITH funding rates included.
  (Bitget's dedicated funding-rate endpoints 404 as of 2026-11-09,
  so the tickers endpoint is the authoritative source.)
* Gate.io — cross-check. One contracts call returns ~1080
  contracts with funding rates.
* OKX — per-symbol funding rate (only for listed coins).
* Binance — per-symbol funding rate. Geo-blocked (HTTP 451) in
  some regions; treated as "unavailable", never fatal.
* Bybit — per-symbol funding rate. CloudFront geo-blocked
  (HTTP 403) in some regions; treated as "unavailable".

Dependencies: httpx only (mcp is lazy-imported, only for --mcp).
CLI mode works even if the mcp package is not installed.
"""

import argparse
import json
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

try:
    import httpx
except ImportError:  # pragma: no cover - handled in main() with a clear error
    httpx = None

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

BITGET_TICKERS_URL = (
    "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"
)
BITGET_TICKER_URL = (
    "https://api.bitget.com/api/v2/mix/market/ticker"
    "?symbol={symbol}&productType=USDT-FUTURES"
)
GATE_CONTRACTS_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts"
GATE_CONTRACT_URL = "https://api.gateio.ws/api/v4/futures/usdt/contracts/{symbol}"
BINANCE_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
BYBIT_TICKERS_URL = (
    "https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
)
OKX_FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate?instId={inst_id}"

DEFAULT_TIMEOUT = 15.0
DEFAULT_MIN_VOLUME_USDT = 100_000.0  # ignore illiquid dust below this 24h volume

# ---------------------------------------------------------------------------
# Squeeze score (0-100) — honest heuristic, see module docstring.
#   funding    : up to 60 pts — |funding| / 0.30% (only NEGATIVE funding
#                counts; crowded longs score 0 here)
#   momentum   : up to 20 pts — 24h price change / +10% (a coin already
#                pumping with crowded shorts is hotter)
#   liquidity  : up to 10 pts — 24h USDT volume (confidence that the
#                funding signal is real, not dust)
#   agreement  : up to 10 pts — Gate funding also negative (two
#                exchanges agreeing beats one)
# ---------------------------------------------------------------------------

FUNDING_FULL_SCORE_AT_PCT = 0.30  # -0.30% funding earns the full 60 pts
MOMENTUM_FULL_SCORE_AT_PCT = 10.0  # +10% in 24h earns the full 20 pts


def squeeze_score(funding_pct, change24h_pct, volume_usdt, gate_agrees=False):
    """Score a coin 0-100 for short-squeeze potential.

    funding_pct: Bitget funding rate in percent (e.g. -0.2841).
    change24h_pct: 24h price change in percent (e.g. +11.75).
    volume_usdt: 24h volume in USDT.
    gate_agrees: True if Gate.io funding is also negative.
    Returns (score:int, breakdown:dict).
    """
    funding_pts = 0.0
    if funding_pct is not None and funding_pct < 0:
        funding_pts = min(abs(funding_pct) / FUNDING_FULL_SCORE_AT_PCT, 1.0) * 60.0

    momentum_pts = 0.0
    if change24h_pct is not None and change24h_pct > 0:
        momentum_pts = min(change24h_pct / MOMENTUM_FULL_SCORE_AT_PCT, 1.0) * 20.0

    liquidity_pts = 1.0
    if volume_usdt is not None:
        if volume_usdt >= 10_000_000:
            liquidity_pts = 10.0
        elif volume_usdt >= 2_000_000:
            liquidity_pts = 7.0
        elif volume_usdt >= 500_000:
            liquidity_pts = 4.0

    agreement_pts = 10.0 if gate_agrees else 0.0

    score = int(round(funding_pts + momentum_pts + liquidity_pts + agreement_pts))
    score = max(0, min(100, score))
    return score, {
        "funding": round(funding_pts, 1),
        "momentum": round(momentum_pts, 1),
        "liquidity": round(liquidity_pts, 1),
        "agreement": round(agreement_pts, 1),
    }


def read_label(funding_pct):
    """One-line plain-English read of a funding rate."""
    if funding_pct is None:
        return "unknown"
    if funding_pct <= -0.10:
        return "crowded shorts"
    if funding_pct >= 0.10:
        return "crowded longs"
    return "neutral"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _verify_setting():
    """TLS verification setting for httpx.

    Honor SSL_CERT_FILE / NODE_EXTRA_CA_CERTS when set — corporate
    proxies and MITM egress setups publish their CA bundle there.
    Otherwise use httpx's default (certifi). Normal machines are
    unaffected: those variables are normally unset.
    """
    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("NODE_EXTRA_CA_CERTS")
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    return True


def make_client(timeout=DEFAULT_TIMEOUT):
    """Build an httpx client.

    Plain defaults first (works on any normal machine). If the
    client's proxy environment cannot be parsed — some setups ship
    malformed NO_PROXY entries httpx rejects — fall back to reading
    the proxy URL directly and ignoring the rest of the proxy env.
    Everything here is driven by the user's own environment; nothing
    about any one machine is baked in.
    """
    verify = _verify_setting()
    try:
        return httpx.Client(timeout=timeout, follow_redirects=True, verify=verify)
    except Exception:
        proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                 or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
                 or None)
        return httpx.Client(timeout=timeout, follow_redirects=True,
                            verify=verify, proxy=proxy, trust_env=False)


def fetch_json(client, url):
    """GET url -> (parsed_json_or_None, error_or_None). Never raises."""
    try:
        resp = client.get(url)
    except Exception as exc:  # network down, DNS, proxy, timeout, ...
        return None, "unreachable: %s" % (exc,)
    if resp.status_code == 451:
        return None, "unavailable: HTTP 451 (geo-blocked in this region)"
    if resp.status_code == 403:
        return None, "unavailable: HTTP 403 (blocked in this region)"
    if resp.status_code == 404:
        return None, "unavailable: HTTP 404 (endpoint not found)"
    if resp.status_code != 200:
        return None, "unavailable: HTTP %d" % resp.status_code
    try:
        return resp.json(), None
    except Exception as exc:
        return None, "unavailable: bad JSON response (%s)" % (exc,)


def _to_float(value):
    """Parse a string/number to float, or None if it is not a number."""
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------

def normalize_base(raw):
    """Normalize any user-typed symbol to its base coin.

    IDOL, IDOLUSDT, idol/usdt, IDOL/USDT:USDT, IDOL-USDT-SWAP,
    IDOL_USDT -> "IDOL". Raises ValueError on garbage input.
    """
    if raw is None:
        raise ValueError("empty symbol")
    s = str(raw).strip().upper().replace(" ", "")
    if not s:
        raise ValueError("empty symbol")
    # Split on separators first: "IDOL/USDT:USDT" -> "IDOL"
    for sep in ("/", ":", "-"):
        if sep in s:
            s = s.split(sep)[0]
            break
    # Underscore pairs: "IDOL_USDT" -> "IDOL"
    if s.endswith("_USDT"):
        s = s[:-5]
    elif s.endswith("_USD"):
        s = s[:-4]
    # Attached quote: "IDOLUSDT" -> "IDOL"
    if s.endswith("USDT"):
        s = s[:-4]
    elif s.endswith("USD"):
        s = s[:-3]
    if not s or not s.isalnum():
        raise ValueError("could not parse symbol %r" % (raw,))
    return s


def bitget_symbol(base):
    return base + "USDT"


def gate_symbol(base):
    return base + "_USDT"


def okx_inst_id(base):
    return base + "-USDT-SWAP"


# ---------------------------------------------------------------------------
# Exchange fetchers
# ---------------------------------------------------------------------------

def fetch_bitget_tickers(client):
    """All Bitget USDT-futures tickers (funding included).

    Returns (tickers, error). Each ticker: dict with keys
    symbol, base, price, funding (decimal), funding_pct,
    change24h_pct, volume_usdt.
    """
    data, err = fetch_json(client, BITGET_TICKERS_URL)
    if err:
        return [], err
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], "unavailable: unexpected Bitget response shape"
    tickers = []
    for t in rows:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol[:-4]
        funding = _to_float(t.get("fundingRate"))
        change = _to_float(t.get("change24h"))
        tickers.append({
            "symbol": symbol,
            "base": base,
            "price": _to_float(t.get("lastPr")),
            "funding": funding,
            "funding_pct": (funding * 100.0) if funding is not None else None,
            "change24h_pct": (change * 100.0) if change is not None else None,
            "volume_usdt": _to_float(t.get("usdtVolume"))
            or _to_float(t.get("quoteVolume")),
        })
    return tickers, None


def fetch_gate_contracts(client):
    """All Gate.io USDT-futures contracts.

    Returns (dict base -> funding_pct, error).
    """
    data, err = fetch_json(client, GATE_CONTRACTS_URL)
    if err:
        return {}, err
    if not isinstance(data, list):
        return {}, "unavailable: unexpected Gate response shape"
    out = {}
    for c in data:
        name = c.get("name", "")
        if not name.endswith("_USDT"):
            continue
        funding = _to_float(c.get("funding_rate"))
        if funding is not None:
            out[name[:-5]] = funding * 100.0
    return out, None


def fetch_gate_funding(client, base):
    """Single Gate.io contract funding, in percent. (None, error) on failure."""
    data, err = fetch_json(client, GATE_CONTRACT_URL.format(symbol=gate_symbol(base)))
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "unavailable: unexpected Gate response shape"
    funding = _to_float(data.get("funding_rate"))
    if funding is None:
        return None, "not listed on Gate"
    return funding * 100.0, None


def fetch_binance_funding(client, base):
    """Single Binance funding rate, in percent. (None, error) on failure."""
    data, err = fetch_json(
        client, BINANCE_PREMIUM_URL.format(symbol=bitget_symbol(base))
    )
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "unavailable: unexpected Binance response shape"
    funding = _to_float(data.get("lastFundingRate"))
    if funding is None:
        return None, "not listed on Binance"
    return funding * 100.0, None


def fetch_bybit_funding(client, base):
    """Single Bybit funding rate, in percent. (None, error) on failure."""
    data, err = fetch_json(
        client, BYBIT_TICKERS_URL.format(symbol=bitget_symbol(base))
    )
    if err:
        return None, err
    result = data.get("result") if isinstance(data, dict) else None
    items = result.get("list") if isinstance(result, dict) else None
    if not items:
        return None, "not listed on Bybit"
    funding = _to_float(items[0].get("fundingRate"))
    if funding is None:
        return None, "not listed on Bybit"
    return funding * 100.0, None


def fetch_okx_funding(client, base):
    """Single OKX funding rate, in percent. (None, error) on failure."""
    data, err = fetch_json(
        client, OKX_FUNDING_URL.format(inst_id=okx_inst_id(base))
    )
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "unavailable: unexpected OKX response shape"
    if data.get("code") not in ("0", 0):
        return None, "not listed on OKX"
    items = data.get("data") or []
    if not items:
        return None, "not listed on OKX"
    funding = _to_float(items[0].get("fundingRate"))
    if funding is None:
        return None, "not listed on OKX"
    return funding * 100.0, None


# ---------------------------------------------------------------------------
# Core: scan + deep check
# ---------------------------------------------------------------------------

def scan(top=10, min_volume=DEFAULT_MIN_VOLUME_USDT):
    """Scan the Bitget universe, rank by most negative funding.

    Returns {"generated_at_utc", "exchanges": {...}, "results": [...]}.
    Results are sorted most-negative funding first. Exchanges that are
    down are reported in "exchanges", never fatal.
    """
    client = make_client()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_bitget = pool.submit(fetch_bitget_tickers, client)
            fut_gate = pool.submit(fetch_gate_contracts, client)
            tickers, bitget_err = fut_bitget.result()
            gate_map, gate_err = fut_gate.result()
    finally:
        client.close()

    exchanges = {
        "bitget": "ok" if not bitget_err else bitget_err,
        "gate": "ok" if not gate_err else gate_err,
    }

    results = []
    for t in tickers:
        funding_pct = t["funding_pct"]
        if funding_pct is None:
            continue
        vol = t["volume_usdt"]
        if vol is not None and vol < min_volume:
            continue  # illiquid dust — funding there means nothing
        gate_funding = gate_map.get(t["base"])
        gate_agrees = gate_funding is not None and gate_funding < 0
        score, breakdown = squeeze_score(
            funding_pct, t["change24h_pct"], vol, gate_agrees
        )
        results.append({
            "symbol": t["symbol"],
            "base": t["base"],
            "price": t["price"],
            "funding_pct": round(funding_pct, 4),
            "funding_gate_pct": (
                round(gate_funding, 4) if gate_funding is not None else None
            ),
            "change24h_pct": (
                round(t["change24h_pct"], 2)
                if t["change24h_pct"] is not None else None
            ),
            "volume_usdt": round(vol, 2) if vol is not None else None,
            "score": score,
            "score_breakdown": breakdown,
            "read": read_label(funding_pct),
            "gate_agrees": gate_agrees,
        })

    # Most negative funding first = most crowded shorts on top.
    results.sort(key=lambda r: r["funding_pct"])
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "exchanges": exchanges,
        "results": results[:top],
    }


def check_symbol(raw_symbol):
    """Deep-check one coin across all five exchanges.

    Returns a dict with per-exchange funding (percent) or an error
    string per exchange. Never raises for a bad symbol — returns
    {"error": ...} instead.
    """
    try:
        base = normalize_base(raw_symbol)
    except ValueError as exc:
        return {"error": str(exc)}

    client = make_client()
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {
                "bitget": pool.submit(fetch_bitget_tickers, client),
                "gate": pool.submit(fetch_gate_funding, client, base),
                "binance": pool.submit(fetch_binance_funding, client, base),
                "bybit": pool.submit(fetch_bybit_funding, client, base),
                "okx": pool.submit(fetch_okx_funding, client, base),
            }
            tickers, bitget_err = futs["bitget"].result()
            gate_funding, gate_err = futs["gate"].result()
            binance_funding, binance_err = futs["binance"].result()
            bybit_funding, bybit_err = futs["bybit"].result()
            okx_funding, okx_err = futs["okx"].result()
    finally:
        client.close()

    bitget_row = next((t for t in tickers if t["base"] == base), None)
    if bitget_row is None and gate_funding is None:
        return {
            "error": "Symbol %r not found on Bitget or Gate futures "
                     "(check the spelling — e.g. IDOL, HOT, RSR)." % (raw_symbol,)
        }

    funding = {}
    errors = {}
    if bitget_row is not None and bitget_row["funding_pct"] is not None:
        funding["bitget"] = round(bitget_row["funding_pct"], 4)
    else:
        errors["bitget"] = bitget_err or "not listed on Bitget"

    if gate_funding is not None:
        funding["gate"] = round(gate_funding, 4)
    else:
        errors["gate"] = gate_err or "not listed on Gate"

    for name, value, err in (
        ("binance", binance_funding, binance_err),
        ("bybit", bybit_funding, bybit_err),
        ("okx", okx_funding, okx_err),
    ):
        if value is not None:
            funding[name] = round(value, 4)
        else:
            errors[name] = err or "not listed"

    main_funding = funding.get("bitget", funding.get("gate"))
    gate_agrees = (
        funding.get("gate") is not None and funding["gate"] < 0
    )
    score, breakdown = squeeze_score(
        main_funding,
        bitget_row["change24h_pct"] if bitget_row else None,
        bitget_row["volume_usdt"] if bitget_row else None,
        gate_agrees,
    )
    return {
        "symbol": bitget_symbol(base),
        "base": base,
        "price": bitget_row["price"] if bitget_row else None,
        "change24h_pct": (
            round(bitget_row["change24h_pct"], 2)
            if bitget_row and bitget_row["change24h_pct"] is not None else None
        ),
        "volume_usdt": (
            round(bitget_row["volume_usdt"], 2)
            if bitget_row and bitget_row["volume_usdt"] is not None else None
        ),
        "funding_pct": funding,
        "funding_errors": errors,
        "score": score,
        "score_breakdown": breakdown,
        "read": read_label(main_funding),
    }


def exchange_status():
    """Light reachability check of all five exchanges.

    Returns {"bitget": "ok"|"unavailable: ...", ...}. Used by the
    squeeze_radar_status MCP tool.
    """
    client = make_client(timeout=10.0)
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futs = {
                "bitget": pool.submit(fetch_bitget_tickers, client),
                "gate": pool.submit(fetch_gate_funding, client, "BTC"),
                "binance": pool.submit(fetch_binance_funding, client, "BTC"),
                "bybit": pool.submit(fetch_bybit_funding, client, "BTC"),
                "okx": pool.submit(fetch_okx_funding, client, "BTC"),
            }
            _, bitget_err = futs["bitget"].result()
            _, gate_err = futs["gate"].result()
            _, binance_err = futs["binance"].result()
            _, bybit_err = futs["bybit"].result()
            _, okx_err = futs["okx"].result()
    finally:
        client.close()
    return {
        "bitget": "ok" if not bitget_err else bitget_err,
        "gate": "ok" if not gate_err else gate_err,
        "binance": "ok" if not binance_err else binance_err,
        "bybit": "ok" if not bybit_err else bybit_err,
        "okx": "ok" if not okx_err else okx_err,
    }


# ---------------------------------------------------------------------------
# CLI presentation
# ---------------------------------------------------------------------------

def _fmt_pct(value, decimals=4):
    if value is None:
        return "  n/a  "
    return "%+.{}f%%".format(decimals) % value


def _fmt_num(value, decimals=2):
    if value is None:
        return "n/a"
    return "%.*f" % (decimals, value)


def print_scan_table(report, top):
    print("SqueezeRadar v%s — most crowded shorts (Bitget universe, %s)"
          % (__version__, report["generated_at_utc"][:19] + "Z"))
    for name, status in report["exchanges"].items():
        print("  exchange %-8s: %s" % (name, status))
    print()
    print("%-3s %-10s %12s %12s %12s %9s %6s  %s" % (
        "#", "Symbol", "Price", "Funding", "Gate fund.", "24h chg", "Score", "Read"
    ))
    print("-" * 88)
    for i, r in enumerate(report["results"], 1):
        print("%-3d %-10s %12s %12s %12s %9s %6d  %s" % (
            i,
            r["symbol"],
            _fmt_num(r["price"], 6 if (r["price"] or 0) < 1 else 2),
            _fmt_pct(r["funding_pct"]),
            _fmt_pct(r["funding_gate_pct"]),
            _fmt_pct(r["change24h_pct"], 2),
            r["score"],
            r["read"],
        ))
    print()
    print("Funding settles every 4-8h — this is a SLOW crowding filter,")
    print("not an entry trigger. Crowded shorts can stay crowded for days.")
    print("Not financial advice.")


def print_symbol_report(rep):
    if "error" in rep:
        print("Error: %s" % rep["error"])
        return 2
    print("SqueezeRadar v%s — deep check: %s" % (__version__, rep["symbol"]))
    print("  price: %s USDT   24h change: %s   24h volume: %s USDT" % (
        _fmt_num(rep["price"], 6 if (rep["price"] or 0) < 1 else 2),
        _fmt_pct(rep["change24h_pct"], 2),
        _fmt_num(rep["volume_usdt"], 0),
    ))
    print()
    print("  %-8s %s" % ("Exchange", "Funding rate"))
    print("  " + "-" * 30)
    for name in ("bitget", "gate", "binance", "bybit", "okx"):
        if name in rep["funding_pct"]:
            print("  %-8s %s" % (name, _fmt_pct(rep["funding_pct"][name])))
        else:
            print("  %-8s %s" % (name, rep["funding_errors"].get(name, "n/a")))
    print()
    print("  Squeeze score: %d/100  (%s)" % (rep["score"], rep["read"]))
    print("  breakdown: funding %(funding)s + momentum %(momentum)s"
          " + liquidity %(liquidity)s + agreement %(agreement)s"
          % rep["score_breakdown"])
    print()
    print("Funding settles every 4-8h — slow crowding filter, not an entry")
    print("trigger. Not financial advice.")
    return 0


# ---------------------------------------------------------------------------
# MCP server (stdio) — lazy import so the CLI works without mcp installed
# ---------------------------------------------------------------------------

MCP_TOOLS = [
    {
        "name": "scan_crowded_shorts",
        "description": "Scan Bitget USDT-futures for the most crowded shorts "
                       "(most negative funding rates) with squeeze scores.",
        "parameters": {
            "top": "number of results to return (default 10, max 50)",
        },
    },
    {
        "name": "check_funding",
        "description": "Deep-check one coin's funding rate across Bitget, "
                       "Gate, Binance, Bybit and OKX, with a squeeze score.",
        "parameters": {
            "symbol": "coin symbol, e.g. IDOL, IDOLUSDT, idol/usdt",
        },
    },
    {
        "name": "squeeze_radar_status",
        "description": "Check which exchanges the radar can reach right now.",
        "parameters": {},
    },
]


def dump_tools():
    print(json.dumps({"server": "squeeze-radar", "version": __version__,
                      "tools": MCP_TOOLS}, indent=2))


def run_mcp_server():
    """Run the MCP server over stdio.

    mcp 2.x renamed FastMCP to MCPServer; support both so the server
    works with either major version.
    """
    try:
        from mcp.server.mcpserver import MCPServer as _Server
    except ImportError:
        from mcp.server.fastmcp import FastMCP as _Server

    mcp = _Server("squeeze-radar")

    @mcp.tool()
    def scan_crowded_shorts(top: int = 10) -> str:
        """Scan Bitget USDT-futures for the most crowded shorts
        (most negative funding rates) with squeeze scores."""
        top = max(1, min(int(top), 50))
        return json.dumps(scan(top=top), indent=2)

    @mcp.tool()
    def check_funding(symbol: str) -> str:
        """Deep-check one coin's funding rate across Bitget, Gate,
        Binance, Bybit and OKX, with a squeeze score."""
        return json.dumps(check_symbol(symbol), indent=2)

    @mcp.tool()
    def squeeze_radar_status() -> str:
        """Check which exchanges the radar can reach right now."""
        return json.dumps({
            "server": "squeeze-radar",
            "version": __version__,
            "exchanges": exchange_status(),
        }, indent=2)

    mcp.run()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="squeeze_radar",
        description="Find crowded shorts before the squeeze — "
                    "funding-rate radar for crypto perpetual futures.",
    )
    p.add_argument("--top", type=int, default=10,
                   help="how many crowded shorts to show (default: 10)")
    p.add_argument("--symbol", type=str, default=None,
                   help="deep-check one coin, e.g. IDOLUSDT")
    p.add_argument("--json", action="store_true",
                   help="output machine-readable JSON instead of a table")
    p.add_argument("--watch", nargs="?", const=15, type=int, default=None,
                   metavar="MINUTES",
                   help="re-scan every N minutes (default 15) until Ctrl+C")
    p.add_argument("--min-volume", type=float, default=DEFAULT_MIN_VOLUME_USDT,
                   help="ignore coins below this 24h USDT volume "
                        "(default: 100000)")
    p.add_argument("--mcp", action="store_true",
                   help="run as an MCP server over stdio")
    p.add_argument("--dump-tools", action="store_true",
                   help="print the MCP tool list as JSON and exit")
    return p


def main(argv=None):
    # Line-buffer stdout so --watch output appears immediately even
    # when redirected to a file or pipe (not just on a TTY).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    args = build_parser().parse_args(argv)

    if args.dump_tools:
        dump_tools()
        return 0

    if args.mcp:
        if httpx is None:
            print("Error: httpx is not installed. Run: pip install -r requirements.txt",
                  file=sys.stderr)
            return 1
        try:
            run_mcp_server()
        except ImportError:
            print("Error: the mcp package is not installed. "
                  "Run: pip install -r requirements.txt", file=sys.stderr)
            return 1
        return 0

    if httpx is None:
        print("Error: httpx is not installed. Run: pip install -r requirements.txt",
              file=sys.stderr)
        return 1

    if args.symbol:
        rep = check_symbol(args.symbol)
        if args.json:
            print(json.dumps(rep, indent=2))
            return 0 if "error" not in rep else 2
        return print_symbol_report(rep)

    top = max(1, min(args.top, 50))
    if args.watch is None:
        report = scan(top=top, min_volume=args.min_volume)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_scan_table(report, top)
        return 0

    # Watch mode: re-scan every N minutes until Ctrl+C.
    interval = max(1, args.watch) * 60
    try:
        while True:
            print("=" * 88)
            print("SqueezeRadar watch — %s UTC (every %d min, Ctrl+C to stop)"
                  % (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                     args.watch))
            report = scan(top=top, min_volume=args.min_volume)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print_scan_table(report, top)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatch stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
