"""Clipgang-Formate, die ueber den Einzelclip hinausgehen.

  versus           zwei Streamer uebereinander, nacheinander abgespielt (der inaktive
                   steht still und abgedunkelt), Hook auf der Trennlinie
  zusammenschnitt  mehrere Clips hintereinander, mit Countdown-Etikett (#3 -> #1)
                   oder Streamer-Namen (z. B. "3 Streamer, gleiches Finale")
  rennen           Story-Rennen: animiertes Ranking aus den Stream-Titeln
                   ("83% Main Mission"), komplett aus den Tracker-Daten, ohne fremde Clips
"""

import json
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import cut as C

ROOT = Path(__file__).parent
HALF = C.H // 2


# ---------- Versus ----------

def versus(src_a, src_b, dst, hook, name_a, name_b, untertitel=False,
           start_a=0.0, start_b=0.0, dauer=20.0, words_a=None, words_b=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clips = []
        for src, start in ((src_a, start_a), (src_b, start_b)):
            if start == "auto":
                start = C.find_punch(src)
            out = tmp / f"v{len(clips)}.mp4"
            C.prepare(src, out, float(start or 0), dauer)
            clips.append(out)
        a, b = clips
        da, db = C.probe_duration(a), C.probe_duration(b)

        overlay = tmp / "overlay.png"
        img = Image.new("RGBA", (C.W, C.H), (0, 0, 0, 0))
        if hook.strip():
            C.draw_hook(img, hook, center_y=HALF, max_lines=2, max_size=78)
        else:
            C.draw_badge(img, "VS", C.W // 2 - 60, HALF - 50, size=70)
        C.draw_tag(img, f"@{name_a.lstrip('@')}", 48, 190)
        C.draw_tag(img, f"@{name_b.lstrip('@')}", 48, HALF + 110)
        C.draw_watermark(img, C.W - 44, C.TAG_Y_FACECAM)
        img.save(overlay)

        ass = tmp / "subs.ass"
        subs = False
        if untertitel:
            wa = words_a if words_a is not None else C.transcribe(a)
            wb = words_b if words_b is not None else C.transcribe(b)
            subs = C.write_ass(ass, [(wa, HALF - 200, 0.0), (wb, C.H - 420, da)], size=96)

        half = f"scale=-2:{HALF},crop={C.W}:{HALF},setsar=1"
        tail = f",{C.subs_filter(ass)}" if subs else ""
        filt = (
            f"[0:v]{half},tpad=stop_mode=clone:stop_duration={db:.2f},"
            f"eq=brightness=-0.28:saturation=0.5:enable='gte(t,{da:.2f})'[top];"
            f"[2:v]{half},tpad=start_mode=clone:start_duration={da:.2f},"
            f"eq=brightness=-0.28:saturation=0.5:enable='lt(t,{da:.2f})'[bot];"
            f"[top][bot]vstack[v1];"
            f"[v1][1:v]overlay=0:0{tail},format=yuv420p[v];"
            f"[0:a][2:a]concat=n=2:v=0:a=1[aout]"
        )
        C.encode(a, overlay, filt, dst, extra_inputs=[b], audio="[aout]")


# ---------- Zusammenschnitt ----------

def zusammenschnitt(items, dst, hook, modus="countdown", untertitel=False, dauer=15.0):
    """items: Liste von dicts mit src, streamer, optional start und badge.

    countdown: das erste Element bekommt die hoechste Nummer, das letzte #1.
    namen:     Etikett = Streamer-Name.
    """
    n = len(items)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for i, it in enumerate(items):
            if it.get("badge"):
                badge = it["badge"]
            elif modus == "countdown":
                badge = f"#{n - i}"
            else:
                badge = it.get("streamer", "")
            seg = tmp / f"seg{i}.mp4"
            C.cut(it["src"], seg, hook, it.get("streamer", ""), "standard",
                  untertitel=untertitel, start=it.get("start", 0), dauer=it.get("dauer", dauer),
                  badge=badge, words=it.get("words"))
            parts.append(seg)
        lst = tmp / "liste.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        C.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
               "-c", "copy", "-movflags", "+faststart", str(dst)])


# ---------- Story-Rennen ----------

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


