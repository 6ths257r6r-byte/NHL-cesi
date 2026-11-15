#!/usr/bin/env python3
import html
import json
import os
import re
import smtplib
import sys
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

API = "https://api-web.nhle.com/v1"
STAV = "odeslano.txt"


def get(path):
    req = urllib.request.Request(API + path, headers={"User-Agent": "nhl-cesi/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


CACHE = "cesi_cache.json"
_zeme = {}
_stav = {"zmena": False}
_rosters = {}


def nacti_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            _zeme.update(json.load(f))
    except Exception:
        pass


def uloz_cache():
    if _stav["zmena"]:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(_zeme, f, sort_keys=True)


def zapamatuj(pid, zeme):
    if _zeme.get(str(pid)) != zeme:
        _zeme[str(pid)] = zeme
        _stav["zmena"] = True


def soupiska(tym):
    """Aktualni soupiska tymu {id: zeme}; zaroven si pamatuje zeme puvodu hracu."""
    if tym not in _rosters:
        ids = {}
        try:
            data = get(f"/roster/{tym}/current")
            for skupina in ("forwards", "defensemen", "goalies"):
                for p in data.get(skupina, []):
                    zeme = p.get("birthCountry")
                    ids[p["id"]] = zeme
                    if zeme:
                        zapamatuj(p["id"], zeme)
        except Exception as e:
            print(f"Soupisku {tym} se nepodarilo nacist: {e}", file=sys.stderr)
        _rosters[tym] = ids
    return _rosters[tym]


def je_cech(pid):
    """Cech podle zeme puvodu. Hrace mimo soupisky (poslane na farmu) dohleda jednou a zapamatuje."""
    if pid is None:
        return False
    k = str(pid)
    if k not in _zeme:
        try:
            zeme = get(f"/player/{pid}/landing").get("birthCountry") or "?"
            zapamatuj(pid, zeme)
        except Exception as e:
            print(f"Hrace {pid} se nepodarilo zjistit: {e}", file=sys.stderr)
            return False
    return _zeme[k] == "CZE"


VIDEO = re.compile(r"\[\[v:(https?://[^\]\s]+)\]\]")


def najdi_odkaz(gol):
    for k, v in gol.items():
        kl = k.lower()
        if "clip" in kl and "url" in kl and not k.endswith("Fr") and isinstance(v, str) and v.startswith("http"):
            return v
    return None


def nacti_videa(game_id):
    """{playerId: [odkazy na videa golu]} podle dat zapasu (jen kdyz je NHL uvadi)."""
    videa, celkem = {}, 0
    try:
        data = get(f"/gamecenter/{game_id}/landing")
        for obdobi in data.get("summary", {}).get("scoring", []):
            for gol in obdobi.get("goals", []):
                celkem += 1
                odk = najdi_odkaz(gol)
                if odk:
                    videa.setdefault(gol.get("playerId"), []).append(odk)
    except Exception as e:
        print(f"Videa k zapasu {game_id} se nepodarilo nacist: {e}", file=sys.stderr)
    print(f"Zapas {game_id}: odkazy na videa {sum(len(v) for v in videa.values())} z {celkem} golu")
    return videa


def jmeno(p):
    n = p.get("name", {})
    return n.get("default", "?") if isinstance(n, dict) else str(n)


def radek_hrace(p, tym, videa=None):
    toi = p.get("toi") or "00:00"
    if toi in ("00:00", "0:00"):
        return None
    g, a = p.get("goals", 0), p.get("assists", 0)
    pm = p.get("plusMinus", 0)
    body = "🚨" * g
    if g and a:
        body += " "
    body += "🍎" * a
    if not body:
        body = "bez bodu"
    body += "".join(f" [[v:{u}]]" for u in (videa or []))
    return (
        f"{jmeno(p)} ({tym}): {body}, {pm:+d}, "
        f"{toi} na ledě, {p.get('sog', 0)} střel na bránu"
    )


def radek_brankare(p, tym):
    toi = p.get("toi") or "00:00"
    if toi in ("00:00", "0:00"):
        return None
    strely = gol = zasahy = None
    sa = p.get("saveShotsAgainst")
    if isinstance(sa, str) and "/" in sa:
        zasahy, strely = (int(x) for x in sa.split("/"))
    else:
        strely, gol = p.get("shotsAgainst"), p.get("goalsAgainst")
        if strely is not None and gol is not None:
            zasahy = strely - gol
    if zasahy is None or not strely:
        return f"{jmeno(p)} ({tym}): brankář, {toi} na ledě"
    proc = f"{zasahy / strely * 100:.1f}".replace(".", ",")
    return f"{jmeno(p)} ({tym}): {zasahy} zásahů z {strely} střel, úspěšnost {proc} %, {toi} na ledě"


def sestav_prehled(datum):
    hry = [g for g in get(f"/score/{datum}").get("games", [])
           if g.get("gameScheduleState", "OK") == "OK"]
    if not hry:
        return "nic", []
    if not all(g.get("gameState") in ("OFF", "FINAL") for g in hry):
        return "hraje", []
    bloky = []
    for g in hry:
        box = get(f"/gamecenter/{g['id']}/boxscore")
        away, home = box["awayTeam"], box["homeTeam"]
        radky = []
        videa = None
        for strana, tym in (("awayTeam", away["abbrev"]), ("homeTeam", home["abbrev"])):
            soupiska(tym)
            st = box.get("playerByGameStats", {}).get(strana, {})
            for p in st.get("forwards", []) + st.get("defense", []):
                if je_cech(p.get("playerId")):
                    if p.get("goals", 0) and videa is None:
                        videa = nacti_videa(g["id"])
                    r = radek_hrace(p, tym, (videa or {}).get(p.get("playerId")))
                    if r:
                        radky.append(r)
            for p in st.get("goalies", []):
                if je_cech(p.get("playerId")):
                    r = radek_brankare(p, tym)
                    if r:
                        radky.append(r)
        if radky:
            hlavicka = f"{away['abbrev']} {away.get('score', '?')}:{home.get('score', '?')} {home['abbrev']}"
            bloky.append(hlavicka + "\n" + "\n".join(radky))
    return "hotovo", bloky


# ---------- webova stranka ----------

MANIFEST = {
    "name": "Češi v NHL", "short_name": "NHL Češi", "lang": "cs",
    "start_url": "./", "scope": "./", "display": "standalone",
    "background_color": "#f5f6f8", "theme_color": "#f5f6f8",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

SERVICE_WORKER = """const C='nhl-cesi-v1';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('fetch',e=>{
  if(e.request.method!=='GET')return;
  e.respondWith(fetch(e.request).then(r=>{const c=r.clone();caches.open(C).then(x=>x.put(e.request,c));return r;}).catch(()=>caches.match(e.request)));
});
"""

STRANKA = """<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="NHL Češi">
<meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#f5f6f8">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" href="apple-touch-icon.png">
<title>Češi v NHL</title>
<style>
:root{--bg:#f5f6f8;--fg:#1a1a1a;--m:#666;--nova:#fff;--stara:#dcdfe4;--b:#d9dce1}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--m:#9aa3ad;--nova:#2a3039;--stara:#181b20;--b:#333}}
body{margin:0;padding:16px;padding-top:max(16px,env(safe-area-inset-top));padding-bottom:max(16px,env(safe-area-inset-bottom));background:var(--bg);color:var(--fg);font:15px/1.4 -apple-system,system-ui,sans-serif}
h1{font-size:21px;margin:0 0 4px}h2{font-size:17px;margin:22px 0 8px}h3{font-size:15px;margin:0 0 6px}
section{border-radius:10px;padding:10px 12px;margin:0 0 10px}
.nova section{background:var(--nova);border:1px solid var(--b);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.stara section{background:var(--stara);border:1px solid transparent}
ul{margin:0;padding-left:18px}li{margin:4px 0}
.m{color:var(--m);font-size:13px}
.t{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:5px 6px;border-bottom:1px solid var(--b);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
a.v{color:inherit;text-decoration:none;padding:0 4px}
</style></head><body>
<h1>🇨🇿 Češi v NHL</h1>
<p class="m">Aktualizováno @@CAS@@</p>
@@TELO@@
<script>if("serviceWorker"in navigator)navigator.serviceWorker.register("sw.js");</script>
</body></html>
"""


def cz_datum(datum):
    d = datetime.strptime(datum, "%Y-%m-%d")
    return f"{d.day}. {d.month}. {d.year}"


def den_html(datum, stav, bloky, nova=False):
    out = f'<div class="den {"nova" if nova else "stara"}">'
    out += f"<h2>{html.escape(cz_datum(datum))}</h2>"
    if stav == "nic":
        out += '<p class="m">Nehrálo se.</p>'
    elif stav == "hraje":
        out += '<p class="m">Zápasy ještě nejsou dohrané.</p>'
    elif not bloky:
        out += '<p class="m">Žádný Čech nenastoupil.</p>'
    for b in bloky:
        radky = b.split("\n")
        out += "<section><h3>" + html.escape(radky[0]) + "</h3><ul>"
        out += "".join(
            "<li>" + VIDEO.sub(r'<a class="v" href="\1" target="_blank" rel="noopener">▶</a>', html.escape(r)) + "</li>"
            for r in radky[1:])
        out += "</ul></section>"
    return out + "</div>"


def cele_jmeno(p):
    casti = []
    for k in ("firstName", "lastName"):
        v = p.get(k, "")
        casti.append(v.get("default", "") if isinstance(v, dict) else str(v))
    return " ".join(c for c in casti if c) or "?"


def poradi(vsechny, hodnota):
    return 1 + sum(1 for x in vsechny if x > hodnota)


def sluc_brankare(zaznamy):
    hlavni = max(zaznamy, key=lambda g: g.get("gamesPlayed", 0))
    sv = hlavni.get("savePercentage")
    if len(zaznamy) > 1:
        sa = sum(g.get("shotsAgainst") or 0 for g in zaznamy)
        ga = sum(g.get("goalsAgainst") or 0 for g in zaznamy)
        if sa:
            sv = 1 - ga / sa
    return {"p": hlavni, "z": sum(g.get("gamesPlayed", 0) for g in zaznamy),
            "sv": sv, "gaa": hlavni.get("goalsAgainstAverage")}


def sezonni_html():
    tymy = [t["teamAbbrev"]["default"] for t in get("/standings/now")["standings"]]
    hraci, brankari_zaznamy, soucasny, kde = {}, {}, {}, {}
    for tym in tymy:
        for pid in soupiska(tym):
            soucasny[pid] = tym
        try:
            st = get(f"/club-stats/{tym}/now")
        except Exception as e:
            print(f"Statistiky {tym} se nepodarilo nacist: {e}", file=sys.stderr)
            continue
        for p in st.get("skaters", []):
            pid = p.get("playerId")
            if pid is None:
                continue
            kde.setdefault(pid, []).append(tym)
            h = hraci.setdefault(pid, {"p": p, "z": 0, "g": 0, "a": 0, "b": 0, "pm": 0})
            h["z"] += p.get("gamesPlayed", 0)
            h["g"] += p.get("goals", 0)
            h["a"] += p.get("assists", 0)
            h["b"] += p.get("points", 0)
            h["pm"] += p.get("plusMinus", 0)
        for p in st.get("goalies", []):
            pid = p.get("playerId")
            if pid is not None:
                kde.setdefault(pid, []).append(tym)
                brankari_zaznamy.setdefault(pid, []).append(p)
    cesi = {pid for pid in list(hraci) + list(brankari_zaznamy) if je_cech(pid)}

    def tym_hrace(pid):
        return soucasny.get(pid) or kde[pid][-1]

    brankari_vsichni = {pid: sluc_brankare(z) for pid, z in brankari_zaznamy.items()}
    body_vsech = [h["b"] for h in hraci.values()]
    limit = max(1, round(max((h["z"] for h in hraci.values()), default=0) * 0.25))
    sv_vsech = [g["sv"] for g in brankari_vsichni.values() if g["z"] >= limit and g["sv"] is not None]
    bruslari = [(tym_hrace(pid), h) for pid, h in hraci.items() if pid in cesi]
    brankari = [(tym_hrace(pid), g) for pid, g in brankari_vsichni.items() if pid in cesi]
    bruslari.sort(key=lambda x: (-x[1]["b"], -x[1]["g"]))
    brankari.sort(key=lambda x: -x[1]["z"])
    e = html.escape
    out = '<h2>Sezóna: hráči</h2><div class="t"><table>'
    out += "<tr><th>Hráč</th><th>Tým</th><th>Z</th><th>G</th><th>A</th><th>B</th><th>+/-</th></tr>"
    for tym, h in bruslari:
        r = poradi(body_vsech, h["b"])
        out += (f"<tr><td>{e(cele_jmeno(h['p']))} <span class=\"m\">({r}.)</span></td><td>{e(tym)}</td>"
                f"<td>{h['z']}</td><td>{h['g']}</td><td>{h['a']}</td>"
                f"<td><b>{h['b']}</b></td><td>{h['pm']:+d}</td></tr>")
    out += "</table></div>"
    out += '<h2>Sezóna: brankáři</h2><div class="t"><table>'
    out += "<tr><th>Brankář</th><th>Tým</th><th>Z</th><th>Úspěšnost</th><th>Gól/zápas</th></tr>"
    for tym, g in brankari:
        jm = e(cele_jmeno(g["p"]))
        if g["sv"] is not None and g["z"] >= limit:
            jm += f' <span class="m">({poradi(sv_vsech, g["sv"])}.)</span>'
        sv = g["sv"]
        sv_text = "-" if sv is None else f"{sv * 100 if sv <= 1 else sv:.1f} %".replace(".", ",")
        gaa = "-" if g["gaa"] is None else f"{g['gaa']:.2f}".replace(".", ",")
        out += (f"<tr><td>{jm}</td><td>{e(tym)}</td><td>{g['z']}</td>"
                f"<td>{sv_text}</td><td>{gaa}</td></tr>")
    out += "</table></div>"
    out += ('<p class="m">V závorce za jménem je pořadí v celé NHL: hráči podle bodů (za všechny týmy dohromady), '
            f'brankáři podle úspěšnosti, a to jen od {limit} odehraných zápasů. Stejný výsledek znamená stejné pořadí.</p>')
    return out


def zapis_web(datum, stav, bloky):
    pred = (datetime.strptime(datum, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        st2, bl2 = sestav_prehled(pred)
    except Exception as e:
        print(f"Predchozi den se nepodarilo nacist: {e}", file=sys.stderr)
        st2, bl2 = "nic", []
    try:
        sezona = sezonni_html()
    except Exception as e:
        print(f"Sezonni tabulku se nepodarilo nacist: {e}", file=sys.stderr)
        sezona = '<p class="m">Sezónní tabulku se nepodařilo načíst.</p>'
    telo = den_html(datum, stav, bloky, True) + den_html(pred, st2, bl2) + sezona
    cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%-d. %-m. %Y %H:%M")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(STRANKA.replace("@@CAS@@", cas).replace("@@TELO@@", telo))
    with open("docs/manifest.webmanifest", "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False, indent=1)
    with open("docs/sw.js", "w", encoding="utf-8") as f:
        f.write(SERVICE_WORKER)
    print("Web ulozen: docs/index.html")


# ---------- stav a e-mail ----------

def uz_zpracovano(datum):
    try:
        with open(STAV) as f:
            return f.read().strip() == datum
    except FileNotFoundError:
        return False


def zapis_stav(datum):
    with open(STAV, "w") as f:
        f.write(datum + "\n")


def posli_email(predmet, text):
    user = os.environ["SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"] = predmet
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO", user)
    msg.set_content(text)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port) as s:
        s.login(user, os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


def hlavni():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    jen_vypsat = "--print" in sys.argv
    rucne = bool(args)
    ukladat = not rucne and not jen_vypsat

    if rucne:
        datum = args[0]
    else:
        ted = datetime.now(ZoneInfo("America/New_York"))
        datum = (ted - timedelta(hours=6)).strftime("%Y-%m-%d")
        if uz_zpracovano(datum):
            print(f"{datum}: uz zpracovano, nic se nedela.")
            return

    stav, bloky = sestav_prehled(datum)

    if stav == "hraje":
        print(f"{datum}: nektere zapasy se jeste hraji, zkusi se to pozdeji.")
        return

    if stav == "nic":
        print(f"{datum}: nehralo se.")
    elif not bloky:
        print(f"{datum}: zadny cesky hrac nenastoupil. E-mail se neposila.")
    else:
        nadpis = f"Čeští hráči v NHL, {cz_datum(datum)}"
        text = nadpis + "\n\n" + "\n\n".join(bloky) + "\n"
        text = VIDEO.sub(lambda m: "▶ " + m.group(1), text)
        if jen_vypsat:
            print(text)
        else:
            posli_email(nadpis, text)
            print("E-mail odeslan.")

    if not jen_vypsat:
        try:
            zapis_web(datum, stav, bloky)
        except Exception as e:
            print(f"Web se nepodarilo vytvorit: {e}", file=sys.stderr)

    if ukladat:
        zapis_stav(datum)


def main():
    nacti_cache()
    try:
        hlavni()
    finally:
        uloz_cache()


if __name__ == "__main__":
    main()
