#!/usr/bin/env python3
import html
import json
import os
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


_rosters = {}


def cesi_v_tymu(tym):
    if tym not in _rosters:
        ids = set()
        try:
            data = get(f"/roster/{tym}/current")
            for skupina in ("forwards", "defensemen", "goalies"):
                for p in data.get(skupina, []):
                    if p.get("birthCountry") == "CZE":
                        ids.add(p["id"])
        except Exception as e:
            print(f"Soupisku {tym} se nepodarilo nacist: {e}", file=sys.stderr)
        _rosters[tym] = ids
    return _rosters[tym]


def jmeno(p):
    n = p.get("name", {})
    return n.get("default", "?") if isinstance(n, dict) else str(n)


def radek_hrace(p, tym):
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
        for strana, tym in (("awayTeam", away["abbrev"]), ("homeTeam", home["abbrev"])):
            cesi = cesi_v_tymu(tym)
            st = box.get("playerByGameStats", {}).get(strana, {})
            for p in st.get("forwards", []) + st.get("defense", []):
                if p.get("playerId") in cesi:
                    r = radek_hrace(p, tym)
                    if r:
                        radky.append(r)
            for p in st.get("goalies", []):
                if p.get("playerId") in cesi:
                    r = radek_brankare(p, tym)
                    if r:
                        radky.append(r)
        if radky:
            hlavicka = f"{away['abbrev']} {away.get('score', '?')}:{home.get('score', '?')} {home['abbrev']}"
            bloky.append(hlavicka + "\n" + "\n".join(radky))
    return "hotovo", bloky


# ---------- webova stranka ----------

STRANKA = """<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="NHL Češi">
<title>Češi v NHL</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--m:#666;--c:#f3f4f6;--b:#d9dce1}
@media(prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--m:#9aa3ad;--c:#1c1f24;--b:#333}}
body{margin:0;padding:16px;padding-top:max(16px,env(safe-area-inset-top));padding-bottom:max(16px,env(safe-area-inset-bottom));background:var(--bg);color:var(--fg);font:15px/1.4 -apple-system,system-ui,sans-serif}
h1{font-size:21px;margin:0 0 4px}h2{font-size:17px;margin:22px 0 8px}h3{font-size:15px;margin:0 0 6px}
section{background:var(--c);border-radius:10px;padding:10px 12px;margin:0 0 10px}
ul{margin:0;padding-left:18px}li{margin:4px 0}
.m{color:var(--m);font-size:13px}
.t{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:5px 6px;border-bottom:1px solid var(--b);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
</style></head><body>
<h1>🇨🇿 Češi v NHL</h1>
<p class="m">Aktualizováno @@CAS@@</p>
@@TELO@@
</body></html>
"""


def cz_datum(datum):
    d = datetime.strptime(datum, "%Y-%m-%d")
    return f"{d.day}. {d.month}. {d.year}"


def den_html(datum, stav, bloky):
    out = f"<h2>{cz_datum(datum)}</h2>"
    if stav == "nic":
        out += '<p class="m">Nehrálo se.</p>'
    elif stav == "hraje":
        out += '<p class="m">Zápasy ještě nejsou dohrané.</p>'
    elif not bloky:
        out += '<p class="m">Žádný Čech nenastoupil.</p>'
    for b in bloky:
        radky = b.split("\n")
        out += "<section><h3>" + html.escape(radky[0]) + "</h3><ul>"
        out += "".join("<li>" + html.escape(r) + "</li>" for r in radky[1:])
        out += "</ul></section>"
    return out


def cele_jmeno(p):
    casti = []
    for k in ("firstName", "lastName"):
        v = p.get(k, "")
        casti.append(v.get("default", "") if isinstance(v, dict) else str(v))
    return " ".join(c for c in casti if c) or "?"


def sezonni_html():
    tymy = [t["teamAbbrev"]["default"] for t in get("/standings/now")["standings"]]
    bruslari, brankari = [], []
    for tym in tymy:
        cesi = cesi_v_tymu(tym)
        if not cesi:
            continue
        st = get(f"/club-stats/{tym}/now")
        bruslari += [(tym, p) for p in st.get("skaters", []) if p.get("playerId") in cesi]
        brankari += [(tym, p) for p in st.get("goalies", []) if p.get("playerId") in cesi]
    bruslari.sort(key=lambda x: (-x[1].get("points", 0), -x[1].get("goals", 0)))
    brankari.sort(key=lambda x: -x[1].get("gamesPlayed", 0))
    e = html.escape
    out = '<h2>Sezóna: hráči</h2><div class="t"><table>'
    out += "<tr><th>Hráč</th><th>Tým</th><th>Z</th><th>G</th><th>A</th><th>B</th><th>+/-</th></tr>"
    for tym, p in bruslari:
        out += (f"<tr><td>{e(cele_jmeno(p))}</td><td>{e(tym)}</td><td>{p.get('gamesPlayed', 0)}</td>"
                f"<td>{p.get('goals', 0)}</td><td>{p.get('assists', 0)}</td>"
                f"<td><b>{p.get('points', 0)}</b></td><td>{p.get('plusMinus', 0):+d}</td></tr>")
    out += "</table></div>"
    out += '<h2>Sezóna: brankáři</h2><div class="t"><table>'
    out += "<tr><th>Brankář</th><th>Tým</th><th>Z</th><th>Úspěšnost</th><th>Gól/zápas</th></tr>"
    for tym, p in brankari:
        sv = p.get("savePercentage")
        sv = "-" if sv is None else f"{sv * 100 if sv <= 1 else sv:.1f} %".replace(".", ",")
        gaa = p.get("goalsAgainstAverage")
        gaa = "-" if gaa is None else f"{gaa:.2f}".replace(".", ",")
        out += (f"<tr><td>{e(cele_jmeno(p))}</td><td>{e(tym)}</td><td>{p.get('gamesPlayed', 0)}</td>"
                f"<td>{sv}</td><td>{gaa}</td></tr>")
    out += "</table></div>"
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
    telo = den_html(datum, stav, bloky) + den_html(pred, st2, bl2) + sezona
    cas = datetime.now(ZoneInfo("Europe/Prague")).strftime("%-d. %-m. %Y %H:%M")
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(STRANKA.replace("@@CAS@@", cas).replace("@@TELO@@", telo))
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


def main():
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


if __name__ == "__main__":
    main()
