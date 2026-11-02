"""Regression tests for match-scoped secret scan exemptions; generated fixtures only."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "repository_secret_scan", Path(__file__).resolve().parents[1] / "tools/secret_scan.py"
)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class ScannerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.key = "sk-" + "a9B2c8D4e7F6g5H3j1K0m9N8p7Q6r5S4"

    def test_detects_generated_credential(self):
        self.assertEqual(scanner.scan_text(self.key), [1])

    def test_markers_never_suppress_a_match(self):
        for marker in (
            "synthetic",
            "fictional",
            "gitleaks:allow",
            "secret-scan: synthetic",
            "<YOUR_",
        ):
            with self.subTest(marker=marker):
                self.assertEqual(scanner.scan_text(marker + " " + self.key), [1])

    def test_fixture_does_not_hide_other_credential(self):
        self.assertEqual(scanner.scan_text(scanner.HISTORY_ALLOWLIST[-1] + " " + self.key), [1])

    def test_fixture_prefix_does_not_hide_longer_credential(self):
        self.assertEqual(scanner.scan_text(scanner.HISTORY_ALLOWLIST[-1] + "EXTRA"), [1])

    def test_exact_historical_fixtures_only(self):
        for fixture in scanner.HISTORY_ALLOWLIST:
            self.assertEqual(scanner.scan_text(fixture), [])

    def test_private_key_marker_cannot_hide_other_match(self):
        block = "-----BEGIN " + "PRIVATE KEY-----\\nFAKEBODYLINE\\n-----END PRIVATE KEY-----"
        self.assertEqual(scanner.scan_text(block), [])
        self.assertEqual(scanner.scan_text(block + " " + self.key), [1])

    def test_fake_body_substring_cannot_exempt_unknown_private_key(self):
        block = "-----BEGIN " + "PRIVATE KEY-----\\nFAKEBODYLINEEXTRA\\n-----END PRIVATE KEY-----"
        self.assertEqual(scanner.scan_text(block), [1])

    def test_safe_text_and_line_numbers(self):
        self.assertEqual(scanner.scan_text("ordinary text\n" + self.key), [2])
