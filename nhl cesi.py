#!/usr/bin/env python3
import html
import json
import os
import re
import smtplib
import sys
import urllib.parse
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
_cesi_tymy = {}


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
                    if zeme == "CZE":
                        jm = [(p.get(k) or {}).get("default", "") for k in ("firstName", "lastName")]
                        _cesi_tymy.setdefault(tym, []).append(" ".join(x for x in jm if x))
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


_kratke = {}


def zkrat(url):
    """Zkrati odkaz pres is.gd (zaloha TinyURL). Kdyz to nejde, vrati puvodni odkaz."""
    if url in _kratke:
        return _kratke[url]
    kratky = url
    for sluzba in ("https://is.gd/create.php?format=simple&url=", "https://tinyurl.com/api-create.php?url="):
        try:
            req = urllib.request.Request(sluzba + urllib.parse.quote(url, safe=""),
                                         headers={"User-Agent": "nhl-cesi/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                odp = r.read().decode("utf-8").strip()
            if odp.startswith("http") and " " not in odp:
                kratky = odp
                break
        except Exception as e:
            print(f"Zkraceni odkazu selhalo ({sluzba.split('/')[2]}): {e}", file=sys.stderr)
    _kratke[url] = kratky
    return kratky


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

SERVICE_WORKER = """const C='nhl-cesi-v2';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('fetch',e=>{
  const r=e.request;
  if(r.method!=='GET'||new URL(r.url).origin!==location.origin)return;
  e.respondWith(fetch(r,{cache:'no-store'}).then(x=>{const c=x.clone();caches.open(C).then(y=>y.put(r,c));return x;}).catch(()=>caches.match(r).then(x=>x||Response.error())));
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
.hl{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:22px 0 8px}
.hl h2{margin:0}
.kop{font:inherit;font-size:13px;padding:5px 11px;border-radius:8px;border:1px solid var(--b);background:var(--nova);color:var(--fg);cursor:pointer;white-space:nowrap}
</style></head><body>
<h1>🇨🇿 Češi v NHL</h1>
<p class="m">Aktualizováno @@CAS@@ <span id="stavzive"></span> <button class="kop" type="button" id="obnov">🔄 Obnovit</button></p>
@@TELO@@
<script>if("serviceWorker"in navigator)navigator.serviceWorker.register("sw.js");
document.addEventListener("click",function(ev){
  var b=ev.target.closest(".kop");if(!b)return;
  var t=b.getAttribute("data-t"),o=b.textContent;
  function ok(){b.textContent="✓ Zkopírováno";setTimeout(function(){b.textContent=o;},1600);}
  function nouzove(){var a=document.createElement("textarea");a.value=t;a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);a.focus();a.select();try{document.execCommand("copy");ok();}catch(e){b.textContent="Nepodařilo se";}document.body.removeChild(a);}
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(ok,nouzove);}else{nouzove();}
});
var nacteno=Date.now();
function obnov(){location.reload();}
document.getElementById("obnov").addEventListener("click",obnov);
document.addEventListener("visibilitychange",function(){if(!document.hidden&&Date.now()-nacteno>300000)obnov();});
setInterval(function(){if(!document.hidden&&Date.now()-nacteno>600000)obnov();},60000);
</script>
<script src="live.js"></script>
</body></html>
"""


def cz_datum(datum):
    d = datetime.strptime(datum, "%Y-%m-%d")
    return f"{d.day}. {d.month}. {d.year}"


def tlacitko(text):
    if not text:
        return ""
    t = html.escape(text).replace("\n", "&#10;")
    return f'<button class="kop" type="button" data-t="{t}">📋 Kopírovat</button>'


def den_text(datum, stav, bloky):
    if stav == "hraje":
        return ""
    if stav == "nic":
        telo = "Nehrálo se."
    elif not bloky:
        telo = "Žádný Čech nenastoupil."
    else:
        telo = "\n\n".join(bloky)
    text = f"Čeští hráči v NHL, {cz_datum(datum)}\n\n" + telo
    return VIDEO.sub(lambda m: "▶ " + zkrat(m.group(1)), text)


def den_html(datum, stav, bloky, nova=False):
    out = f'<div class="den {"nova" if nova else "stara"}">'
    out += f'<div class="hl"><h2>{html.escape(cz_datum(datum))}</h2>{tlacitko(den_text(datum, stav, bloky))}</div>'
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
    radky_h, text_h = [], []
    for tym, h in bruslari:
        r = poradi(body_vsech, h["b"])
        jm = cele_jmeno(h["p"])
        radky_h.append(
            f"<tr><td>{e(jm)} <span class=\"m\">({r}.)</span></td><td>{e(tym)}</td>"
            f"<td>{h['z']}</td><td>{h['g']}</td><td>{h['a']}</td>"
            f"<td><b>{h['b']}</b></td><td>{h['pm']:+d}</td></tr>")
        text_h.append(f"{jm} ({tym}): {h['z']} z., {h['g']} G, {h['a']} A, {h['b']} B, {h['pm']:+d} (NHL: {r}.)")
    radky_b, text_b = [], []
    for tym, g in brankari:
        jm = cele_jmeno(g["p"])
        poradi_txt = ""
        if g["sv"] is not None and g["z"] >= limit:
            poradi_txt = f"({poradi(sv_vsech, g['sv'])}.)"
        sv = g["sv"]
        sv_text = "-" if sv is None else f"{sv * 100 if sv <= 1 else sv:.1f} %".replace(".", ",")
        gaa = "-" if g["gaa"] is None else f"{g['gaa']:.2f}".replace(".", ",")
        jm_html = e(jm) + (f' <span class="m">{poradi_txt}</span>' if poradi_txt else "")
        radky_b.append(
            f"<tr><td>{jm_html}</td><td>{e(tym)}</td><td>{g['z']}</td>"
            f"<td>{sv_text}</td><td>{gaa}</td></tr>")
        text_b.append(f"{jm} ({tym}): {g['z']} z., úspěšnost {sv_text}, {gaa} gólů/zápas"
                      + (f" (NHL: {poradi_txt[1:-1]})" if poradi_txt else ""))
    dnes = datetime.now(ZoneInfo("Europe/Prague"))
    text = (f"Čeští hráči v NHL, sezóna (stav k {dnes.day}. {dnes.month}. {dnes.year})\n\nHráči\n"
            + "\n".join(text_h) + "\n\nBrankáři\n" + "\n".join(text_b)
            + "\n\nV závorce je pořadí v celé NHL.")
    out = f'<div class="hl"><h2>Sezóna: hráči</h2>{tlacitko(text)}</div><div class="t"><table>'
    out += "<tr><th>Hráč</th><th>Tým</th><th>Z</th><th>G</th><th>A</th><th>B</th><th>+/-</th></tr>"
    out += "".join(radky_h) + "</table></div>"
    out += '<h2>Sezóna: brankáři</h2><div class="t"><table>'
    out += "<tr><th>Brankář</th><th>Tým</th><th>Z</th><th>Úspěšnost</th><th>Gól/zápas</th></tr>"
    out += "".join(radky_b) + "</table></div>"
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
    telo = '<div id="zive"></div><div id="staticke">' + den_html(datum, stav, bloky, True) + den_html(pred, st2, bl2) + "</div>" + sezona
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

def zapis_podpory():
    os.makedirs("docs", exist_ok=True)
    with open("docs/sw.js", "w", encoding="utf-8") as f:
        f.write(SERVICE_WORKER)
    with open("docs/manifest.webmanifest", "w", encoding="utf-8") as f:
        json.dump(MANIFEST, f, ensure_ascii=False, indent=1)


def zapis_data():
    """docs/data.json: seznam Cechu a Cesi na soupiskach tymu (pro zivou stranku). Obnovuje se po 6 hodinach."""
    try:
        with open("docs/data.json", encoding="utf-8") as f:
            if datetime.now().timestamp() - json.load(f).get("cas", 0) < 6 * 3600:
                return
    except Exception:
        pass
    for t in get("/standings/now")["standings"]:
        soupiska(t["teamAbbrev"]["default"])
    os.makedirs("docs", exist_ok=True)
    data = {"cas": int(datetime.now().timestamp()),
            "cesi": sorted(int(k) for k, v in _zeme.items() if v == "CZE"),
            "tymy": _cesi_tymy}
    with open("docs/data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print("Data pro zivou stranku ulozena.")


def obnov_stary_web():
    """Kdyz je stranka z puvodni verze (bez zive casti), jednou ji znovu vytvori."""
    try:
        with open("docs/index.html", encoding="utf-8") as f:
            stary = "live.js" not in f.read()
    except FileNotFoundError:
        stary = True
    d = posledni_zpracovany()
    if stary and d:
        stav, bloky = sestav_prehled(d)
        if stav != "hraje":
            web_bez_padu(d, stav, bloky)


def ted_et():
    return datetime.now(ZoneInfo("America/New_York"))


def posledni_zpracovany():
    try:
        with open(STAV) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def nezpracovane_dny(dnes):
    """Hraci dny od posledniho zpracovaneho (nejvic 4 dny zpet) az do dnesniho americkeho dne."""
    zacatek = dnes - timedelta(days=1)
    posledni = posledni_zpracovany()
    if posledni:
        try:
            po = datetime.strptime(posledni, "%Y-%m-%d").date() + timedelta(days=1)
            zacatek = max(po, dnes - timedelta(days=3))
        except ValueError:
            pass
    dny = []
    d = zacatek
    while d <= dnes:
        dny.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return dny


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


def zpracuj_den(datum, jen_vypsat):
    """Vrati (stav, bloky). Pri stavu "hraje" se nic neodesila."""
    stav, bloky = sestav_prehled(datum)
    if stav == "hraje":
        print(f"{datum}: nektere zapasy se jeste hraji, zkusi se to pozdeji.")
    elif stav == "nic":
        print(f"{datum}: nehralo se.")
    elif not bloky:
        print(f"{datum}: zadny cesky hrac nenastoupil. E-mail se neposila.")
    else:
        nadpis = f"Čeští hráči v NHL, {cz_datum(datum)}"
        text = nadpis + "\n\n" + "\n\n".join(bloky) + "\n"
        text = VIDEO.sub(lambda m: "▶ " + zkrat(m.group(1)), text)
        if jen_vypsat:
            print(text)
        else:
            posli_email(nadpis, text)
            print(f"{datum}: e-mail odeslan.")
    return stav, bloky


def web_bez_padu(datum, stav, bloky):
    try:
        zapis_web(datum, stav, bloky)
    except Exception as e:
        print(f"Web se nepodarilo vytvorit: {e}", file=sys.stderr)


def hlavni():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    jen_vypsat = "--print" in sys.argv

    if args:
        # rucne zadane datum: neukladame stav, jen odesleme a obnovime web
        stav, bloky = zpracuj_den(args[0], jen_vypsat)
        if stav != "hraje" and not jen_vypsat:
            web_bez_padu(args[0], stav, bloky)
        return

    if not jen_vypsat:
        try:
            zapis_podpory()
            zapis_data()
        except Exception as e:
            print(f"Data pro zivou stranku se nepodarilo ulozit: {e}", file=sys.stderr)
    dnes = (ted_et() - timedelta(hours=6)).date()
    dny = nezpracovane_dny(dnes)
    if not dny:
        print(f"{posledni_zpracovany()}: uz zpracovano, nic se nedela.")
        if not jen_vypsat:
            obnov_stary_web()
        return
    posledni = None
    for datum in dny:
        stav, bloky = zpracuj_den(datum, jen_vypsat)
        if stav == "hraje":
            break
        posledni = (datum, stav, bloky)
        if not jen_vypsat:
            zapis_stav(datum)
    if not jen_vypsat:
        if posledni:
            web_bez_padu(*posledni)
        else:
            obnov_stary_web()


def main():
    nacti_cache()
    try:
        hlavni()
    finally:
        uloz_cache()


if __name__ == "__main__":
    main()
