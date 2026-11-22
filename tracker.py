"""Clipgang Tracker: sammelt deutsche GTA-6-Streams und Top-Clips von Twitch.

Laeuft per GitHub Actions. Braucht die Umgebungsvariablen
TWITCH_CLIENT_ID und TWITCH_CLIENT_SECRET (als GitHub Secrets hinterlegt).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
HISTORY = DATA / "history"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

API = "https://api.twitch.tv/helix"
HISTORY_DAYS = 7


# ---------- Twitch API ----------

def get_token(client_id, client_secret):
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
    }).encode()
    req = urllib.request.Request("https://id.twitch.tv/oauth2/token", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        print(f"Client-ID: {len(client_id)} Zeichen, beginnt mit '{client_id[:4]}', endet mit '{client_id[-3:]}'")
        print(f"Client-Secret: {len(client_secret)} Zeichen")
        hint = ""
        if "invalid client secret" in detail:
            hint = "Das Client-Secret stimmt nicht. Neues Geheimnis auf dev.twitch.tv erzeugen und TWITCH_CLIENT_SECRET aktualisieren."
        elif "invalid client" in detail:
            hint = "Die Client-ID stimmt nicht. Auf dev.twitch.tv unter 'Verwalten' kopieren und TWITCH_CLIENT_ID aktualisieren."
        sys.exit(f"Twitch-Anmeldung fehlgeschlagen ({e.code}): {detail}\n{hint}")


class Twitch:
    def __init__(self, client_id, token):
        self.headers = {"Client-Id": client_id, "Authorization": f"Bearer {token}"}

    def get(self, path, params):
        query = urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(f"{API}/{path}?{query}", headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def paged(self, path, params, max_pages):
        items, cursor = [], None
        for _ in range(max_pages):
            p = dict(params)
            if cursor:
                p["after"] = cursor
            res = self.get(path, p)
            items += res.get("data", [])
            cursor = res.get("pagination", {}).get("cursor")
            if not cursor:
                break
        return items


def find_game_id(tw):
    for name in CONFIG["game_names"]:
        res = tw.get("games", {"name": name}).get("data", [])
        if res:
            return res[0]["id"], res[0]["name"]
    res = tw.get("search/categories", {"query": CONFIG["game_names"][0], "first": 5}).get("data", [])
    if res:
        return res[0]["id"], res[0]["name"]
    return None, None


# ---------- Sammeln ----------

def collect(tw, now):
    game_id, game_name = find_game_id(tw)
    if not game_id:
        raise SystemExit("GTA 6 wurde auf Twitch nicht gefunden. Spielnamen in config.json pruefen.")

    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lang = CONFIG["language"]

    streams = tw.paged("streams", {"game_id": game_id, "language": lang, "first": 100}, max_pages=3)
    live = [{
        "login": s["user_login"],
        "name": s["user_name"],
        "viewers": s["viewer_count"],
        "title": s["title"],
        "started_at": s["started_at"],
    } for s in streams]
    live.sort(key=lambda x: -x["viewers"])

    watch = [w.lower() for w in CONFIG["watchlist"]]
    users = tw.get("users", {"login": watch}).get("data", []) if watch else []
    ids = {u["id"]: u["login"] for u in users}
    profiles = {u["login"]: u.get("profile_image_url", "") for u in users}

    clips = {}
    for c in tw.paged("clips", {"game_id": game_id, "started_at": since, "first": 100}, max_pages=CONFIG["game_clip_pages"]):
        if c.get("language") == lang:
            clips[c["id"]] = c
    broadcaster_ids = set(ids)
    if live:
        live_users = tw.get("users", {"login": [l["login"] for l in live[:100]]}).get("data", [])
        broadcaster_ids |= {u["id"] for u in live_users}
        profiles.update({u["login"]: u.get("profile_image_url", "") for u in live_users})
    for bid in broadcaster_ids:
        for c in tw.paged("clips", {"broadcaster_id": bid, "started_at": since, "first": 100}, max_pages=1):
            if c.get("game_id") == game_id:
                clips[c["id"]] = c

    clip_list = [{
        "id": c["id"],
        "streamer": c["broadcaster_name"],
        "login": c["broadcaster_name"].lower(),
        "title": c["title"],
        "views": c["view_count"],
        "duration": round(c.get("duration", 0)),
        "created_at": c["created_at"],
        "url": c["url"],
    } for c in clips.values()]
    clip_list.sort(key=lambda x: -x["views"])

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "game": {"id": game_id, "name": game_name},
        "live": live,
        "clips": clip_list,
        "profiles": profiles,
    }


# ---------- Auswerten ----------

def per_streamer(snapshot):
    stats = {}
    for c in snapshot["clips"]:
        s = stats.setdefault(c["login"], {"name": c["streamer"], "clip_views": 0, "clips": 0})
        s["clip_views"] += c["views"]
        s["clips"] += 1
    for l in snapshot["live"]:
        s = stats.setdefault(l["login"], {"name": l["name"], "clip_views": 0, "clips": 0})
        s["viewers"] = l["viewers"]
    for s in stats.values():
        v = s.get("viewers")
        s["ratio"] = round(s["clip_views"] / v, 2) if v else None
    return stats


def fmt(n):
    return f"{n:,}".replace(",", ".")


def cell(text, limit=None):
    text = text.replace("|", "/").replace("\n", " ").strip()
    return text[:limit] if limit else text


PCT = re.compile(r"(\d{1,3})(?:[.,]\d+)?\s*(?:/\s*100\s*)?%")
STORY_WORDS = re.compile(r"main|story|mission|chapter|kapitel|platin|fortschritt|kampagne", re.I)


def progress_from_title(title):
    """Story-Fortschritt aus dem Stream-Titel. Ein % neben Story-Woertern zaehlt zuerst;
    ein nacktes "100%" ist meist ein Ziel ("100% durchzocken") und wird ignoriert."""
    title = title or ""
    near, other = [], []
    for m in PCT.finditer(title):
        v = int(m.group(1))
        if not 0 < v <= 100:
            continue
        ctx = title[max(0, m.start() - 25):m.end() + 25]
        (near if STORY_WORDS.search(ctx) else other).append(v)
    if near:
        return near[0]
    other = [v for v in other if v < 100]
    return other[0] if other else None


def write_report(snapshot, path):
    stats = per_streamer(snapshot)
    no_tiktok = {x.lower() for x in CONFIG.get("no_tiktok", [])}
    ts = datetime.strptime(snapshot["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    local = ts.astimezone(timezone(timedelta(hours=1))).strftime("%d.%m.%Y %H:%M")

    lines = [f"# Clipgang Report – {local} Uhr", ""]

    lines += ["## Top-Clips der letzten 24 Stunden", ""]
    if snapshot["clips"]:
        lines += ["| # | Streamer | Clip | Views | Länge |", "|---|---|---|---|---|"]
        for i, c in enumerate(snapshot["clips"][:15], 1):
            flag = " ⚠️ nicht TikTok" if c["login"] in no_tiktok else ""
            lines.append(f"| {i} | {c['streamer']}{flag} | [{cell(c['title'], 80)}]({c['url']}) | {fmt(c['views'])} | {c['duration']}s |")
    else:
        lines.append("Noch keine deutschen GTA-6-Clips in den letzten 24 Stunden.")
    lines.append("")

    lines += ["## Gerade live (GTA 6, deutsch)", ""]
    if snapshot["live"]:
        lines += ["| Streamer | Zuschauer | Titel |", "|---|---|---|"]
        for l in snapshot["live"][:15]:
            lines.append(f"| {l['name']} | {fmt(l['viewers'])} | {cell(l['title'], 70)} |")
    else:
        lines.append("Gerade streamt niemand GTA 6 auf Deutsch.")
    lines.append("")

    progress = []
    for l in snapshot["live"]:
        p = progress_from_title(l["title"])
        if p and l["viewers"] >= 500:
            progress.append((p, l))
    if progress:
        lines += ["## Story-Fortschritt laut Stream-Titel", "", "| Streamer | Fortschritt | Zuschauer |", "|---|---|---|"]
        for p, l in sorted(progress, key=lambda x: (-x[0], -x[1]["viewers"]))[:10]:
            lines.append(f"| {l['name']} | {p} % | {fmt(l['viewers'])} |")
        lines.append("")

    lines += ["## Streamer-Ranking nach Clip-Views", ""]
    ranked = sorted(stats.values(), key=lambda s: -s["clip_views"])
    ranked = [s for s in ranked if s["clip_views"] > 0][:15]
    if ranked:
        lines += ["| Streamer | Clip-Views 24h | Clips | Live-Zuschauer | Views pro Zuschauer |", "|---|---|---|---|---|"]
        for s in ranked:
            v = fmt(s["viewers"]) if s.get("viewers") else "–"
            r = str(s["ratio"]).replace(".", ",") if s.get("ratio") is not None else "–"
            lines.append(f"| {s['name']} | {fmt(s['clip_views'])} | {s['clips']} | {v} | {r} |")
        lines += ["", "Hohe *Views pro Zuschauer* = die Community klippt diesen Streamer gerade überdurchschnittlich viel. Dort clippen."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prune_history(now):
    cutoff = now - timedelta(days=HISTORY_DAYS)
    for f in HISTORY.glob("*.json"):
        try:
            t = datetime.strptime(f.stem, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t < cutoff:
            f.unlink()


def merge_recent_history(snapshot, now):
    """Clips aus frueheren Laeufen der letzten 24h ergaenzen.

    Die Twitch-Clip-API liefert nicht bei jedem Lauf alle Clips. Damit ein
    schwacher Lauf keine Clips verliert, werden bekannte Clips der letzten
    24 Stunden mit ihrer zuletzt gemessenen View-Zahl uebernommen.
    """
    cutoff = now - timedelta(hours=24)
    clips = {c["id"]: c for c in snapshot["clips"]}
    fresh = len(clips)
    for f in sorted(HISTORY.glob("*.json")):
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for c in old.get("clips", []):
            created = datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if created < cutoff:
                continue
            known = clips.get(c["id"])
            if known is None or known["views"] < c["views"]:
                clips[c["id"]] = c
    snapshot["clips"] = sorted(clips.values(), key=lambda x: -x["views"])
    snapshot["fetched_clips"] = fresh
    return snapshot


def main():
    cid = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        sys.exit("TWITCH_CLIENT_ID und TWITCH_CLIENT_SECRET fehlen (GitHub Secrets pruefen).")

    now = datetime.now(timezone.utc)
    tw = Twitch(cid, get_token(cid, secret))
    snapshot = collect(tw, now)

    HISTORY.mkdir(parents=True, exist_ok=True)
    # Rohdaten dieses Laufs archivieren, danach mit den letzten 24h zusammenfuehren
    (HISTORY / f"{now:%Y-%m-%d_%H%M}.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    snapshot = merge_recent_history(snapshot, now)
    (DATA / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    prune_history(now)
    write_report(snapshot, ROOT / "REPORT.md")
    print(f"OK: {len(snapshot['live'])} live, {snapshot['fetched_clips']} Clips abgerufen, {len(snapshot['clips'])} Clips in 24h")


if __name__ == "__main__":
    main()
