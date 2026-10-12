#!/usr/bin/env python3
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
    return (
        f"{jmeno(p)} ({tym}): {g} G, {a} A, {pm:+d}, "
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
        d = datetime.strptime(datum, "%Y-%m-%d")
        nadpis = f"Čeští hráči v NHL, {d.day}. {d.month}. {d.year}"
        text = nadpis + "\n\n" + "\n\n".join(bloky) + "\n"
        if jen_vypsat:
            print(text)
        else:
            posli_email(nadpis, text)
            print("E-mail odeslan.")

    if ukladat:
        zapis_stav(datum)


if __name__ == "__main__":
    main()
