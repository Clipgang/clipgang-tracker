"""Schneidet einen Twitch-Clip ins Clipgang-Layout (1080x1920).

Layout: unscharfer Hintergrund, Clip mittig in voller Breite, pinke Hook-Box
oben, @streamer unten links, Wasserzeichen unten rechts.

Aufruf: python cut.py <eingabe.mp4> <ausgabe.mp4> "<hook>" "<streamer>"
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
FONT = ROOT / "assets" / "Anton.ttf"
WATERMARK = ROOT / "assets" / "wasserzeichen.png"

W, H = 1080, 1920
VIDEO_Y = 690                # Oberkante des Clips (Clip ist 1080x608)
VIDEO_H = 608
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
        if line and line_w + gap + w > max_w:
            lines.append((line, line_w))
            line, line_w, gap = [], 0, 0
        line.append(tok)
        line_w += gap + w
    if line:
        lines.append((line, line_w))
    return lines


def build_overlay(hook, streamer, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Hook-Box: Schrift so gross wie moeglich, max. 3 Zeilen
    tokens = split_tokens(hook.strip().upper()) if hook.strip() else []
    tokens = [t for t in tokens if t[0] == "text" or emoji_image(t[1], 60)]
    if tokens:
        for size in range(90, 44, -4):
            font = ImageFont.truetype(str(FONT), size)
            lines = layout_lines(d, tokens, font, size, 860)
            if len(lines) <= 3:
                break
        space = d.textlength(" ", font=font)
        line_h = int(size * 1.18)
        text_w = max(w for _, w in lines)
        pad_x, pad_y = 56, 22
        box_w, box_h = int(text_w + 2 * pad_x), line_h * len(lines) + 2 * pad_y
        bx, by = (W - box_w) // 2, VIDEO_Y - box_h - 70
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

    below = VIDEO_Y + VIDEO_H + 36

    # @streamer unten links
    if streamer.strip():
        tag_font = ImageFont.truetype(str(FONT), 52)
        d.text((48, below), f"@{streamer.strip().lstrip('@')}", font=tag_font,
               fill=WHITE, stroke_width=3, stroke_fill=DARK)

    # Wasserzeichen unten rechts, 80 % Deckkraft
    wm = Image.open(WATERMARK).convert("RGBA")
    wm.thumbnail((190, 190), Image.LANCZOS)
    alpha = wm.getchannel("A").point(lambda a: int(a * 0.8))
    wm.putalpha(alpha)
    img.alpha_composite(wm, (W - wm.width - 44, below - 6))

    img.save(path)


def cut(src, dst, hook, streamer):
    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / "overlay.png"
        build_overlay(hook, streamer, overlay)
        filt = (
            f"[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.18[bg];"
            f"[b]scale={W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2:color=black[fg];"
            f"[bg][fg]overlay=0:{VIDEO_Y}[v1];"
            f"[v1][1:v]overlay=0:0,format=yuv420p[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(overlay),
            "-filter_complex", filt, "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", "30",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst),
        ]
        subprocess.run(cmd, check=True)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("Aufruf: python cut.py <eingabe.mp4> <ausgabe.mp4> \"<hook>\" \"<streamer>\"")
    src, dst = sys.argv[1], sys.argv[2]
    hook = sys.argv[3] if len(sys.argv) > 3 else ""
    streamer = sys.argv[4] if len(sys.argv) > 4 else ""
    cut(src, dst, hook, streamer)
    print(f"Fertig: {dst}")
