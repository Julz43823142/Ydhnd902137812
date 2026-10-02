"""Local cinematic profile backgrounds: no image fetches in Discord interactions."""
import base64
from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'assets' / 'profile_themes'


@lru_cache(maxsize=1)
def catalog():
    return json.loads((ROOT / 'catalog.json').read_text())


@lru_cache(maxsize=40)
def image_uri(theme_key, layer='file'):
    entry = catalog()[theme_key]
    path = ROOT / entry[layer]
    data = path.read_bytes()
    mime = 'image/png' if path.suffix == '.png' else 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
