"""Schneidet einen Twitch-Clip ins Clipgang-Layout (1080x1920).

Layouts:
  standard  unscharfer Hintergrund, Clip mittig in voller Breite, Hook-Box oben
  facecam   Facecam gross oben, Gameplay unten, Hook-Box auf der Trennlinie

Optionen (ueber auftrag.py oder als Funktion cut()):
  untertitel  automatische Untertitel Wort fuer Wort in der Bildmitte; die Hook
              wandert dann nach oben (beim Facecam-Layout in einen eigenen Streifen)
  start       Sekunde, ab der der Clip beginnt, oder "auto" = kurz vor der lautesten
              Stelle (Schrei, Lachen), damit die Pointe sofort kommt
  dauer       maximale Laenge in Sekunden
  badge       kleines Etikett oben links im Clip, z. B. "#3", "TEIL 1" oder ein Name

Aufruf:
  python cut.py <eingabe.mp4> <ausgabe.mp4> "<hook>" "<streamer>" [layout] [facecam]
  python cut.py --vorschau <eingabe.mp4> <bild.jpg>
  python cut.py --raster <eingabe.mp4> <bild.jpg>

facecam: Preset (unten-rechts, unten-links, oben-rechts, oben-links) oder
"x,y,b,h" in Prozent des Bildes, z. B. "72,66,28,34". Leer = aus config.json.
"""

import array
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
FONT = ROOT / "assets" / "Anton.ttf"
WATERMARK = ROOT / "assets" / "wasserzeichen.png"

W, H = 1080, 1920
VIDEO_Y = 690                # Standard: Oberkante des Clips (Clip ist 1080x608)
VIDEO_H = 608
FACE_H = 760                 # Facecam-Layout: Hoehe des Facecam-Bereichs oben
GAME_H = H - FACE_H          # Facecam-Layout: Hoehe des Gameplay-Bereichs unten
TAG_Y_FACECAM = 1490         # Facecam-Layout: Hoehe von @streamer und Logo

# Facecam-Layout mit Untertiteln: Hook-Streifen oben, Untertitel auf der Trennlinie
BAND_H = 360                 # Hoehe des Hook-Streifens oben (unter der TikTok-Leiste)
SUB_FACE_H = 600             # Facecam 360..960
SUB_SPLIT_Y = BAND_H + SUB_FACE_H   # = 960, Trennlinie = Bildmitte
SUB_GAME_H = H - SUB_SPLIT_Y

FPS = 30
AUDIO_RATE = 48000

FACECAM_PRESETS = {           # x, y, breite, hoehe in Prozent des Quellbilds
    "unten-rechts": (72, 66, 28, 34),
    "unten-links": (0, 66, 28, 34),
    "oben-rechts": (72, 0, 28, 34),
    "oben-links": (0, 0, 28, 34),
}
PINK = (255, 46, 136, 255)
WHITE = (255, 255, 255, 255)
DARK = (26, 11, 46, 255)


TEXT_CHARS = set("ÄÖÜäöüß€–—„“‚‘…")
EMOJI_FONTS = [
    ROOT / "assets" / "NotoColorEmoji.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
]
_emoji_cache = {}


def is_text_char(ch):
    return ch.isascii() or ch in TEXT_CHARS


def split_tokens(text):
    """Zerlegt den Hook in Woerter und einzelne Emojis."""
    tokens = []
    for word in text.split():
        buf = ""
        for ch in word:
            if is_text_char(ch):
                buf += ch
            elif ch in "️‍" or 0x1F3FB <= ord(ch) <= 0x1F3FF:
                if tokens and tokens[-1][0] == "emoji":
                    tokens[-1] = ("emoji", tokens[-1][1] + ch, tokens[-1][2])
            else:
                if buf:
                    tokens.append(("text", buf, False))
                    buf = ""
                tokens.append(("emoji", ch, False))
        if buf:
            tokens.append(("text", buf, False))
        if tokens:
            # markieren, dass nach diesem Token ein Leerzeichen kommt
            kind, val, _ = tokens[-1]
            tokens[-1] = (kind, val, True)
    return tokens


