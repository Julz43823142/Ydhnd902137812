"""Compare real objective evidence using full and compute-only source packages."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CHILD='''
import json,os,sys,time
from pathlib import Path
sys.path.insert(0,os.getcwd())
import fairplay_analysis as analysis
import scripts.fairplay_compute_worker
from fairplay_config import CONFIG
from fairplay_data import parse_game
from fairplay_distributed import serialize_game,deserialize_game
from fairplay_opening import opening_reference
from scripts.smoke_stockfish_reviews import fixture,TARGET
assert Path(analysis.__file__).resolve().is_relative_to(Path.cwd())
game=parse_game(fixture(),TARGET)
scanner=analysis.EngineScanner(time.monotonic()+120)
try:scanner.analyse(game,CONFIG.fast_nodes)
finally:scanner.close()
assert opening_reference(), 'Required offline opening reference is absent'
roundtrip=deserialize_game(serialize_game(game))
assert roundtrip.metrics==game.metrics
print(json.dumps({'metrics':game.metrics,'decisions':[d.metrics for d in game.decisions],
                  'reference_positions':len(opening_reference())},sort_keys=True))
'''


def main():
    names=subprocess.check_output(['git','ls-tree','-r','--name-only','HEAD'],cwd=ROOT,text=True).splitlines()
    selected=[name for name in names if ('/' not in name and name.endswith('.py'))
              or name.startswith(('scripts/','assets/fairplay/')) or name=='requirements.txt']
    archive=subprocess.check_output(['git','archive','HEAD',*selected],cwd=ROOT)
    env=dict(os.environ,PYTHONPATH='',PYTHONDONTWRITEBYTECODE='1')
    with tempfile.TemporaryDirectory() as folder:
        sparse=Path(folder)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            bundle.extractall(sparse,filter='data')
        assert not (sparse/'economy_analytics.json').exists()
        outputs=[]
        for cwd in (ROOT,sparse):
            result=subprocess.run([sys.executable,'-c',CHILD],cwd=cwd,env=env,
                                  capture_output=True,text=True,timeout=150,check=True)
            outputs.append(json.loads(result.stdout))
        assert outputs[0]==outputs[1], 'Sparse source changed objective game/decision evidence'
    print(f'Compute-only source parity passed: {len(selected)} files; '
          'real Stockfish metrics, reference data and game serialization match.')


if __name__=='__main__':main()
