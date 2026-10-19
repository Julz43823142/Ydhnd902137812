"""Owner-only, explicitly fictional Fair Play parody report.

Never reads or writes the actual Fair Play analysis pipeline.
"""
import io
import math
import re

from PIL import Image, ImageDraw, ImageFont


def sanitize_username(value):
    name = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,25}", name):
        raise ValueError("Enter a username (1-25 letters, numbers, _ or -).")
    return name


def build_sus_chart(username):
    """Produce a labeled, synthetic expected-vs-observed chart."""
    name = sanitize_username(username)
    image = Image.new("RGB", (1000, 540), "#101827")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text((40, 25), "FAIR PLAY / ANALYSIS REPORT", fill="#f5f5f5", font=font)
    draw.text((40, 49), f"Player: {name}", fill="#fbbf24", font=font)
    left, top, right, bottom = 90, 100, 955, 440
    for n in range(5):
        y = top + n * (bottom - top) / 4
        draw.line((left, y, right, y), fill="#334155", width=1)
    expected = [22, 24, 23, 26, 27, 25, 29, 27, 30, 29, 31, 30]
    observed = [69, 72, 77, 75, 81, 86, 83, 89, 92, 90, 95, 98]
    def coords(values):
        return [(round(left + i * (right - left) / (len(values) - 1)),
                 round(bottom - v * (bottom - top) / 100)) for i, v in enumerate(values)]
    draw.line(coords(expected), fill="#3b82f6", width=5, joint="curve")
    draw.line(coords(observed), fill="#fb923c", width=5, joint="curve")
    for x, y in coords(expected):
        draw.ellipse((x-4, y-4, x+4, y+4), fill="#3b82f6")
    for x, y in coords(observed):
        draw.ellipse((x-4, y-4, x+4, y+4), fill="#fb923c")
    draw.text((90, 462), "BLUE: EXPECTED", fill="#60a5fa", font=font)
    draw.text((280, 462), "ORANGE: OBSERVED", fill="#fdba74", font=font)
    draw.text((90, 491), "VERY HIGH suspicion  |  HIGH confidence  |  100% CHEATER", fill="#fbbf24", font=font)
    draw.text((90, 515), "Fair Play v62 | History Blocked | Simulated data - not an actual Fair Play assessment.", fill="#fbbf24", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    return output
