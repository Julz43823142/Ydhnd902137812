import gzip
import json
import pytest
from fairplay_evidence_payload import SCHEMA, pack, owner_json


def bundle():
    rows = [{"decisions": [{"ply": 1}], "identity": "a"},
            {"decisions": [{"ply": 2}, {"ply": 3}], "identity": "b"}]
    chunk = pack({"schema": SCHEMA, "games": rows})
    import hashlib
    manifest = {"schema": SCHEMA, "fully_reviewed_games": 2,
                "position_records": 3,
                "files": [{"name": "fairplay-part-001.json.gz",
                           "sha256": hashlib.sha256(chunk).hexdigest()}]}
    return (("fairplay-manifest.json.gz", pack(manifest)),
            ("fairplay-part-001.json.gz", chunk))


def test_owner_json_one_complete_file():
    name, data = owner_json(bundle())
    assert name == "fairplay-owner-evidence.json.gz"
    decoded = json.loads(gzip.decompress(data))
    assert len(decoded["games"]) == 2
    assert decoded["position_records"] == 3


def test_owner_json_rejects_tampering():
    files = list(bundle())
    files[1] = (files[1][0], b"corrupted")
    with pytest.raises(ValueError):
        owner_json(files)


def test_owner_json_rejects_oversize_without_partial_output():
    with pytest.raises(ValueError, match="upload limit"):
        owner_json(bundle(), max_bytes=10)
