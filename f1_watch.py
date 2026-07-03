"""
F1 FORWARD-TEST İZLEYİCİSİ (GitHub Actions)
============================================
Her 4h bar kapanışında çalışır (cron). Perp'i olan spot USDT altcoin'lerde
F1 sinyalini (sürdürülen hacim + sessiz zemin) tarar. Sinyal düşerse:
  * Telegram'a alarm atar (TG_TOKEN secret'ı varsa; yoksa sessizce loglar)
  * f1_log.json'a KAĞIT işlem yazar (giriş = sinyal barı kapanışı)
Sonraki koşularda 1 günü dolan kağıt işlemlerin sonucunu kendisi doldurur
(çıkış = 6 bar sonra kapanış; pnl = -(cikis/giris-1) - %0.15 maliyet+funding).
Gerçek emir YOK, anahtar YOK. Bu dosya = F1'in canlı sicili.

Sinyal aritmetiği backtest ile BİREBİR: MULT=3, CONSEC=6, BASE=42, REC=6,
V1<0.75 (patlama-öncesi zemin, CONSEC kaydırmalı), cooldown=LA=6 bar.
"""
import json, os, time, urllib.request, urllib.parse, datetime as dt

# ── ön-kayıtlı sabitler (backtest ile aynı) ──
BASE, CONSEC, REC, LA = 42, 6, 6, 6
MULT, V1_LO           = 3.0, 0.75
FEE                   = 0.0015                    # %0.1 RT + %0.05 funding (hüküm senaryosu)
BAR_MS                = 4 * 3600 * 1000
EXCLUDE = {"BTC","ETH","USDC","DAI","TUSD","USDD","FDUSD","BUSD","WBTC","EURT"}

OKX      = "https://www.okx.com/api/v5"
TG_TOKEN = os.environ.get("TG_TOKEN", "")
CHAT_ID  = os.environ.get("TG_CHAT", "481345337")
LOG_YOL, STATE_YOL = "f1_log.json", "f1_state.json"

def get(url, deneme=4):
    for k in range(deneme):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "f1-watch/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
            if d.get("code") == "0": return d.get("data", [])
        except Exception:
            pass
        time.sleep(1 + k)
    return None

def evren():
    spot = get(f"{OKX}/public/instruments?instType=SPOT") or []
    swap = get(f"{OKX}/public/instruments?instType=SWAP") or []
    perp = {x.get("ctValCcy") or x["instId"].split("-")[0]
            for x in swap if x.get("instId","").endswith("-USDT-SWAP") and x.get("state") == "live"}
    return {x["baseCcy"]: x["instId"] for x in spot
            if x.get("quoteCcy") == "USDT" and x.get("state") == "live"
            and x["baseCcy"] not in EXCLUDE and x["baseCcy"] in perp}

def mumlar(inst_id, limit=100):
    """Kapanmış barlar, ESKİ->YENİ: (ts, c, h, l, vq) listesi."""
    d = get(f"{OKX}/market/candles?instId={inst_id}&bar=4H&limit={limit}")
    if not d: return None
    rows = [(int(r[0]), float(r[4]), float(r[2]), float(r[3]), float(r[7]))
            for r in d if r[8] == "1"]
    rows.sort()
    return rows

def rmean(x, w, i):
    return sum(x[i - w + 1: i + 1]) / w

def f1_kontrol(bars):
    """Son kapanmış barda F1 var mı? Varsa dict döner."""
    n = len(bars)
    if n < BASE + CONSEC + 2: return None
    ts = [b[0] for b in bars]; c = [b[1] for b in bars]
    h  = [b[2] for b in bars]; l = [b[3] for b in bars]; vq = [b[4] for b in bars]
    i = n - 1
    for k in range(i - CONSEC + 1, i + 1):                 # ardışık 6 bar > 3x taban
        if k - BASE + 1 < 0 or not vq[k] > MULT * rmean(vq, BASE, k):
            return None
    j = i - CONSEC                                          # patlama-ÖNCESİ zemin
    if j - BASE + 1 < 0: return None
    rng = [max(h[k] - l[k], 0.0) / c[k] for k in range(n)]
    v1 = rmean(rng, REC, j) / max(rmean(rng, BASE, j), 1e-12)
    if v1 < V1_LO:
        return dict(ts=ts[i], giris=c[i], v1=round(v1, 3))
    return None

