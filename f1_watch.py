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

İKİ KURAL (v2):
  donuk : MULT=3.0, V1<0.75  — orijinal, DONDURULMUŞ. Okuma eşiği: 10 işlem.
  genis : MULT=2.5, V1<0.85  — M2-WFO doğrulamalı geniş ağ. Okuma eşiği: 20 işlem.
Ortak: CONSEC=6, BASE=42, REC=6, cooldown=LA=6 bar (kural başına ayrı).
Donuk sinyaller genişin alt kümesidir; ikisi ateşlerse tek mesaj, iki sicil kaydı.
Eski format uyumu: etiketsiz eski kayıt/state "donuk" sayılır.
"""
import json, os, time, urllib.request, urllib.parse, datetime as dt

# ── ön-kayıtlı sabitler (backtest ile aynı) ──
BASE, CONSEC, REC, LA = 42, 6, 6, 6
KURALLAR = {"donuk": (3.0, 0.75), "genis": (2.5, 0.85)}
FEE                   = 0.0015                    # %0.1 RT + %0.05 funding (hüküm senaryosu)
BAR_MS                = 4 * 3600 * 1000
EXCLUDE = {"BTC","ETH","USDC","DAI","TUSD","USDD","FDUSD","BUSD","WBTC","EURT"}

OKX      = "https://www.okx.com/api/v5"
DERIN_MS = 2 * 365 * 24 * 3600 * 1000            # backtest evreni şartı: >=2 yıl geçmiş
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

def derin_mi(coin, inst, state, simdi):
    """Coin >=2 yıl geçmişe sahip mi? (backtest popülasyonu) Bir kez ölçülür, hafızaya alınır."""
    if state.get(f"yas:{coin}") is True: return True
    d = get(f"{OKX}/market/history-candles?instId={inst}&bar=4H&after={simdi - DERIN_MS}&limit=1")
    if d:
        state[f"yas:{coin}"] = True
        return True
    return False

def rmean(x, w, i):
    return sum(x[i - w + 1: i + 1]) / w

def f1_kontrol(bars, mult, v1_lo):
    """Son kapanmış barda verilen kurala göre F1 var mı?"""
    n = len(bars)
    if n < BASE + CONSEC + 2: return None
    ts = [b[0] for b in bars]; c = [b[1] for b in bars]
    h  = [b[2] for b in bars]; l = [b[3] for b in bars]; vq = [b[4] for b in bars]
    i = n - 1
    for k in range(i - CONSEC + 1, i + 1):                 # ardışık 6 bar > 3x taban
        if k - BASE + 1 < 0 or not vq[k] > mult * rmean(vq, BASE, k):
            return None
    j = i - CONSEC                                          # patlama-ÖNCESİ zemin
    if j - BASE + 1 < 0: return None
    rng = [max(h[k] - l[k], 0.0) / c[k] for k in range(n)]
    v1 = rmean(rng, REC, j) / max(rmean(rng, BASE, j), 1e-12)
    if v1 < v1_lo:
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
    state = {(k if ":" in k else f"donuk:{k}"): v for k, v in state.items()}   # eski format göçü
    log   = oku(LOG_YOL, [])
    for e in log: e.setdefault("kural", "donuk"); e.setdefault("derin", True)
    simdi = int(time.time() * 1000)
    yeni, kapanan = [], []

    # 1) TARAMA (tek fetch, iki kural)
    for coin, inst in sorted(uni.items()):
        bars = mumlar(inst)
        if not bars: continue
        atesler = []
        derin = None
        for kural, (mult, v1lo) in KURALLAR.items():
            s = f1_kontrol(bars, mult, v1lo)
            if s and s["ts"] - int(state.get(f"{kural}:{coin}", 0)) >= LA * BAR_MS:
                state[f"{kural}:{coin}"] = s["ts"]
                if derin is None: derin = derin_mi(coin, inst, state, simdi)
                kayit = dict(kural=kural, coin=coin, derin=derin,
                             giris_ts=s["ts"], giris=s["giris"], v1=s["v1"],
                             cikis_ts=s["ts"] + LA * BAR_MS, cikis=None, pnl=None)
                log.append(kayit); atesler.append(kayit)
        if atesler: yeni.append(atesler)
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
    for grup in yeni:
        k = grup[0]
        etiket = "+".join(g["kural"].upper() for g in grup)
        t = dt.datetime.fromtimestamp(k["giris_ts"]/1000, dt.timezone.utc).strftime("%d.%m %H:%M UTC")
        gen = "" if k.get("derin", True) else " · genç coin (sicil-dışı)"
        tg(f"🔻 <b>F1 SİNYAL · {k['coin']}</b> [{etiket}]{gen}\n{t} · giriş {k['giris']}\n"
           f"zemin v1={k['v1']} · kağıt-short, 1 gün\n"
           f"https://www.okx.com/trade-swap/{k['coin'].lower()}-usdt-swap")
    for k in kapanan:
        isaret = "✅" if isinstance(k["pnl"], float) and k["pnl"] > 0 else "🔴"
        tg(f"{isaret} <b>F1 SONUÇ · {k['coin']}</b> [{k['kural'].upper()}]\npnl: {k['pnl']*100:+.2f}% (kağıt)"
           if isinstance(k["pnl"], float) else f"⚪ F1 SONUÇ · {k['coin']} [{k['kural'].upper()}]: veri yok")
    biten = [e for e in log if isinstance(e["pnl"], float)]
    if biten and (yeni or kapanan):
        satir = []
        for kural in KURALLAR:
            b = [e for e in biten if e["kural"] == kural and e.get("derin", True)]
            g = sum(1 for e in biten if e["kural"] == kural and not e.get("derin", True))
            if b or g:
                oz = (f"{kural.upper()}: {len(b)} işlem, {sum(e['pnl'] for e in b)*100:+.1f}%, "
                      f"isabet %{sum(e['pnl']>0 for e in b)/len(b)*100:.0f}") if b else f"{kural.upper()}: 0 işlem"
                satir.append(oz + (f"  (+{g} genç, sicil-dışı)" if g else ""))
        tg("📒 F1 sicilleri\n" + "\n".join(satir))
    say = {k: sum(1 for e in biten if e["kural"] == k and e.get("derin", True)) for k in KURALLAR}
    print(f"Yeni sinyal grubu: {len(yeni)} | Kapanan: {len(kapanan)} | Sicil: donuk {say['donuk']} / genis {say['genis']}")
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and not (yeni or kapanan):
        tg(f"🟢 Gözcü canlı · evren {len(uni)} coin · sicil: donuk {say['donuk']} / genis {say['genis']} işlem")

if __name__ == "__main__":
    ana()
