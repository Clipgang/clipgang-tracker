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


def wrap(draw, text, font, max_w):
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=font) <= max_w or not line:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def clean(text):
    """Emojis entfernen – die Schrift kann sie nicht darstellen."""
    keep = set("ÄÖÜäöüß€–—„“‚‘…")
    out = "".join(ch for ch in text if ch.isascii() or ch in keep)
    return " ".join(out.split())


def build_overlay(hook, streamer, path):
    hook = clean(hook)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Hook-Box: Schrift so gross wie moeglich, max. 3 Zeilen
    if hook.strip():
        hook = hook.strip().upper()
        for size in range(104, 50, -4):
            font = ImageFont.truetype(str(FONT), size)
            lines = wrap(d, hook, font, 900)
            if len(lines) <= 3:
                break
        line_h = int(size * 1.18)
        text_w = max(d.textlength(l, font=font) for l in lines)
        box_w, box_h = int(text_w + 80), line_h * len(lines) + 56
        bx, by = (W - box_w) // 2, VIDEO_Y - box_h - 70
        d.rounded_rectangle((bx, by, bx + box_w, by + box_h), radius=28, fill=PINK)
        y = by + 28
        for l in lines:
            lw = d.textlength(l, font=font)
            d.text(((W - lw) / 2, y), l, font=font, fill=WHITE, stroke_width=3, stroke_fill=DARK)
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