def oku(yol, varsayilan):
    try:
        with open(yol) as f: return json.load(f)
    except Exception:
        return varsayilan

def yaz(yol, veri):
    tmp = yol + ".tmp"
    with open(tmp, "w") as f: json.dump(veri, f, ensure_ascii=False, indent=1)
    os.replace(tmp, yol)

def tg(metin):
    if not TG_TOKEN:
        print("[TG yok] " + metin.replace("\n", " | ")); return
    try:
        q = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": metin, "parse_mode": "HTML"})
        urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage?{q}", timeout=15)
    except Exception as e:
        print("TG hata:", e)

def ana():
    uni = evren()
    print(f"Evren: {len(uni)} coin (spot∩perp)")
    state = oku(STATE_YOL, {})
    log   = oku(LOG_YOL, [])
    simdi = int(time.time() * 1000)
    yeni, kapanan = [], []

    # 1) TARAMA
    for coin, inst in sorted(uni.items()):
        bars = mumlar(inst)
        if not bars: continue
        s = f1_kontrol(bars)
        if s and s["ts"] - int(state.get(coin, 0)) >= LA * BAR_MS:
            state[coin] = s["ts"]
            kayit = dict(coin=coin, giris_ts=s["ts"], giris=s["giris"], v1=s["v1"],
                         cikis_ts=s["ts"] + LA * BAR_MS, cikis=None, pnl=None)
            log.append(kayit); yeni.append(kayit)
        time.sleep(0.12)

    # 2) SONUÇ DOLDURMA (1 günü dolan kağıt işlemler)
    for e in log:
        if e["pnl"] is not None or simdi < e["cikis_ts"] + 10 * 60 * 1000: continue
        inst = uni.get(e["coin"], f"{e['coin']}-USDT")
        bars = mumlar(inst)
        cikis = next((b[1] for b in (bars or []) if b[0] == e["cikis_ts"]), None)
        if cikis is None:
            if simdi > e["cikis_ts"] + 3 * 86400_000: e["pnl"] = "VERI_YOK"
            continue
        e["cikis"] = cikis
        e["pnl"] = round(-(cikis / e["giris"] - 1) - FEE, 5)
        kapanan.append(e)

    yaz(STATE_YOL, state); yaz(LOG_YOL, log)

    # 3) BİLDİRİM
    for k in yeni:
        t = dt.datetime.fromtimestamp(k["giris_ts"]/1000, dt.timezone.utc).strftime("%d.%m %H:%M UTC")
        tg(f"🔻 <b>F1 SİNYAL · {k['coin']}</b>\n{t} · giriş {k['giris']}\n"
           f"zemin v1={k['v1']} · kağıt-short, 1 gün\n"
           f"https://www.okx.com/trade-swap/{k['coin'].lower()}-usdt-swap")
    for k in kapanan:
        isaret = "✅" if isinstance(k["pnl"], float) and k["pnl"] > 0 else "🔴"
        tg(f"{isaret} <b>F1 SONUÇ · {k['coin']}</b>\npnl: {k['pnl']*100:+.2f}% (kağıt)"
           if isinstance(k["pnl"], float) else f"⚪ F1 SONUÇ · {k['coin']}: veri yok")
    biten = [e for e in log if isinstance(e["pnl"], float)]
    if biten and (yeni or kapanan):
        top = sum(e["pnl"] for e in biten)
        tg(f"📒 F1 sicili: {len(biten)} işlem · toplam {top*100:+.1f}% · "
           f"isabet %{sum(e['pnl']>0 for e in biten)/len(biten)*100:.0f}")
    print(f"Yeni sinyal: {len(yeni)} | Kapanan: {len(kapanan)} | Sicil: {len(biten)} işlem")

if __name__ == "__main__":
    ana()
