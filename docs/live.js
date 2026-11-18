(function () {
  var API = "https://api-web.nhle.com/v1", D = null, SET = {}, cache = {}, zacatek = Date.now();
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  var PROXY = "https://nhl-proxy.nhjz99szk4.workers.dev";
  function ok(r) { if (!r.ok) throw new Error("HTTP " + r.status + " " + r.url); return r.json(); }
  function j(u) {
    return fetch(u).then(ok).catch(function (e) {
      if (!PROXY || u.indexOf("https://api-web.nhle.com") !== 0) throw e;
      return fetch(u.replace("https://api-web.nhle.com", PROXY)).then(ok);
    });
  }
  function hraciDen(ted) {
    var o = {};
    new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(ted || new Date()).forEach(function (x) { o[x.type] = x.value; });
    return new Date(Date.UTC(+o.year, +o.month - 1, +o.day, +o.hour - 6, +o.minute)).toISOString().slice(0, 10);
  }
  function predtim(s) { var d = new Date(s + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() - 1); return d.toISOString().slice(0, 10); }
  function cz(s) { var p = s.split("-"); return +p[2] + ". " + +p[1] + ". " + p[0]; }
  function jm(p) { var n = p.name; return n && n.default ? n.default : "?"; }
  function znamy(g) { return (D.tymy[g.awayTeam.abbrev] || []).length || (D.tymy[g.homeTeam.abbrev] || []).length; }
  function konec(g) { return g.gameState === "FINAL" || g.gameState === "OFF"; }
  function bezi(g) { return g.gameState === "LIVE" || g.gameState === "CRIT"; }
  function hrac(p, tym, vid) {
    if (!p.toi || p.toi === "00:00" || p.toi === "0:00") return null;
    var g = p.goals || 0, a = p.assists || 0, b = "🚨".repeat(g) + (g && a ? " " : "") + "🍎".repeat(a), pm = p.plusMinus || 0;
    return { t: jm(p) + " (" + tym + "): " + (b || "bez bodu"), v: vid || [], r: ", " + (pm > 0 ? "+" : "") + pm + ", " + p.toi + " na ledě, " + (p.sog || 0) + " střel na bránu" };
  }
  function brankar(p, tym) {
    if (!p.toi || p.toi === "00:00" || p.toi === "0:00") return null;
    var z, s, m = /^(\d+)\/(\d+)$/.exec(p.saveShotsAgainst || "");
    if (m) { z = +m[1]; s = +m[2]; } else if (p.shotsAgainst != null && p.goalsAgainst != null) { s = p.shotsAgainst; z = s - p.goalsAgainst; }
    if (!s) return { t: jm(p) + " (" + tym + "): brankář, " + p.toi + " na ledě", v: [], r: "" };
    return { t: jm(p) + " (" + tym + "): " + z + " zásahů z " + s + " střel, úspěšnost " + (z / s * 100).toFixed(1).replace(".", ",") + " %, " + p.toi + " na ledě", v: [], r: "" };
  }
  function videa(id) {
    return j(API + "/gamecenter/" + id + "/landing").then(function (d) {
      var out = {};
      ((d.summary || {}).scoring || []).forEach(function (o) { (o.goals || []).forEach(function (g) {
        for (var k in g) { var kl = k.toLowerCase(); if (kl.indexOf("clip") >= 0 && kl.indexOf("url") >= 0 && !/Fr$/.test(k) && /^http/.test(g[k])) { (out[g.playerId] = out[g.playerId] || []).push(g[k]); break; } }
      }); });
      return out;
    }).catch(function () { return {}; });
  }
  function karta(g) {
    var a = g.awayTeam.abbrev, h = g.homeTeam.abbrev;
    if (cache[g.id]) return Promise.resolve(cache[g.id]);
    if (!konec(g) && !bezi(g)) {
      var cas = new Date(g.startTimeUTC).toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit" });
      return Promise.resolve({ h: a + " – " + h + " · začne " + cas, radky: [], cekani: (D.tymy[a] || []).concat(D.tymy[h] || []) });
    }
    return j(API + "/gamecenter/" + g.id + "/boxscore").then(function (b) {
      var vsichni = [], ps = b.playerByGameStats || {};
      [["awayTeam", a], ["homeTeam", h]].forEach(function (x) {
        var st = ps[x[0]] || {};
        (st.forwards || []).concat(st.defense || []).forEach(function (p) { if (SET[p.playerId]) vsichni.push({ p: p, t: x[1], b: 0 }); });
        (st.goalies || []).forEach(function (p) { if (SET[p.playerId]) vsichni.push({ p: p, t: x[1], b: 1 }); });
      });
      var chce = vsichni.some(function (x) { return !x.b && x.p.goals; });
      return (chce ? videa(g.id) : Promise.resolve({})).then(function (v) {
        var radky = vsichni.map(function (x) { return x.b ? brankar(x.p, x.t) : hrac(x.p, x.t, v[x.p.playerId]); }).filter(Boolean);
        var sk = (g.awayTeam.score != null ? g.awayTeam.score : (b.awayTeam || {}).score) + ":" + (g.homeTeam.score != null ? g.homeTeam.score : (b.homeTeam || {}).score);
        var k = { h: a + " " + sk + " " + h + (bezi(g) ? " · probíhá" : ""), radky: radky };
        if (konec(g)) cache[g.id] = k;
        return k;
      });
    });
  }
  function den(datum) {
    return j(API + "/score/" + datum).then(function (sc) {
      var rel = (sc.games || []).filter(function (g) { return (g.gameScheduleState || "OK") === "OK" && znamy(g); });
      return Promise.all(rel.map(karta)).then(function (karty) {
        return { datum: datum, karty: karty, celkem: rel.length, hotovo: rel.filter(konec).length, bezi: rel.filter(bezi).length };
      });
    });
  }
  function radekHtml(r) { return esc(r.t) + r.v.map(function (u) { return ' <a class="v" href="' + esc(u) + '" target="_blank" rel="noopener">▶</a>'; }).join("") + esc(r.r); }
  function radekText(r) { return r.t + r.v.map(function (u) { return " ▶ " + u; }).join("") + r.r; }
  function textDne(d) {
    var t = "Čeští hráči v NHL, " + cz(d.datum) + "\n\n";
    var s = d.karty.filter(function (k) { return k.radky.length; }).map(function (k) { return k.h + "\n" + k.radky.map(radekText).join("\n"); });
    return t + (s.length ? s.join("\n\n") : "Zatím žádný Čech nenastoupil.");
  }
  function htmlDne(d, nova) {
    var hlav = d.celkem ? "Zápasy s Čechy: dohráno " + d.hotovo + " z " + d.celkem + (d.bezi ? " · probíhá " + d.bezi : "") + " · zbývá " + (d.celkem - d.hotovo) : "Nehraje žádný Čech.";
    var o = '<div class="den ' + (nova ? "nova" : "stara") + '"><div class="hl"><h2>' + cz(d.datum) + '</h2><button class="kop" type="button" data-t="' + esc(textDne(d)).replace(/\n/g, "&#10;") + '">📋 Kopírovat</button></div><p class="m">' + esc(hlav) + "</p>";
    d.karty.forEach(function (k) {
      o += "<section><h3>" + esc(k.h) + "</h3><ul>";
      if (k.cekani) o += "<li>Na soupisce: " + esc(k.cekani.join(", ")) + "</li>";
      o += k.radky.map(function (r) { return "<li>" + radekHtml(r) + "</li>"; }).join("");
      o += "</ul></section>";
    });
    return o + "</div>";
  }
  function nacti() {
    if (!D) return;
    var d0 = hraciDen();
    Promise.all([den(d0), den(predtim(d0))]).then(function (r) {
      $("zive").innerHTML = htmlDne(r[0], true) + htmlDne(r[1], false);
      $("staticke").style.display = "none";
      $("stavzive").textContent = "· živě " + new Date().toLocaleTimeString("cs-CZ");
    }).catch(function (e) { $("stavzive").textContent = "· živá data nedostupná (NHL: " + e + ")"; });
  }
  fetch("data.json").then(function (r) { return r.json(); }).then(function (d) {
    D = d; d.cesi.forEach(function (id) { SET[id] = 1; });
    nacti();
    setInterval(function () { if (!document.hidden) nacti(); }, 60000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) nacti(); });
  }).catch(function (e) { $("stavzive").textContent = "· živá data nedostupná (data.json: " + e + ")"; });
  window.__live = { hraciDen: hraciDen, predtim: predtim };
})();
