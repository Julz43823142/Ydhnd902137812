"""Tests for pinned Leela source, bounded network delivery, and provenance."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_fairplay_lc0 import (
    NETWORK_URL, SOURCE_SHA, MAX_NET_BYTES, download_network, prepare)


class Response(io.BytesIO):
    def geturl(self):
        return NETWORK_URL


class LeelaProvisioningTests(unittest.TestCase):
    def test_pinned_upstream_sha_is_full_length(self):
        self.assertEqual(len(SOURCE_SHA), 40)

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
