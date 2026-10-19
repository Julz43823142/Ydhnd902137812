import gzip
import hashlib
import json
import unittest
from fairplay_evidence_payload import SCHEMA, pack, owner_json


class OwnerSingleFileTests(unittest.TestCase):
    def make_bundle(self):
        games = [{"decisions": [{"ply": 1}], "identity": "a"},
                 {"decisions": [{"ply": 2}, {"ply": 3}], "identity": "b"}]
        blob = pack({"schema": SCHEMA, "games": games})
        name = "fairplay-part-001.json.gz"
        manifest = {"schema": SCHEMA, "fully_reviewed_games": 2,
                    "position_records": 3,
                    "files": [{"name": name, "sha256": hashlib.sha256(blob).hexdigest()}]}
        return (("fairplay-manifest.json.gz", pack(manifest)), (name, blob))

    def test_complete_single_json(self):
        name, data = owner_json(self.make_bundle())
        self.assertEqual(name, "fairplay-owner-evidence.json.gz")
        payload = json.loads(gzip.decompress(data))
        self.assertEqual(len(payload["games"]), 2)
        self.assertEqual(payload["position_records"], 3)

    def test_corruption_rejected(self):
        files = list(self.make_bundle())
        files[1] = (files[1][0], b"bad")
        with self.assertRaises(ValueError):
            owner_json(files)

    def test_oversize_rejected(self):
        with self.assertRaisesRegex(ValueError, "upload limit"):
            owner_json(self.make_bundle(), max_bytes=10)


if __name__ == "__main__":
    unittest.main()