def load_snapshot(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def progress_table(data_dir, anzahl=6, min_viewers=500):
    """Aktueller Fortschritt laut Stream-Titel + Wert von vor ~24 h."""
    data_dir = Path(data_dir)
    latest = load_snapshot(data_dir / "latest.json")
    gen = datetime.strptime(latest["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    watch = {w.lower() for w in cfg.get("watchlist", [])}

    # aeltere Snapshots: letzter bekannter Stand je Streamer bis ~24 h vor jetzt
    before = {}
    for f in sorted((data_dir / "history").glob("*.json")):
        try:
            t = datetime.strptime(f.stem, "%Y-%m-%d_%H%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t > gen - timedelta(hours=20):
            continue
        for l in load_snapshot(f).get("live", []):
            p = progress_from_title(l["title"])
            if p:
                before[l["login"]] = p

    rows = []
    for l in latest["live"]:
        p = progress_from_title(l["title"])
        if not p:
            continue
        if l["viewers"] < min_viewers and l["login"] not in watch:
            continue
        rows.append({"login": l["login"], "name": l["name"], "viewers": l["viewers"],
                     "now": p, "before": before.get(l["login"])})
    rows.sort(key=lambda r: -r["viewers"])
    rows = rows[: anzahl * 2]
    rows.sort(key=lambda r: (-r["now"], -r["viewers"]))
    return rows[:anzahl], gen, latest.get("profiles", {})


def avatar(url, name, size):
    img = None
    if url:
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                img = Image.open(BytesIO(r.read())).convert("RGBA")
        except Exception:
            img = None
    if img is None:
        img = Image.new("RGBA", (size, size), C.PINK)
        d = ImageDraw.Draw(img)
        font = ImageFont.truetype(str(C.FONT), int(size * 0.55))
        ch = name[:1].upper()
        tw = d.textlength(ch, font=font)
        d.text(((size - tw) / 2, size * 0.12), ch, font=font, fill=C.WHITE)
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def rennen(dst, data_dir, hook="WER IST ZUERST DURCH? 😳", anzahl=6, dauer=9.0):
    rows, gen, profiles = progress_table(data_dir, anzahl)
    if len(rows) < 2:
        raise SystemExit("Zu wenige Streamer mit Prozentangabe im Stream-Titel fuer ein Story-Rennen.")
    local = gen.astimezone(timezone(timedelta(hours=1)))

    # statischer Hintergrund mit Hook, Unterzeile, Avataren und Namen
    base = Image.new("RGBA", (C.W, C.H), C.DARK)
    d = ImageDraw.Draw(base)
    for y in range(C.H):   # leichter Verlauf nach unten
        k = y / C.H
        d.line((0, y, C.W, y), fill=(int(26 + 30 * k), int(11 + 4 * k), int(46 + 40 * k), 255))
    C.draw_hook(base, hook, center_y=300, max_lines=2, max_size=86)
    small = ImageFont.truetype(str(C.FONT), 38)
    sub = f"STORY-FORTSCHRITT LAUT STREAM-TITEL · {local:%d.%m.} {local:%H} UHR"
    d.text(((C.W - d.textlength(sub, font=small)) / 2, 440), sub, font=small, fill=(255, 255, 255, 200))

    top, bottom = 540, 1560
    row_h = (bottom - top) / len(rows)
    av = int(min(120, row_h * 0.62))
    name_font = ImageFont.truetype(str(C.FONT), int(min(54, row_h * 0.3)))
    pct_font = ImageFont.truetype(str(C.FONT), int(min(64, row_h * 0.36)))
    delta_font = ImageFont.truetype(str(C.FONT), int(min(36, row_h * 0.2)))
    bar_x0, bar_x1 = 60 + av + 30, C.W - 60
    geo = []
    for i, r in enumerate(rows):
        y0 = top + i * row_h
        base.alpha_composite(avatar(profiles.get(r["login"]), r["name"], av), (60, int(y0 + 8)))
        d.text((bar_x0, y0 + 4), f"{i + 1}. {r['name']}", font=name_font, fill=C.WHITE,
               stroke_width=2, stroke_fill=C.DARK)
        by0 = y0 + row_h * 0.42
        by1 = by0 + row_h * 0.36
        d.rounded_rectangle((bar_x0, by0, bar_x1, by1), radius=14, fill=(72, 48, 104, 255))
        geo.append((by0, by1))
    C.draw_watermark(base, C.W - 44, 1600)

    frames = int(dauer * C.FPS)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{C.W}x{C.H}", "-r", str(C.FPS), "-i", "-",
           "-f", "lavfi", "-i", f"anullsrc=r={C.AUDIO_RATE}:cl=stereo",
           "-map", "0:v", "-map", "1:a", "-shortest",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dst)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    last = None
    for f in range(frames):
        t = f / C.FPS
        k = ease((t - 0.6) / 2.6)
        key = round(k, 3)
        if key != last:
            img = base.copy()
            dd = ImageDraw.Draw(img)
            for r, (by0, by1) in zip(rows, geo):
                start = r["before"] if r["before"] is not None and r["before"] <= r["now"] else r["now"]
                val = start + (r["now"] - start) * k
                x1 = bar_x0 + (bar_x1 - bar_x0) * val / 100
                if x1 - bar_x0 > 28:
                    dd.rounded_rectangle((bar_x0, by0, x1, by1), radius=14, fill=C.PINK)
                label = f"{val:.0f} %"
                lw = dd.textlength(label, font=pct_font)
                lx = min(max(x1 - lw - 16, bar_x0 + 12), bar_x1 - lw - 12)
                dd.text((lx, by0 + (by1 - by0 - pct_font.size * 1.2) / 2), label, font=pct_font,
                        fill=C.WHITE, stroke_width=3, stroke_fill=C.DARK)
                if r["before"] is not None and r["now"] > r["before"] and k > 0.95:
                    delta = f"+{r['now'] - r['before']} % SEIT GESTERN"
                    dw = dd.textlength(delta, font=delta_font)
                    dd.text((bar_x1 - dw, by1 + 6), delta, font=delta_font, fill=(0, 229, 212, 255))
            frame = img.tobytes()
            last = key
        proc.stdin.write(frame)
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg ist beim Story-Rennen fehlgeschlagen.")
    return rows
