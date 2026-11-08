# 📡 SqueezeRadar

> **Find crowded shorts BEFORE the squeeze.**
>
> **One command. Five exchanges. Zero API keys.**
> SqueezeRadar scans crypto perpetual futures, ranks coins by how negative their funding rate is, and scores each one 0–100 for short-squeeze potential.

[![GitHub stars](https://img.shields.io/github/stars/muslim-ahmad-wagay/SqueezeRadar?style=social)](https://github.com/muslim-ahmad-wagay/SqueezeRadar)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-stdio-green.svg)](https://modelcontextprotocol.io/)
[![Tools](https://img.shields.io/badge/MCP_tools-3-orange.svg)](#-easy-usage--just-ask-your-ai)

**⭐ If this saves you from one bad short, star the repo — it takes 2 seconds and helps more traders find the radar.**

When funding goes very negative, shorts are paying longs to stay short. That's crowded — and crowded shorts are squeeze fuel. SqueezeRadar watches funding across Bitget, Gate, Binance, Bybit and OKX, and shows you where the pile-up is *before* price rips through it.

**No keys. No signup. No orders. Pure read-only public data.**

---

## ⚡ 60-Second Setup

```bash
git clone https://github.com/muslim-ahmad-wagay/SqueezeRadar.git
cd SqueezeRadar
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then run it:

```bash
.venv/bin/python squeeze_radar.py --top 20
```

**Done.** That's the whole setup — no API keys, no account, no config file.

```text
SqueezeRadar v1.0.0 — most crowded shorts (Bitget universe)
  exchange bitget  : ok
  exchange gate    : ok

#   Symbol            Price      Funding   Gate fund.   24h chg  Score  Read
----------------------------------------------------------------------------------------
1   CYBERUSDT      0.280100     -0.5675%     -1.1169%   -10.31%     77  crowded shorts
2   BRETTUSDT      0.003713     -0.3856%     +0.0050%    -5.13%     61  crowded shorts
3   RSRUSDT        0.001743     -0.2907%     -0.3886%   +11.23%     95  crowded shorts
...
```

*(Example output from a real scan, 2026-11-08. Your numbers will differ — funding moves every 4–8 hours.)*

---

## 🎯 Easy Usage — just ask your AI

SqueezeRadar is also an MCP server. Plug it into Claude Desktop, Claude Code, Cursor, Windsurf, VS Code or ChatGPT, and talk to it like a trader:

| What you want | Copy-paste this prompt |
|---|---|
| Find the squeeze | `Scan for the most crowded shorts right now — top 10 with funding and squeeze scores` |
| Check one coin | `Is IDOL crowded? Check its funding across all exchanges` |
| Hunt the rip | `Which coin has the most negative funding AND is already pumping?` |
| Cross-check | `Compare RSR funding on Bitget vs Gate — do both exchanges agree?` |
| Before you short | `I'm thinking of shorting HOT — check the funding first and tell me if it's crowded` |
| Exchange health | `Which exchanges can the radar reach right now?` |

### CLI cheatsheet

```bash
python squeeze_radar.py --top 20            # ranked table, most crowded first
python squeeze_radar.py --symbol IDOLUSDT    # deep-check one coin, all 5 exchanges
python squeeze_radar.py --top 10 --json      # machine-readable JSON
python squeeze_radar.py --top 10 --watch 15  # re-scan every 15 minutes
python squeeze_radar.py --mcp                # run as an MCP server (stdio)
```

Symbols are forgiving: `IDOL`, `IDOLUSDT`, `idol/usdt`, `IDOL/USDT:USDT` all work. Typos fail cleanly with a clear error, never a crash.

---

## 🏆 Why traders love it

- **Sees the pile-up first** — funding tells you where shorts are trapped before price moves.
- **Five exchanges, one command** — Bitget, Gate, Binance, Bybit, OKX. If one is geo-blocked where you live, the radar says so and keeps scanning the rest.
- **Honest score, not hype** — a transparent 0–100 formula you can read in 2 minutes. No black box, no "guaranteed squeeze".
- **Zero keys, zero risk** — read-only public data. It cannot place orders, move funds, or touch your accounts. There is nothing to leak.
- **Works everywhere** — CLI for the terminal, MCP for Claude, ChatGPT, Cursor and friends.
- **Free forever** — MIT licensed. No subscription, no paywall, no "pro tier".

---

## 🧮 How the squeeze score works

Score 0–100, built from four honest pieces:

| Piece | Max | What it measures |
|---|---|---|
| Funding | 60 | How negative the funding rate is. −0.30% earns the full 60. Only *negative* funding scores — crowded longs score 0 here. |
| Momentum | 20 | 24h price change. A coin already pumping +10% with crowded shorts is hotter. |
| Liquidity | 10 | 24h USDT volume. Real signal on liquid coins scores higher than dust. Coins under $100k volume are ignored entirely. |
| Agreement | 10 | Gate.io funding is *also* negative — two exchanges agreeing beats one. |

**Read labels:** funding ≤ −0.10% → `crowded shorts` · between −0.10% and +0.10% → `neutral` · ≥ +0.10% → `crowded longs`.

**The honest part:** funding settles every 4–8 hours, so this is a **slow crowding filter, never a 15-minute entry trigger**. A crowded short can stay crowded for days, and price can keep falling while funding is negative. The score is a heuristic, not a prediction. **This is not financial advice.**

---

## 🌐 What it scans

| Exchange | Role | Status |
|---|---|---|
| Bitget | Primary universe — one call returns ~884 USDT-futures tickers with funding included | Works everywhere tested |
| Gate.io | Cross-check — ~1080 contracts with funding | Works everywhere tested |
| OKX | Per-symbol funding (listed coins only) | Works everywhere tested |
| Binance | Per-symbol funding | Geo-blocked (HTTP 451) in some regions — reported, never fatal |
| Bybit | Per-symbol funding | CloudFront-blocked (HTTP 403) in some regions — reported, never fatal |

If an exchange is unreachable from your location, SqueezeRadar prints that in the output and keeps going. It never crashes the whole scan because one exchange is down, and it never invents numbers — every figure comes from a live API response.

---

## 🔌 Connect it (MCP — Claude, ChatGPT, Cursor…)

Paste this into your Claude Desktop config (`claude_desktop_config.json`), fixing the path — the same JSON works in Cursor, Windsurf, VS Code, and ChatGPT's desktop app (developer mode):

```json
{
  "mcpServers": {
    "squeeze-radar": {
      "command": "/absolute/path/to/SqueezeRadar/.venv/bin/python",
      "args": ["/absolute/path/to/SqueezeRadar/squeeze_radar.py", "--mcp"]
    }
  }
}
```

Restart the app. Three tools appear:

| Tool | What it does |
|---|---|
| `scan_crowded_shorts` | Ranked scan of the most crowded shorts, with squeeze scores |
| `check_funding` | Deep-check one coin's funding across all 5 exchanges |
| `squeeze_radar_status` | Which exchanges the radar can reach right now |

Check the tools without starting the server:

```bash
python squeeze_radar.py --dump-tools
```

---

## 🛡️ Safety

- **Read-only by design.** Only public market-data endpoints. No keys, no logins, no orders, no trading — the code literally cannot move money.
- **No credentials in this repo.** No keys, secrets, passwords or tokens anywhere — grep it yourself.
- **No invented numbers.** Every funding rate comes from a live exchange response. Unreachable exchanges are labelled, not silently skipped.
- **No guaranteed squeezes.** A high score means crowded shorts, not a certain rip. Do your own research, size positions, use stop-losses.
- **This is not financial advice.** The radar shows you the market; it does not tell you what to buy or short.

---

## 🧪 Tests

```bash
# Live smoke test: real top-5 scan + IDOL check + bad-symbol handling
python tests/smoke_test.py
```

The test hits the real public APIs and asserts the ranking is sorted most-negative first, funding is numeric, scores are 0–100, and a garbage symbol fails cleanly.

---

## 📣 Share the radar

Help it go viral — copy, paste, post:

**X / Twitter:**

> Shorts are piling up somewhere right now — and you can't see it.
>
> 📡 SqueezeRadar scans 5 exchanges, ranks coins by funding rate and scores the squeeze before it happens. One command, zero API keys.
>
> https://github.com/muslim-ahmad-wagay/SqueezeRadar

**Reddit (r/CryptoCurrency, r/algotrading, r/ClaudeAI):**

> Title: SqueezeRadar — find crowded shorts before the squeeze (5 exchanges, 0 keys)
>
> Body: Funding rates show where shorts are trapped. This tool scans Bitget + Gate + Binance + Bybit + OKX, ranks the most negative funding, and scores each coin 0–100 for squeeze potential. CLI + MCP for Claude/ChatGPT. Free, MIT, no keys: https://github.com/muslim-ahmad-wagay/SqueezeRadar

**Discord / Telegram:**

> 📡 SqueezeRadar — crowded shorts radar for crypto perps. 5 exchanges, 60-second setup, no API keys. https://github.com/muslim-ahmad-wagay/SqueezeRadar

---

## 🤝 Contributing

PRs welcome — new exchanges, better scoring, more tests. Rules:

1. Read-only public data only. Never add a tool that moves funds.
2. Never invent a number — every figure must come from a live API response.
3. An unreachable exchange is reported, never fatal.
4. Keep the honesty notes intact: funding is a slow filter, not a trigger, and nothing here is financial advice.

---

## 💛 Support

SqueezeRadar is free and open source (MIT). If it helps you trade smarter, tips keep the radar running:

**USDT (TRC20):** `TPWtGkxHsrNtUY7cAuPtZzSX7otzE44zyo`

Donations are voluntary and non-refundable. Never send funds to any other address claiming to be SqueezeRadar.

---

## 📜 License

MIT — see [LICENSE](LICENSE).
