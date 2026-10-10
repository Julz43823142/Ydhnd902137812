"""Tests for pinned Leela source, bounded network delivery, and provenance."""
import hashlib
import io
from urllib.error import HTTPError
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_fairplay_lc0 import (
    NETWORK_URL, NETWORK_FALLBACK_URL, NETWORK_SHA256, SOURCE_SHA, MAX_NET_BYTES, download_network, prepare)


class Response(io.BytesIO):
    def geturl(self):
        return NETWORK_URL


class LeelaProvisioningTests(unittest.TestCase):
    def test_pinned_upstream_sha_is_full_length(self):
        self.assertEqual(len(SOURCE_SHA), 40)

    def test_official_403_uses_pinned_mirror_but_rejects_wrong_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/"net.pb.gz"
            calls=[]
            def fetch(url,timeout):
                calls.append(url)
                if url==NETWORK_URL:
                    raise HTTPError(url,403,"Forbidden",{},None)
                return Response(b"not-the-authentic-network"*80)
            with self.assertRaisesRegex(RuntimeError,"SHA-256 mismatch"):
                download_network(output,opener=fetch)
            self.assertEqual(calls,[NETWORK_URL,NETWORK_FALLBACK_URL])
            self.assertFalse(output.exists())

    def test_pinned_network_digest_is_known(self):
        self.assertEqual(NETWORK_SHA256,
            "bc27a6cae8ad36f2b9a80a6ad9dabb0d6fda25b1e7f481a79bc359e14f563406")

    def test_official_download_is_bounded_and_checks_checksum(self):
        payload = b"neural-net-synthetic-fixture" * 80
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "net.pb.gz"
            seen = []
            def fetch(url, timeout):
                seen.append(url)
                return Response(payload)
            got = download_network(output, expected_sha256=digest, opener=fetch)
            self.assertEqual(got, digest)
            self.assertEqual(output.read_bytes(), payload)
            self.assertEqual(seen, [NETWORK_URL])
            # Cached file remains valid; never call network again.
            self.assertEqual(download_network(output, expected_sha256=digest,
                opener=lambda *_args, **_kw: self.fail("must not fetch")), digest)

    def test_bad_digest_never_installs_new_network(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "net.pb.gz"
            with self.assertRaisesRegex(RuntimeError, "mismatch"):
                download_network(output, expected_sha256="0" * 64,
                                 opener=lambda *_args, **_kw: Response(b"x" * 4096))
            self.assertFalse(output.exists())
            self.assertFalse(output.with_name("net.pb.gz.part").exists())

    def test_invalid_digest_cannot_pass_without_download(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "net.pb.gz"
            with self.assertRaises(ValueError):
                download_network(path, expected_sha256="not-a-hash")

    def test_oversized_download_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "net.pb.gz"
            with self.assertRaisesRegex(RuntimeError, "maximum"):
                download_network(output,
                    opener=lambda *_args, **_kw: Response(b"x" * (MAX_NET_BYTES + 1)))
            self.assertFalse(output.exists())

    def test_incompatible_cached_cpu_binary_is_rebuilt_once(self):
        import chess.engine
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            binary=root/"source"/"build"/"release"/"lc0"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n",encoding="utf-8")
            binary.chmod(0o755)
            net=root/"network.pb.gz"
            net.write_bytes(b"synthetic net")
            rebuilt=[]
            def fake_rebuild(location):
                rebuilt.append(location)
                binary.write_text("#!/bin/sh\n",encoding="utf-8")
                binary.chmod(0o755)
                return binary
            with patch("scripts.prepare_fairplay_lc0.smoke",side_effect=[
                    chess.engine.EngineTerminatedError("incompatible CPU"),
                    "Lc0 compatible"]), patch(
                    "scripts.prepare_fairplay_lc0.provision_source",
                    side_effect=fake_rebuild):
                result=prepare(root,provision=True,installed_weights=str(net))
            self.assertTrue(result["ready"])
            self.assertEqual(rebuilt,[root])

    def test_probe_failure_never_exports_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "lc0"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            net = root / "net.pb.gz"
            net.write_bytes(b"synthetic network")
            envfile = root / "github.env"
            with patch("scripts.prepare_fairplay_lc0.smoke",
                       side_effect=RuntimeError("no real model")):
                with self.assertRaises(RuntimeError):
                    prepare(root, installed_binary=str(binary),
                            installed_weights=str(net), github_env=str(envfile))
            self.assertFalse(envfile.exists())


if __name__ == "__main__":
    unittest.main()
