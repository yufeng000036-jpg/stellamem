"""隐私与加密相关测试：识别、脱敏、加密往返。

数字全部是合成样本，不含任何真实个人信息。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import pii_audit  # noqa: E402

WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
CHECK = "10X98765432"


def make_id(prefix="110101", birth="19900307", seq="123"):
    """生成一个校验位合法的合成身份证号（仅用于测试）。"""
    body = f"{prefix}{birth}{seq}"
    total = sum(int(body[i]) * WEIGHTS[i] for i in range(17))
    return body + CHECK[total % 11]


class DetectionTest(unittest.TestCase):
    def test_phone_contiguous_and_spaced(self):
        text = "电话 13800138000，备用 138 0013 8000"
        self.assertEqual(pii_audit.scan_text(text)["phone"], 2)

    def test_valid_id_detected(self):
        idn = make_id()
        self.assertTrue(pii_audit.id_checksum_ok(idn))
        self.assertEqual(pii_audit.scan_text(f"身份证 {idn}")["idcard"], 1)

    def test_random_18_digit_run_is_rejected(self):
        # 校验位不对的 18 位数字不能算身份证
        self.assertFalse(pii_audit.id_checksum_ok("110101199003071234"))
        self.assertEqual(pii_audit.scan_text("编号 110101199003071234")["idcard"], 0)

    def test_hash_fragment_is_not_a_phone(self):
        # 40 位十六进制里包含的 11 位数字不能当手机号
        digest = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
        self.assertEqual(pii_audit.scan_text(digest)["phone"], 0)

    def test_disability_requires_id_plus_category_and_level(self):
        idn = make_id()
        self.assertTrue(pii_audit.disability_ok(idn + "42"))
        self.assertFalse(pii_audit.disability_ok(idn + "99"))
        self.assertEqual(pii_audit.scan_text(f"残疾证 {idn}42")["disability"], 1)

    def test_secret_patterns(self):
        text = "key=sk-abcdefghijklmnopqrstuvwx token ghp_" + "a" * 36
        self.assertEqual(pii_audit.scan_text(text)["secret"], 2)


class RedactionTest(unittest.TestCase):
    def test_placeholders_and_preservation(self):
        sys.path.insert(0, str(ROOT / "benchmark"))
        import redact_daily

        idn = make_id()
        original = f"联系人电话 13800138000，证件 {idn}42。其余文字保持原样。"
        redacted, stats = redact_daily.redact(original)
        self.assertIn("[REDACTED:PHONE]", redacted)
        self.assertIn("[REDACTED:ID]", redacted)
        self.assertNotIn("13800138000", redacted)
        self.assertNotIn(idn, redacted)
        self.assertIn("其余文字保持原样", redacted)
        self.assertEqual(stats["phone"], 1)
        self.assertEqual(stats["disability"], 1)

    def test_redaction_is_idempotent(self):
        sys.path.insert(0, str(ROOT / "benchmark"))
        import redact_daily

        once, _ = redact_daily.redact("电话 13800138000")
        twice, stats = redact_daily.redact(once)
        self.assertEqual(once, twice)
        self.assertEqual(sum(stats.values()), 0)


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_vault(self, *args, expect=0):
        result = subprocess.run(["node", str(ROOT / "tools" / "vault.mjs"), *args],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def test_roundtrip_and_wrong_key(self):
        plain = self.tmp / "plain.txt"
        plain.write_text("合成内容 12345", encoding="utf-8")
        key = self.tmp / "k.key"
        self.run_vault("keygen", "--key", str(key))
        enc = self.tmp / "p.enc"
        out = json.loads(self.run_vault("seal", "--key", str(key), "--in", str(plain),
                                        "--out", str(enc)).stdout)
        self.assertEqual(out["source_sha256"], pii_audit.hashlib.sha256(plain.read_bytes()).hexdigest())
        back = self.tmp / "back.txt"
        info = json.loads(self.run_vault("unseal", "--key", str(key), "--in", str(enc),
                                         "--out", str(back)).stdout)
        self.assertTrue(info["matches"])
        self.assertEqual(back.read_text(encoding="utf-8"), plain.read_text(encoding="utf-8"))
        wrong = self.tmp / "w.key"
        self.run_vault("keygen", "--key", str(wrong))
        self.run_vault("unseal", "--key", str(wrong), "--in", str(enc),
                       "--out", str(self.tmp / "x.txt"), expect=4)
        self.assertFalse((self.tmp / "x.txt").exists())


if __name__ == "__main__":
    unittest.main()
