"""遗忘曲线排序测试（第一步：只影响排序，不动数据）。"""

import importlib.util
import sys
from pathlib import Path

DAY = 86_400_000
NOW = 1_800_000_000_000  # 固定时间点，测试可复现


def load_rank():
    """以文件路径方式载入 compiler/rank.py（避免依赖包结构）。"""
    p = Path(__file__).resolve().parents[1] / "compiler" / "rank.py"
    spec = importlib.util.spec_from_file_location("stellamem_rank_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rank = load_rank()


# ---------------------------------------------------------------- 基础


def test_identity_never_decays():
    r = {"kind": "identity", "updated_at": NOW - 3650 * DAY}
    assert rank.retention(r, NOW) == 1.0


def test_user_core_never_decays():
    r = {"kind": "user-core", "updated_at": NOW - 3650 * DAY}
    assert rank.retention(r, NOW) == 1.0


def test_preference_half_life_90_days():
    r = {"kind": "preference", "updated_at": NOW - 90 * DAY}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-6


def test_project_half_life_30_days():
    r = {"kind": "project", "updated_at": NOW - 30 * DAY}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-6


def test_fact_half_life_3_days():
    r = {"kind": "fact", "updated_at": NOW - 3 * DAY}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-6


def test_unknown_kind_uses_default_30():
    r = {"kind": "whatever", "updated_at": NOW - 30 * DAY}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-6


def test_no_kind_uses_default():
    r = {"updated_at": NOW - 30 * DAY}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-6


# ---------------------------------------------------------------- 边界


def test_fresh_record_full_retention():
    r = {"kind": "fact", "updated_at": NOW}
    assert rank.retention(r, NOW) == 1.0


def test_missing_timestamp_is_not_penalized():
    r = {"kind": "fact"}
    assert rank.retention(r, NOW) == 1.0


def test_future_timestamp_clamped_to_now():
    r = {"kind": "fact", "updated_at": NOW + 10 * DAY}
    assert rank.retention(r, NOW) == 1.0


def test_retention_never_negative():
    r = {"kind": "fact", "updated_at": NOW - 10000 * DAY}
    assert rank.retention(r, NOW) >= 0.0


# ---------------------------------------------------------------- 时间解析


def test_accepts_seconds_timestamp():
    r = {"kind": "fact", "updated_at": (NOW - 3 * DAY) // 1000}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-3


def test_accepts_iso_string():
    from datetime import datetime, timezone

    dt = datetime.fromtimestamp((NOW - 30 * DAY) / 1000, tz=timezone.utc)
    r = {"kind": "project", "updated_at": dt.isoformat()}
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-3


def test_prefers_last_at_over_created_at():
    r = {
        "kind": "project",
        "created_at": NOW - 300 * DAY,
        "last_at": NOW - 30 * DAY,
    }
    assert abs(rank.retention(r, NOW) - 0.5) < 1e-6


# ---------------------------------------------------------------- 排序


def test_ranking_reorders_but_keeps_all_items():
    old_high = ({"kind": "fact", "id": "old", "updated_at": NOW - 30 * DAY}, 0.9)
    new_low = ({"kind": "fact", "id": "new", "updated_at": NOW}, 0.6)
    out = rank.rank([old_high, new_low], NOW)
    assert len(out) == 2  # 一条都不丢
    assert out[0][0]["id"] == "new"  # 新的分低的排到了前面
    assert out[1][0]["id"] == "old"


def test_identity_keeps_rank_despite_age():
    old_identity = ({"kind": "identity", "id": "id", "updated_at": NOW - 3650 * DAY}, 0.9)
    fresh_fact = ({"kind": "fact", "id": "fact", "updated_at": NOW}, 0.85)
    out = rank.rank([old_identity, fresh_fact], NOW)
    assert out[0][0]["id"] == "id"


def test_rank_does_not_mutate_input():
    rec = {"kind": "fact", "id": "x", "updated_at": NOW - 30 * DAY}
    snapshot = dict(rec)
    rank.rank([(rec, 1.0)], NOW)
    assert rec == snapshot


def test_rank_handles_bad_base_score():
    out = rank.rank([({"kind": "fact", "id": "a", "updated_at": NOW}, "oops")], NOW)
    assert out[0][1] == 0.0


def test_explain_reports_fields():
    info = rank.explain({"kind": "project", "updated_at": NOW - 30 * DAY}, NOW)
    assert info["kind"] == "project"
    assert info["stability_days"] == 30.0
    assert abs(info["retention"] - 0.5) < 1e-6
