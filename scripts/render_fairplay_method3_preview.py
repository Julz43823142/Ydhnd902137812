"""Render nine deterministic, synthetic Method 3 images for CI visual QA.

No live username, game, authentication secret, API call or engine evaluation.
Run: python scripts/render_fairplay_method3_preview.py [output_dir]
"""
import json
import struct
import sys
from pathlib import Path

from test_fairplay_v83_landscape import TrueLandscape
from fairplay_method3_ui import chart_pngs
from fairplay_method3 import VERSION


def main():
    output=Path(sys.argv[1] if len(sys.argv)>1 else '.artifacts/fairplay-method3-preview')
    output.mkdir(parents=True,exist_ok=True)
    result=TrueLandscape.result()
    files=chart_pngs(result)
    if len(files)!=9:raise RuntimeError(f'Expected 9 charts; got {len(files)}')
    sizes=[]
    for index,(name,blob) in enumerate(files,1):
        if not blob.startswith(bytes.fromhex('89504e470d0a1a0a')):
            raise RuntimeError(f'Invalid PNG: {name}')
        width,height=struct.unpack('>II',blob[16:24])
        if width!=1800:raise RuntimeError(f'Unexpected width: {name} ({width})')
        if index in (4,7,9) and height>1350:
            raise RuntimeError(f'Regression to portrait output: {name} ({height}px)')
        (output/name).write_bytes(blob)
        sizes.append(dict(number=index,name=name,width=width,height=height,
                          bytes=len(blob)))
        print(f'#{index}: {name} {width}x{height}, {len(blob):,} bytes')
    (output/'manifest.json').write_text(
        json.dumps({'version':VERSION,'source':'synthetic regression fixture',
                    'games':len(result.games),'charts':sizes},indent=2),
        encoding='utf-8')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
