#!/usr/bin/env python3
"""SqueezeRadar smoke test — runs LIVE against the public exchange APIs.

Checks:
1. A top-5 scan returns real rows, sorted most-negative funding first,
   with numeric funding and a 0-100 score.
2. A single-symbol check (IDOLUSDT) returns real per-exchange funding.
3. A garbage symbol fails cleanly with an error dict, not a crash.
4. Binance/Bybit geo-blocks (451/403) are reported, never fatal.
5. Symbol normalization handles every input shape.

Run: python tests/smoke_test.py
Requires: httpx installed (pip install -r requirements.txt).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import squeeze_radar as sr  # noqa: E402


def test_normalize():
    cases = {
        "IDOL": "IDOL",
        "IDOLUSDT": "IDOL",
        "idol": "IDOL",
        "idol/usdt": "IDOL",
        "IDOL/USDT:USDT": "IDOL",
        "IDOL-USDT-SWAP": "IDOL",
        "IDOL_USDT": "IDOL",
        "  hot  ": "HOT",
    }
    for raw, expected in cases.items():
        got = sr.normalize_base(raw)
        assert got == expected, "normalize_base(%r) = %r, want %r" % (raw, got, expected)
    for bad in ("", "   ", "!!!", None):
        try:
            sr.normalize_base(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("normalize_base(%r) should raise ValueError" % (bad,))
    print("PASS normalize_base (%d cases)" % len(cases))


def test_scan_live():
    report = sr.scan(top=5)
    assert report["exchanges"]["bitget"] == "ok", \
        "Bitget must be reachable: %s" % report["exchanges"]["bitget"]
    rows = report["results"]
    assert len(rows) >= 1, "scan returned no rows"
    fundings = []
    for r in rows:
        assert isinstance(r["funding_pct"], (int, float)), "funding must be a number"
        assert -5.0 < r["funding_pct"] < 5.0, "funding out of sane range: %s" % r
        assert 0 <= r["score"] <= 100, "score out of range: %s" % r
        assert r["read"] in ("crowded shorts", "neutral", "crowded longs", "unknown")
        fundings.append(r["funding_pct"])
    assert fundings == sorted(fundings), \
        "ranking must be sorted most-negative first: %s" % fundings
    print("PASS live scan top-5 (%d rows, most negative %.4f%%)"
          % (len(rows), fundings[0]))
    for r in rows:
        print("     %-10s funding %+7.4f%%  gate %s  score %3d  %s"
              % (r["symbol"], r["funding_pct"],
                 ("%+7.4f%%" % r["funding_gate_pct"])
                 if r["funding_gate_pct"] is not None else "    n/a",
                 r["score"], r["read"]))
    return rows


def test_symbol_live():
    rep = sr.check_symbol("IDOLUSDT")
    assert "error" not in rep, "IDOL check failed: %s" % rep
    funding = rep["funding_pct"]
    assert "bitget" in funding, "Bitget funding missing for IDOL: %s" % rep
    assert isinstance(funding["bitget"], (int, float))
    assert -5.0 < funding["bitget"] < 5.0
    assert 0 <= rep["score"] <= 100
    # Geo-blocked exchanges must be reported as errors, not crashes.
    assert "binance" in rep["funding_errors"] or "binance" in funding
    assert "bybit" in rep["funding_errors"] or "bybit" in funding
    print("PASS live symbol check IDOLUSDT (Bitget %+.4f%%, Gate %s, score %d)"
          % (funding["bitget"],
             ("%+.4f%%" % funding["gate"]) if "gate" in funding else "n/a",
             rep["score"]))


def test_unknown_symbol():
    rep = sr.check_symbol("NOTACOIN123")
    assert "error" in rep, "unknown symbol must return an error dict, got %s" % rep
    print("PASS unknown symbol handled cleanly: %s" % rep["error"])


def main():
    print("SqueezeRadar smoke test — live APIs")
    test_normalize()
    test_scan_live()
    test_symbol_live()
    test_unknown_symbol()
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