def emoji_image(char, size):
    key = (char, size)
    if key in _emoji_cache:
        return _emoji_cache[key]
    font_path = next((p for p in EMOJI_FONTS if p.exists()), None)
    if font_path is None:
        return None
    try:
        efont = ImageFont.truetype(str(font_path), 109)
    except OSError:
        return None
    tile = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((10, 10), char, font=efont, embedded_color=True)
    bbox = tile.getbbox()
    if not bbox:
        return None
    tile = tile.crop(bbox)
    h = int(size * 0.9)
    tile = tile.resize((max(1, int(tile.width * h / tile.height)), h), Image.LANCZOS)
    _emoji_cache[key] = tile
    return tile


def token_width(d, tok, font, size):
    kind, val, _ = tok
    if kind == "text":
        return d.textlength(val, font=font)
    img = emoji_image(val, size)
    return img.width if img else 0


def layout_lines(d, tokens, font, size, max_w):
    space = d.textlength(" ", font=font)
    lines, line, line_w = [], [], 0
    for tok in tokens:
        w = token_width(d, tok, font, size)
        gap = space if line and line[-1][2] else 0
        # ein Emoji nie allein in eine neue Zeile setzen, sondern ans letzte Wort haengen
        if line and line_w + gap + w > max_w and tok[0] != "emoji":
            lines.append((line, line_w))
            line, line_w, gap = [], 0, 0
        line.append(tok)
        line_w += gap + w
    if line:
        lines.append((line, line_w))
    return lines


def draw_hook(img, hook, center_y=None, bottom_y=None, max_lines=3, max_size=90):
    """Pinke Hook-Box zeichnen: mittig um center_y oder mit Unterkante bei bottom_y."""
    d = ImageDraw.Draw(img)
    tokens = split_tokens(hook.strip().upper()) if hook.strip() else []
    tokens = [t for t in tokens if t[0] == "text" or emoji_image(t[1], 60)]
    if not tokens:
        return
    for size in range(max_size, 40, -4):
        font = ImageFont.truetype(str(FONT), size)
        lines = layout_lines(d, tokens, font, size, 860)
        if len(lines) <= max_lines and max(w for _, w in lines) <= 920:
            break
    space = d.textlength(" ", font=font)
    line_h = int(size * 1.18)
    text_w = max(w for _, w in lines)
    pad_x, pad_y = 56, 22
    box_w, box_h = int(text_w + 2 * pad_x), line_h * len(lines) + 2 * pad_y
    bx = (W - box_w) // 2
    by = int(center_y - box_h / 2) if center_y is not None else int(bottom_y - box_h)
    d.rounded_rectangle((bx, by, bx + box_w, by + box_h), radius=26, fill=PINK)
    y = by + pad_y
    for line, lw in lines:
        x = (W - lw) / 2
        for i, (kind, val, sp) in enumerate(line):
            if kind == "text":
                d.text((x, y), val, font=font, fill=WHITE, stroke_width=3, stroke_fill=DARK)
                x += d.textlength(val, font=font)
            else:
                em = emoji_image(val, size)
                ey = int(y + (line_h - em.height) / 2 - size * 0.04)
                img.alpha_composite(em, (int(x), ey))
                x += em.width
            if sp and i < len(line) - 1:
                x += space
        y += line_h


def draw_tag(img, text, x, y, size=52):
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), size)
    d.text((x, y), text, font=font, fill=WHITE, stroke_width=3, stroke_fill=DARK)


def draw_badge(img, text, x, y, size=58):
    """Kleines Etikett (#3, TEIL 1, Streamer-Name) in Dunkel mit pinkem Rand."""
    if not text.strip():
        return
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), size)
    text = text.strip().upper()
    tw = d.textlength(text, font=font)
    pad_x, pad_y = 26, 10
    box = (x, y, x + tw + 2 * pad_x, y + int(size * 1.2) + 2 * pad_y)
    d.rounded_rectangle(box, radius=18, fill=DARK, outline=PINK, width=5)
    d.text((x + pad_x, y + pad_y), text, font=font, fill=WHITE)


def draw_watermark(img, x_right, y):
    wm = Image.open(WATERMARK).convert("RGBA")
    wm.thumbnail((190, 190), Image.LANCZOS)
    alpha = wm.getchannel("A").point(lambda a: int(a * 0.8))
    wm.putalpha(alpha)
    img.alpha_composite(wm, (x_right - wm.width, y - 6))


