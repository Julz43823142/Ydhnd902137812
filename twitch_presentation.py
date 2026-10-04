"""Temporary presentation only. Successful Twitch checks own the shared live flag."""
import base64
import json
import time
from pathlib import Path

import shared_leaderboard as ledger
from market_transactions import run

FILE='twitch_live_mode.json'
_cache=(0,False)


def set_status(stream):
    global _cache
    live=stream is not None;stream_id=str((stream or {}).get('id','offline'))
    def build():
        raw=ledger._origin_file(FILE);previous=json.loads(raw) if raw else {}
        seen=list(previous.get('recorded_streams',[]))
        data={'live':live,'stream_id':stream_id,'checked_at':int(time.time()),'recorded_streams':seen}
        files={FILE:json.dumps(data)}
        if live and stream_id not in seen:
            seen.append(stream_id)
            files[FILE]=json.dumps(data)
            import shark_activity
            act=shark_activity.read();shark_activity.add(act,'server','Server','twitch_streams',1,time.time())
            files[shark_activity.FILE]=json.dumps(act)
        return files,data,'twitch-status'
    import uuid
    result=run(f'twitch-status:{stream_id}:{uuid.uuid4().hex}',build)
    _cache=(time.monotonic(),live)
    return result


def live():
    global _cache
    now=time.monotonic()
    if now-_cache[0]<20:return _cache[1]
    with ledger.REPOSITORY_LOCK:
        raw=ledger._origin_file(FILE)
        enabled=bool(json.loads(raw).get('live')) if raw else False
    _cache=(now,enabled);return enabled


def overlay(svg,enabled=None):
    if not (live() if enabled is None else enabled):return svg
    # Holiday and equipped theme artwork stays primary; live backdrop decorates the edges.
    asset=Path(__file__).parent/'assets'/'twitch-live-overlay.svg'
    uri='data:image/svg+xml;base64,'+base64.b64encode(asset.read_bytes()).decode()
    mark=(f'<image href="{uri}" width="1200" height="820" preserveAspectRatio="none"/>'
          '<rect x="32" y="28" width="145" height="42" rx="21" fill="#451878"/>'
          '<circle cx="53" cy="49" r="6" fill="#dba9ff"/>'
          '<text x="68" y="56" font-family="DejaVu Sans" font-size="18" fill="#ffffff" font-weight="bold">LIVE</text>')
    return svg.rsplit('</svg>',1)[0]+mark+'</svg>'
