"""预算闸门加固回归测试（compiler/cloud_client.py）。

只用隔离临时目录与虚构数据，不碰生产数据、不调付费接口、不联网。
覆盖：账本损坏失败关闭（不静默清零）、多进程并发记账不丢数、
第二个 Budget 实例立刻看到第一个的记账、名字/凭证路径走配置。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "compiler"))

from cloud_client import (  # noqa: E402
    Budget,
    BudgetExceeded,
    BudgetLedgerError,
    CloudUnavailable,
    load_key,
    redact,
    restore,
)

WORKER_SCRIPT = """\
import sys
from pathlib import Path

sys.path.insert(0, r"{compiler_dir}")
from cloud_client import Budget

ledger = Path(sys.argv[1])
count = int(sys.argv[2])
cost = float(sys.argv[3])
budget = Budget(ledger, monthly_cny=100.0)
for _ in range(count):
    budget.charge(cost)
"""


class CorruptLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget.json"

    def test_garbage_json_raises_instead_of_resetting(self):
        self.path.write_text("{not json at all", encoding="utf-8")
        with self.assertRaises(BudgetLedgerError):
            Budget(self.path, monthly_cny=50.0)

    def test_truncated_json_raises(self):
        self.path.write_text('{"months": {"2026-11": 12.5', encoding="utf-8")
        with self.assertRaises(BudgetLedgerError):
            Budget(self.path, monthly_cny=50.0)

    def test_non_object_json_raises(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(BudgetLedgerError):
            Budget(self.path, monthly_cny=50.0)

    def test_malformed_months_section_raises(self):
        self.path.write_text(
            json.dumps({"months": {"2026-11": "twelve"}, "total_cny": 0.0, "calls": 0}),
            encoding="utf-8",
        )
        with self.assertRaises(BudgetLedgerError):
            Budget(self.path, monthly_cny=50.0)

    def test_malformed_totals_raise(self):
        self.path.write_text(
            json.dumps({"months": {}, "total_cny": "lots", "calls": 0}),
            encoding="utf-8",
        )
        with self.assertRaises(BudgetLedgerError):
            Budget(self.path, monthly_cny=50.0)

    def test_corrupt_ledger_breaks_spent_and_check_too(self):
        budget = Budget(self.path, monthly_cny=50.0)
        budget.charge(1.0)
        self.path.write_text("corrupted", encoding="utf-8")
        with self.assertRaises(BudgetLedgerError):
            budget.spent()
        with self.assertRaises(BudgetLedgerError):
            budget.check(1.0)
        with self.assertRaises(BudgetLedgerError):
            budget.charge(1.0)

    def test_missing_ledger_starts_empty(self):
        budget = Budget(self.path, monthly_cny=50.0)
        self.assertEqual(budget.spent(), 0.0)
        self.assertEqual(budget.remaining(), 50.0)

    def test_valid_ledger_still_loads(self):
        self.path.write_text(
            json.dumps({"months": {"2099-01": 3.5}, "total_cny": 3.5, "calls": 2}),
            encoding="utf-8",
        )
        budget = Budget(self.path, monthly_cny=50.0)
        self.assertEqual(budget.state["calls"], 2)
        self.assertEqual(budget.state["total_cny"], 3.5)


class CrossInstanceVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "budget.json"

    def test_second_instance_sees_first_charges(self):
        first = Budget(self.path, monthly_cny=50.0)
        first.charge(0.5)
        first.charge(0.25)
        second = Budget(self.path, monthly_cny=50.0)
        self.assertEqual(second.spent(), 0.75)
        self.assertEqual(second.state["calls"], 2)

    def test_preexisting_instance_sees_new_charges_via_spent(self):
        first = Budget(self.path, monthly_cny=50.0)
        second = Budget(self.path, monthly_cny=50.0)
        first.charge(2.0)
        self.assertEqual(second.spent(), 2.0)

    def test_check_sees_other_instance_charges(self):
        first = Budget(self.path, monthly_cny=1.0)
        second = Budget(self.path, monthly_cny=1.0)
        first.charge(0.9)
        with self.assertRaises(BudgetExceeded):
            second.check(0.2)
        second.check(0.05)

    def test_charge_records_actual_cost_even_past_limit(self):
        # 钱已经花了就必须记下来，丢账比超支更坏。
        budget = Budget(self.path, monthly_cny=1.0)
        budget.charge(2.0)
        self.assertEqual(budget.spent(), 2.0)
        self.assertEqual(budget.state["calls"], 1)

    def test_no_staging_files_left_behind(self):
        budget = Budget(self.path, monthly_cny=50.0)
        budget.charge(0.1)
        leftovers = [p for p in Path(self.tmp.name).iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])


class ConcurrentChargeTests(unittest.TestCase):
    def test_two_processes_charging_concurrently_lose_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget.json"
            worker = Path(tmp) / "_budget_worker.py"
            worker.write_text(
                WORKER_SCRIPT.format(compiler_dir=str(REPO / "compiler")),
                encoding="utf-8",
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, str(worker), str(ledger), "10", "0.01"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(4)
            ]
            for proc in processes:
                _, err = proc.communicate(timeout=120)
                self.assertEqual(proc.returncode, 0, err)
            state = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(state["calls"], 40)
            self.assertAlmostEqual(state["total_cny"], 0.4, places=6)
            self.assertAlmostEqual(next(iter(state["months"].values())), 0.4, places=6)


class RedactConfigTests(unittest.TestCase):
    def test_redact_defaults_to_no_names(self):
        # 名字清单默认是空的：代码里不写死任何真名。
        text, mapping = redact("虚构人名甲你好，电话 13800138000")
        self.assertIn("虚构人名甲", text)
        self.assertNotIn("13800138000", text)
        self.assertEqual([v for v in mapping.values()], ["13800138000"])

    def test_redact_with_configured_names_still_works(self):
        text, mapping = redact("虚构人名甲你好", names=["虚构人名甲"])
        self.assertNotIn("虚构人名甲", text)
        self.assertIn("虚构人名甲", mapping.values())
        self.assertEqual(restore(text, mapping), "虚构人名甲你好")


class LoadKeyConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _clean_env(self):
        ctx = mock.patch.dict(os.environ, {}, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)
        os.environ.pop("STELLAMEM_DEEPSEEK_KEY_FILE", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)

    def test_no_credential_without_config_or_env(self):
        self._clean_env()
        with self.assertRaises(CloudUnavailable) as caught:
            load_key()
        # 报错里不许出现写死的本机路径。
        self.assertNotIn("E:/", str(caught.exception))
        self.assertNotIn("E:\\", str(caught.exception))

    def test_env_key_used_when_nothing_configured(self):
        self._clean_env()
        os.environ["DEEPSEEK_API_KEY"] = "sk-fictional-test-key-0001"
        self.assertEqual(load_key(), "sk-fictional-test-key-0001")

    def test_explicit_key_file_wins(self):
        self._clean_env()
        key_file = Path(self.tmp.name) / "deepseek.key"
        key_file.write_text("sk-fictional-test-key-0002\n", encoding="utf-8")
        os.environ["DEEPSEEK_API_KEY"] = "sk-fictional-test-key-0001"
        self.assertEqual(load_key(key_file), "sk-fictional-test-key-0002")

    def test_key_file_from_env_var(self):
        self._clean_env()
        key_file = Path(self.tmp.name) / "deepseek.key"
        key_file.write_text("sk-fictional-test-key-0003", encoding="utf-8")
        os.environ["STELLAMEM_DEEPSEEK_KEY_FILE"] = str(key_file)
        self.assertEqual(load_key(), "sk-fictional-test-key-0003")


if __name__ == "__main__":
    unittest.main()