def build_overlay(hook, streamer, path, box_center_y=None, tag_y=None,
                  hook_bottom_y=None, max_lines=3, max_size=90, badge="", badge_xy=None):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if hook_bottom_y is None and box_center_y is None:
        hook_bottom_y = VIDEO_Y - 70
    draw_hook(img, hook, center_y=box_center_y, bottom_y=hook_bottom_y,
              max_lines=max_lines, max_size=max_size)

    below = tag_y if tag_y is not None else VIDEO_Y + VIDEO_H + 36
    if streamer.strip():
        draw_tag(img, f"@{streamer.strip().lstrip('@')}", 48, below)
    draw_watermark(img, W - 44, below)
    if badge and badge_xy:
        draw_badge(img, badge, *badge_xy)
    img.save(path)


# ---------- Hilfsfunktionen Video/Audio ----------

def run(cmd):
    subprocess.run(cmd, check=True)


def probe_size(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(src)],
        check=True, capture_output=True, text=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def probe_duration(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
        check=True, capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def has_audio(src):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip()
    return bool(out)


def loudness_curve(src, window=0.1):
    """Lautstaerke (RMS) pro Zeitfenster, ohne numpy."""
    rate = 8000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate),
         "-f", "s16le", "-"], capture_output=True).stdout
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) // 2 * 2])
    n = int(rate * window)
    curve = []
    for i in range(0, len(samples) - n + 1, n):
        chunk = samples[i:i + n]
        curve.append(math.sqrt(sum(s * s for s in chunk) / n))
    return curve


def find_punch(src, lead=2.5, min_rest=8.0):
    """Startzeit kurz vor der lautesten Stelle (Reaktion = Pointe).

    Gibt 0 zurueck, wenn die lauteste Stelle ohnehin am Anfang liegt
    oder danach zu wenig Clip uebrig bliebe.
    """
    if not has_audio(src):
        return 0.0
    curve = loudness_curve(src)
    if len(curve) < 20:
        return 0.0
    k = 5   # ueber 0,5 s glaetten, damit ein einzelnes Knacken nicht zaehlt
    smooth = [sum(curve[max(0, i - k):i + k + 1]) / len(curve[max(0, i - k):i + k + 1])
              for i in range(len(curve))]
    peak = max(range(len(smooth)), key=smooth.__getitem__) * 0.1
    total = len(curve) * 0.1
    start = peak - lead
    if start < 1.5:
        return 0.0
    start = min(start, max(0.0, total - min_rest))
    return round(start, 1)


def prepare(src, dst, start=0.0, dauer=None):
    """Clip vereinheitlichen: 30 fps, 1920x1080, Stereo-Ton 48 kHz, optional gekuerzt.

    Fehlt der Ton, wird Stille eingesetzt, damit Zusammenschnitte und Plattformen funktionieren.
    """
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.2f}"]
    if dauer:
        cmd += ["-t", f"{dauer:.2f}"]
    cmd += ["-i", str(src)]
    audio = has_audio(src)
    if not audio:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
    cmd += [
        "-filter_complex",
        "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[v]",
        "-map", "[v]", "-map", "0:a" if audio else "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-shortest", "-movflags", "+faststart", str(dst),
    ]
    run(cmd)


def encode(src, overlay, filt, dst, extra_inputs=(), audio="0:a?"):
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(overlay)]
    for e in extra_inputs:
        cmd += ["-i", str(e)]
    cmd += [
        "-filter_complex", filt, "-map", "[v]", "-map", audio,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ]
    run(cmd)


# ---------- Untertitel ----------

