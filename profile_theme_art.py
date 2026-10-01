"""Local cinematic profile backgrounds: no image fetches in Discord interactions."""
import base64
from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'assets' / 'profile_themes'


@lru_cache(maxsize=1)
def catalog():
    return json.loads((ROOT / 'catalog.json').read_text())


@lru_cache(maxsize=24)
def image_uri(theme_key):
    entry = catalog()[theme_key]
    data = (ROOT / entry['file']).read_bytes()
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')
