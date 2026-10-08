"""Read-only syntax validation of tracked-style JSON/YAML data."""
import json
from pathlib import Path
import yaml

root=Path(__file__).resolve().parents[1]
counts={'JSON':0,'YAML':0}
for path in sorted(root.rglob('*')):
    if not path.is_file() or any(part in {'.git','.venv','venv','__pycache__'} for part in path.relative_to(root).parts):continue
    if path.suffix=='.json':
        with path.open(encoding='utf-8') as stream:json.load(stream)
        counts['JSON']+=1
    elif path.suffix in {'.yml','.yaml'}:
        with path.open(encoding='utf-8') as stream:yaml.safe_load(stream)
        counts['YAML']+=1
print('Repository data syntax passed:',counts)