def transcribe(src, model_name="small"):
    """Woerter mit Zeitstempeln: [(wort, start, ende), ...]. Braucht faster-whisper."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Hinweis: faster-whisper fehlt, Untertitel werden uebersprungen.")
        return []
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(src), language="de", word_timestamps=True,
                                   vad_filter=True, beam_size=5)
    words = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append((text, float(w.start), float(w.end)))
    return words


def ass_time(t):
    t = max(t, 0)
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def clean_word(w):
    return w.replace("{", "").replace("}", "").replace("\\", "").upper()


def chunk_words(words, max_words=3, max_gap=0.6):
    """Woerter in kurze Gruppen (max. 3 Woerter) teilen, an Pausen und Satzzeichen trennen."""
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[1] - cur[-1][2] > max_gap
                    or cur[-1][0][-1:] in ".!?,"):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def write_ass(path, word_tracks, size=112):
    """word_tracks: Liste von (woerter, y_mitte, zeitversatz). Aktives Wort pink."""
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Sub,Anton,{size},&H00FFFFFF,&H00FFFFFF,&H002E0B1A,&H64000000,0,0,0,0,100,100,1,0,1,6,2,5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    pink = "&H882EFF&"   # ASS-Farben sind BGR
    lines = []
    for words, y, offset in word_tracks:
        for chunk in chunk_words(words):
            for i, (_, s, e) in enumerate(chunk):
                end = chunk[i + 1][1] if i + 1 < len(chunk) else e + 0.15
                parts = []
                for j, (t, _, _) in enumerate(chunk):
                    t = clean_word(t)
                    parts.append(f"{{\\c{pink}}}{t}{{\\c&HFFFFFF&}}" if j == i else t)
                text = f"{{\\an5\\pos(540,{y})}}" + " ".join(parts)
                lines.append(f"Dialogue: 0,{ass_time(s + offset)},{ass_time(end + offset)},Sub,,0,0,0,,{text}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return bool(lines)


def subs_filter(ass_path):
    return f"ass={ass_path}:fontsdir={ROOT / 'assets'}"


# ---------- Layouts ----------

def facecam_rect(spec, streamer, iw, ih, face_h=FACE_H):
    """Facecam-Ausschnitt in Pixeln, auf das Seitenverhaeltnis des oberen Bereichs zugeschnitten."""
    spec = (spec or "").strip().lower()
    if not spec:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        spec = cfg.get("facecams", {}).get(streamer.strip().lower().lstrip("@"), "")
    if not spec:
        sys.exit(f"Facecam-Position fuer '{streamer}' unbekannt. Preset "
                 f"({', '.join(FACECAM_PRESETS)}) oder 'x,y,b,h' in Prozent angeben.")
    if spec in FACECAM_PRESETS:
        px, py, pw, ph = FACECAM_PRESETS[spec]
    else:
        try:
            px, py, pw, ph = (float(v) for v in spec.replace(" ", "").split(","))
        except ValueError:
            sys.exit(f"Facecam-Angabe '{spec}' nicht verstanden.")
    x, y, w, h = iw * px / 100, ih * py / 100, iw * pw / 100, ih * ph / 100
    cx, cy = x + w / 2, y + h / 2
    target = W / face_h
    # innerhalb des Facecam-Rahmens bleiben: lieber knapp anschneiden als Spielbild mitnehmen
    if w / h > target:
        w = h * target
    else:
        h = w / target
    w, h = min(w, iw), min(h, ih)
    x = min(max(cx - w / 2, 0), iw - w)
    y = min(max(cy - h / 2, 0), ih - h)
    ev = lambda v: int(v) // 2 * 2
    return ev(x), ev(y), ev(w), ev(h)


def has_facecam(streamer, facecam=""):
    if (facecam or "").strip():
        return True
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    return bool(cfg.get("facecams", {}).get(streamer.strip().lower().lstrip("@")))


def cut(src, dst, hook, streamer, layout="standard", facecam="", untertitel=False,
        start=0.0, dauer=None, badge="", words=None):
    """Einen Clip schneiden. start kann eine Zahl oder "auto" sein."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if start == "auto":
            start = find_punch(src)
            print(f"Auto-Start: {start:.1f} s")
        start = float(start or 0)
        clip = tmp / "clip.mp4"
        prepare(src, clip, start, dauer)
        iw, ih = probe_size(clip)

        overlay = tmp / "overlay.png"
        ass = tmp / "subs.ass"
        subs = False
        if untertitel:
            if words is None:
                words = transcribe(clip)
            sub_y = SUB_SPLIT_Y if layout == "facecam" else VIDEO_Y + VIDEO_H // 2
            subs = write_ass(ass, [(words, sub_y, 0.0)])

        if layout == "facecam" and subs:
            # Hook-Streifen oben, Facecam, Untertitel genau auf der Bildmitte, Gameplay unten
            fx, fy, fw, fh = facecam_rect(facecam, streamer, iw, ih, face_h=SUB_FACE_H)
            gw = min(iw, int(ih * W / SUB_GAME_H) // 2 * 2)
            gx = (iw - gw) // 2 // 2 * 2
            build_overlay(hook, streamer, overlay, box_center_y=BAND_H // 2 + 50,
                          tag_y=TAG_Y_FACECAM, max_lines=2, max_size=74,
                          badge=badge, badge_xy=(40, SUB_SPLIT_Y + 110))
            filt = (
                f"[0:v]split=3[a][b][c];"
                f"[c]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{BAND_H}:0:0,"
                f"boxblur=24:2,eq=brightness=-0.3,setsar=1[band];"
                f"[a]crop={fw}:{fh}:{fx}:{fy},scale={W}:{SUB_FACE_H},setsar=1[face];"
                f"[b]crop={gw}:{ih}:{gx}:0,scale={W}:{SUB_GAME_H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{SUB_GAME_H},setsar=1[game];"
                f"[band][face][game]vstack=3[v1];"
                f"[v1][1:v]overlay=0:0,{subs_filter(ass)},format=yuv420p[v]"
            )
        elif layout == "facecam":
            fx, fy, fw, fh = facecam_rect(facecam, streamer, iw, ih)
            gw = min(iw, int(ih * W / GAME_H) // 2 * 2)
            gx = (iw - gw) // 2 // 2 * 2
            build_overlay(hook, streamer, overlay, box_center_y=FACE_H, tag_y=TAG_Y_FACECAM,
                          badge=badge, badge_xy=(40, 40 + 160))
            filt = (
                f"[0:v]split[a][b];"
                f"[a]crop={fw}:{fh}:{fx}:{fy},scale={W}:{FACE_H},setsar=1[face];"
                f"[b]crop={gw}:{ih}:{gx}:0,scale={W}:{GAME_H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{GAME_H},setsar=1[game];"
                f"[face][game]vstack[v1];"
                f"[v1][1:v]overlay=0:0,format=yuv420p[v]"
            )
        else:
            build_overlay(hook, streamer, overlay, badge=badge, badge_xy=(40, VIDEO_Y + 24))
            tail = f",{subs_filter(ass)}" if subs else ""
            filt = (
                f"[0:v]split[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=24:2,eq=brightness=-0.18[bg];"
                f"[b]scale={W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2:color=black[fg];"
                f"[bg][fg]overlay=0:{VIDEO_Y}[v1];"
                f"[v1][1:v]overlay=0:0{tail},format=yuv420p[v]"
            )
        encode(clip, overlay, filt, dst)


def preview(src, dst, grid=False):
    """Standbild aus der Clipmitte; mit grid=True ein Prozent-Raster zum Ausmessen der Facecam."""
    t = max(probe_duration(src) * 0.4, 0)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "f.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src),
                        "-frames:v", "1", str(raw)], check=True)
        img = Image.open(raw).convert("RGB")
        img.thumbnail((1280, 1280))
        if grid:
            d = ImageDraw.Draw(img)
            font = ImageFont.truetype(str(FONT), 18)
            for p in range(10, 100, 10):
                x, y = img.width * p / 100, img.height * p / 100
                d.line((x, 0, x, img.height), fill=(255, 46, 136), width=1)
                d.line((0, y, img.width, y), fill=(0, 229, 212), width=1)
                d.text((x + 3, 3), str(p), font=font, fill=(255, 46, 136), stroke_width=2, stroke_fill=(0, 0, 0))
                d.text((3, y + 2), str(p), font=font, fill=(0, 229, 212), stroke_width=2, stroke_fill=(0, 0, 0))
        img.save(dst, quality=85)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] in ("--vorschau", "--raster"):
        preview(sys.argv[2], sys.argv[3], grid=sys.argv[1] == "--raster")
        sys.exit(0)
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    args = sys.argv[1:] + [""] * 6
    src, dst, hook, streamer, layout, facecam = args[:6]
    cut(src, dst, hook, streamer, layout or "standard", facecam)
    print(f"Fertig: {dst}")
