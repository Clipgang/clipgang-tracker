"""Fuehrt einen Schnitt-Auftrag aus. Claude schreibt den Auftrag als eine Zeile JSON,
Bre fuegt ihn unter Actions -> "Auftrag ausfuehren" ein.

Formate und Felder (alles ausser format/link(s) ist optional):

  {"format":"einzel","link":"<clip>","hook":"...","streamer":"","layout":"standard|facecam",
   "facecam":"","untertitel":true,"start":"auto","dauer":30,"teile":1}
  {"format":"versus","links":["<clip A>","<clip B>"],"hook":"...","untertitel":true,
   "start":["auto","auto"],"dauer":20}
  {"format":"zusammenschnitt","modus":"countdown|namen","links":["<#3>","<#2>","<#1>"],
   "hook":"...","untertitel":false,"start":"auto","dauer":15}
  {"format":"rennen","hook":"WER IST ZUERST DURCH? 😳","anzahl":6}

Aufruf: python auftrag.py '<json>'   (oder Umgebungsvariable AUFTRAG)
Ergebnis: Videos in fertig/, Vorschaubilder in vorschau_tmp/.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import cut as C
import formate as F

ROOT = Path(__file__).parent
RAW, OUT, PREV = ROOT / "raw", ROOT / "fertig", ROOT / "vorschau_tmp"
LINK_RE = re.compile(r"^https://(www\.|clips\.|m\.)?twitch\.tv/[\w\-/?=&.]+$")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "lokal")


def slug(text, n=40):
    s = re.sub(r"[^\w\-]+", "_", text, flags=re.ASCII).strip("_")
    return (s or "clip")[:n]


def streamer_from_link(link):
    m = re.search(r"twitch\.tv/([^/]+)/clip/", link)
    return m.group(1) if m else ""


def download(link, i):
    if not LINK_RE.match(link.strip()):
        sys.exit(f"Kein Twitch-Link: {link}")
    RAW.mkdir(exist_ok=True)
    dst = RAW / f"clip{i}.mp4"
    subprocess.run(["yt-dlp", "--no-playlist", "-q", "-f", "best[ext=mp4]/best",
                    "-o", str(dst), link.strip()], check=True)
    return dst


def as_list(v, n, default):
    if isinstance(v, list):
        return (v + [default] * n)[:n]
    return [v if v not in (None, "") else default] * n


def start_value(v):
    if v in (None, "", 0, "0"):
        return 0.0
    return "auto" if str(v).strip().lower() == "auto" else float(v)


def finish(path):
    C.preview(path, PREV / f"{RUN_ID}_{path.stem}.jpg")
    print(f"Fertig: {path.name} ({C.probe_duration(path):.1f} s)")


def einzel(a):
    link = a["link"]
    streamer = a.get("streamer") or streamer_from_link(link)
    src = download(link, 0)
    C.preview(src, PREV / f"{RUN_ID}_roh.jpg", grid=True)
    layout = a.get("layout", "standard")
    if layout == "facecam" and not C.has_facecam(streamer, a.get("facecam", "")):
        print(f"Keine Facecam-Position fuer {streamer} gespeichert, nehme Standard-Layout. "
              "Raster-Vorschau ansehen und Position in config.json eintragen.")
        layout = "standard"
    start = start_value(a.get("start"))
    if start == "auto":
        start = C.find_punch(src)
        print(f"Auto-Start: {start:.1f} s")
    dauer = float(a["dauer"]) if a.get("dauer") else None
    teile = int(a.get("teile", 1) or 1)
    total = C.probe_duration(src) - start
    if dauer:
        total = min(total, dauer)
    words = None
    base = f"{slug(streamer)}_{slug(a.get('hook', '') or 'clip', 30)}"
    if teile > 1:
        part = total / teile
        for t in range(teile):
            dst = OUT / f"{base}_teil{t + 1}.mp4"
            C.cut(src, dst, a.get("hook", ""), streamer, layout, a.get("facecam", ""),
                  untertitel=bool(a.get("untertitel")), start=start + t * part, dauer=part,
                  badge=f"TEIL {t + 1}", words=words)
            finish(dst)
    else:
        dst = OUT / f"{base}.mp4"
        C.cut(src, dst, a.get("hook", ""), streamer, layout, a.get("facecam", ""),
              untertitel=bool(a.get("untertitel")), start=start, dauer=dauer)
        finish(dst)


def versus(a):
    links = a["links"][:2]
    if len(links) != 2:
        sys.exit("Versus braucht genau 2 Links.")
    names = as_list(a.get("streamer"), 2, "")
    names = [n or streamer_from_link(l) for n, l in zip(names, links)]
    starts = [start_value(s) for s in as_list(a.get("start"), 2, 0)]
    srcs = [download(l, i) for i, l in enumerate(links)]
    dst = OUT / f"versus_{slug(names[0])}_vs_{slug(names[1])}.mp4"
    F.versus(srcs[0], srcs[1], dst, a.get("hook", ""), names[0], names[1],
             untertitel=bool(a.get("untertitel")), start_a=starts[0], start_b=starts[1],
             dauer=float(a.get("dauer", 20) or 20))
    finish(dst)


def zusammenschnitt(a):
    links = a["links"]
    if len(links) < 2:
        sys.exit("Ein Zusammenschnitt braucht mindestens 2 Links.")
    names = as_list(a.get("streamer"), len(links), "")
    starts = as_list(a.get("start"), len(links), 0)
    badges = as_list(a.get("badges"), len(links), "")
    items = []
    for i, l in enumerate(links):
        items.append({"src": download(l, i), "streamer": names[i] or streamer_from_link(l),
                      "start": start_value(starts[i]), "badge": badges[i]})
    modus = a.get("modus", "countdown")
    dst = OUT / f"{'countdown' if modus == 'countdown' else 'zusammenschnitt'}_{slug(a.get('hook', '') or 'clips', 30)}.mp4"
    F.zusammenschnitt(items, dst, a.get("hook", ""), modus=modus,
                      untertitel=bool(a.get("untertitel")), dauer=float(a.get("dauer", 15) or 15))
    finish(dst)


def rennen(a):
    dst = OUT / "story_rennen.mp4"
    rows = F.rennen(dst, ROOT / "data", hook=a.get("hook") or "WER IST ZUERST DURCH? 😳",
                    anzahl=int(a.get("anzahl", 6) or 6))
    for r in rows:
        prev = f" (gestern {r['before']} %)" if r["before"] is not None else ""
        print(f"  {r['name']}: {r['now']} %{prev}")
    finish(dst)


def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AUFTRAG", "")
    raw = raw.strip().strip("`")
    try:
        a = json.loads(raw)
    except ValueError as e:
        sys.exit(f"Auftrag ist kein gueltiges JSON: {e}\nEingabe: {raw[:200]}")
    OUT.mkdir(exist_ok=True)
    PREV.mkdir(exist_ok=True)
    fmt = a.get("format", "einzel")
    handlers = {"einzel": einzel, "versus": versus, "zusammenschnitt": zusammenschnitt,
                "countdown": zusammenschnitt, "rennen": rennen}
    if fmt not in handlers:
        sys.exit(f"Unbekanntes Format '{fmt}'. Moeglich: {', '.join(handlers)}")
    if fmt == "countdown":
        a.setdefault("modus", "countdown")
    handlers[fmt](a)


if __name__ == "__main__":
    main()
