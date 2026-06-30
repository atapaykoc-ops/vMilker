"""
OKX hacim verisi toplayıcı.
GitHub Actions tarafından saat başı çalıştırılır, veri.json üretir.
"""
import ccxt, numpy as np, json
from datetime import datetime, timezone

# her TF: (haftalık mum, aylık mum, çekilecek mum)
CFG = {"1h": (168, 720, 750), "4h": (42, 180, 200), "1d": (7, 30, 35)}
EXCLUDE = {"BTC", "ETH", "USDC", "DAI", "TUSD", "USDD", "FDUSD", "BUSD", "WBTC", "EURT"}

def vol_usd(bar):
    return bar[5] * bar[4]  # hacim(coin) * kapanış = USD hacim

def get_coin(ex, symbol):
    out = {"c": symbol.split("/")[0], "tf": {}}
    for tf, (week, month, fetch) in CFG.items():
        try:
            d = ex.fetch_ohlcv(symbol, tf, limit=fetch)
            if not d or len(d) < week + 2:
                return None
            v = [vol_usd(x) for x in d]
            out["tf"][tf] = {
                "cur": round(v[-1], 2),
                "wk": round(float(np.mean(v[-(week + 1):-1])), 2),
                "mo": round(float(np.mean(v[-(month + 1):-1])), 2) if len(v) > month else None,
            }
        except Exception:
            return None
    return out

def main():
    ex = ccxt.okx({"enableRateLimit": True})
    markets = ex.load_markets()
    usdt = [s for s in markets
            if s.endswith("/USDT") and markets[s].get("active") and markets[s]["type"] == "spot"]
    alts = [s for s in usdt if s.split("/")[0] not in EXCLUDE]
    print(f"{len(alts)} altcoin taranıyor...")

    coins = []
    for i, sym in enumerate(alts):
        r = get_coin(ex, sym)
        if r:
            coins.append(r)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(alts)} ({len(coins)} geçerli)...")

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(coins),
        "coins": coins,
    }
    with open("veri.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"✅ veri.json yazıldı: {len(coins)} coin")

if __name__ == "__main__":
    main()
