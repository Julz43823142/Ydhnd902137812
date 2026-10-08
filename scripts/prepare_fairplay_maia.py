"""Explicit deployment-time download of one checksum-pinned official model."""
import argparse
import hashlib
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fairplay_maia import MODEL_REVISION, MODEL_SHA256

URL='https://huggingface.co/UofTCSSLab/Maia3-5M/resolve/'+MODEL_REVISION+'/maia3-5m.pt'


def prepare(path,github_env=None):
    import requests
    path=Path(path)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=MODEL_SHA256:
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.download')
        try:
            with requests.get(URL,stream=True,timeout=(15,45)) as response:
                response.raise_for_status()
                digest=hashlib.sha256();size=0
                with temp.open('wb') as handle:
                    for data in response.iter_content(1024*1024):
                        size+=len(data)
                        if size>25*1024*1024:raise ValueError('Model exceeds expected size')
                        digest.update(data);handle.write(data)
                if digest.hexdigest()!=MODEL_SHA256:raise ValueError('Model checksum mismatch')
            temp.replace(path)
        finally:temp.unlink(missing_ok=True)
    if github_env:
        value=str(path.resolve())
        if any(c in value for c in '\r\n'):raise ValueError('Invalid checkpoint path')
        with open(github_env,'a',encoding='utf-8') as handle:
            handle.write('FAIRPLAY_MAIA_CHECKPOINT='+value+'\n')
    print('Verified local Maia-3 5M checkpoint.')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('path',type=Path)
    parser.add_argument('--github-env',action='store_true')
    args=parser.parse_args()
    prepare(args.path,os.environ['GITHUB_ENV'] if args.github_env else None)
