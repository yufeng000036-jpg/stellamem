"""Designer 复盘：hard cases → 技能改进提案（只提议，永不自动生效）。

MemSkill 论文中的 Designer 会自动进化技能库。本适配刻意砍掉自动进化：
Designer 在这里只做两件事——(1) 只读地收集 hard cases（召回失败、记忆错误/
不完整）；(2) 生成版本化的改进提案，状态一律 pending。提案生效的唯一路径
是具名人类批准（approve）后再显式应用（apply）。

红线（写死）：
    - propose() 永不修改 SkillBank；
    - apply() 在提案状态不是 approved、或执行人与批准人不一致时直接拒绝；
    - 本模块不存在 auto_evolve / auto_apply —— 不是缺失，是设计。

CLI：
    designer_review.py review --cases hard_cases.json --out system/skill-proposals.json
    designer_review.py list [--proposals ...]
    designer_review.py show <proposal_id>
    designer_review.py approve <proposal_id> --by <human> --confirm APPLY
    designer_review.py apply <proposal_id> --by <human> [--bank-overlay ...]
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from skills import Skill, SkillBank, SkillGovernanceError

CONFIRM_PHRASE = "APPLY"
HARD_CASE_KINDS = ("recall_failure", "wrong_memory", "incomplete_memory")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(existing: list[dict]) -> str:
    n = len(existing) + 1
    return "sp-%s-%03d" % (datetime.now(timezone.utc).strftime("%Y%m%d"), n)


# ---------------------------------------------------------------------------
# 1. 只读收集：输入不被修改
# ---------------------------------------------------------------------------
def collect_hard_cases(source) -> list[dict]:
    """source: JSON 文件路径或 list[dict]。返回校验后的 hard cases；输入原样不动。"""
    if isinstance(source, (str, Path)):
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
        cases = raw.get("cases", raw) if isinstance(raw, dict) else raw
    else:
        cases = list(source)
    out = []
    for c in cases:
        if not isinstance(c, dict):
            raise ValueError("hard case must be an object")
        kind = c.get("kind", "")
        if kind not in HARD_CASE_KINDS:
            raise ValueError("unknown hard-case kind: %r" % (kind,))
        out.append(
            {
                "id": str(c.get("id", "")),
                "kind": kind,
                "query": str(c.get("query", "")),
                "expected": str(c.get("expected", "")),
                "got": str(c.get("got", "")),
                "notes": str(c.get("notes", "")),
            }
        )
    if not out:
        raise ValueError("no hard cases provided")
    return out


# ---------------------------------------------------------------------------
# 2. 生成提案：只写草稿，状态一律 pending，不碰 SkillBank
# ---------------------------------------------------------------------------
def draft_skill_from_cases(kind: str, cases: list[dict]) -> dict:
    """按 hard-case 类型生成一份技能草稿（启发式模板，人可改）。"""
    case_ids = [c["id"] for c in cases]
    if kind == "recall_failure":
        return {
            "name": "recall_gap_%s" % datetime.now(timezone.utc).strftime("%Y%m%d"),
            "description": "针对召回失败的补充提取规则（草稿，需人工改写）。",
            "when_to_use": "当查询与记忆的字面/语义差距导致漏召回时。",
            "instructions": "（待人工填写：从 %s 总结出的具体提取补救步骤）" % ",".join(case_ids),
            "constraints": ("不得放宽 secret_gate",),
            "triggers": (),
        }
    if kind == "wrong_memory":
        return {
            "name": "correctness_guard_%s" % datetime.now(timezone.utc).strftime("%Y%m%d"),
            "description": "针对错误记忆的复核规则（草稿，需人工改写）。",
            "when_to_use": "当已入库记忆被证实与事实不符时。",
            "instructions": "（待人工填写：从 %s 总结出的复核步骤；走 UPDATE/DELETE 原语）"
            % ",".join(case_ids),
            "constraints": ("走 quarantine_not_delete，不得物理删除",),
            "triggers": (),
        }
    return {
        "name": "completeness_check_%s" % datetime.now(timezone.utc).strftime("%Y%m%d"),
        "description": "针对记忆不完整的补全规则（草稿，需人工改写）。",
        "when_to_use": "当提取结果丢失关键字段（说话人/时间/证据）时。",
        "instructions": "（待人工填写：从 %s 总结出的补全检查清单）" % ",".join(case_ids),
        "constraints": ("不编造缺失字段，缺失则标记 unknown",),
        "triggers": (),
    }


def propose(
    hard_cases: list[dict],
    proposals_path: str | Path,
    analyst: str = "designer",
) -> list[dict]:
    """为每种 hard-case 类型生成一份 pending 提案并追加到提案文件。

    只写草稿：不修改 SkillBank，不改变任何 skill 状态。
    """
    proposals_path = Path(proposals_path)
    existing: list[dict] = []
    if proposals_path.exists():
        try:
            existing = json.loads(proposals_path.read_text(encoding="utf-8")).get("proposals", [])
        except ValueError:
            existing = []
    by_kind: dict[str, list[dict]] = {}
    for c in hard_cases:
        by_kind.setdefault(c["kind"], []).append(c)
    new_proposals = []
    for kind, cases in sorted(by_kind.items()):
        draft = draft_skill_from_cases(kind, cases)
        proposal = {
            "id": _new_id(existing + new_proposals),
            "type": "new_skill",
            "target": None,
            "skill": draft,
            "rationale": "designer review of %d hard case(s) of kind %s" % (len(cases), kind),
            "evidence_case_ids": [c["id"] for c in cases],
            "analyst": analyst,
            "status": "pending",
            "version": 1,
            "created_at": _now(),
            "approved_by": None,
            "approved_at": None,
            "applied_at": None,
        }
        new_proposals.append(proposal)
    all_proposals = existing + new_proposals
    payload = {"updated_at": _now(), "proposals": all_proposals}
    tmp = proposals_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(proposals_path)
    return new_proposals


def _load_proposals(proposals_path: str | Path) -> tuple[Path, list[dict]]:
    proposals_path = Path(proposals_path)
    if not proposals_path.exists():
        return proposals_path, []
    data = json.loads(proposals_path.read_text(encoding="utf-8"))
    return proposals_path, data.get("proposals", [])


def _save_proposals(proposals_path: Path, proposals: list[dict]) -> None:
    payload = {"updated_at": _now(), "proposals": proposals}
    tmp = proposals_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(proposals_path)


def _find(proposals: list[dict], proposal_id: str) -> dict:
    for p in proposals:
        if p["id"] == proposal_id:
            return p
    raise ValueError("no proposal matches id: " + proposal_id)


# ---------------------------------------------------------------------------
# 3. 人工批准：必须具名 + 确认短语
# ---------------------------------------------------------------------------
def approve(proposals_path: str | Path, proposal_id: str, by_human: str, confirm: str = "") -> dict:
    """批准提案。by_human 必须具名；confirm 必须为 APPLY。"""
    human = (by_human or "").strip()
    if not human:
        raise SkillGovernanceError("refused: approval requires a named human (by_human).")
    if confirm != CONFIRM_PHRASE:
        raise SkillGovernanceError(
            "refused: approval requires confirmation phrase %r." % CONFIRM_PHRASE
        )
    path, proposals = _load_proposals(proposals_path)
    p = _find(proposals, proposal_id)
    if p["status"] != "pending":
        raise SkillGovernanceError(
            "refused: proposal %s is %s, only pending proposals can be approved."
            % (proposal_id, p["status"])
        )
    p["status"] = "approved"
    p["approved_by"] = human
    p["approved_at"] = _now()
    _save_proposals(path, proposals)
    return p


def reject(proposals_path: str | Path, proposal_id: str, by_human: str, reason: str = "") -> dict:
    human = (by_human or "").strip()
    if not human:
        raise SkillGovernanceError("refused: rejection requires a named human (by_human).")
    path, proposals = _load_proposals(proposals_path)
    p = _find(proposals, proposal_id)
    if p["status"] != "pending":
        raise SkillGovernanceError("refused: only pending proposals can be rejected.")
    p["status"] = "rejected"
    p["approved_by"] = human
    p["approved_at"] = _now()
    p["rationale"] = p.get("rationale", "") + " [rejected: %s]" % reason
    _save_proposals(path, proposals)
    return p


# ---------------------------------------------------------------------------
# 4. 应用：仅当 approved 且执行人=批准人；否则拒绝（红线）
# ---------------------------------------------------------------------------
def apply(
    proposals_path: str | Path,
    proposal_id: str,
    bank: SkillBank,
    by_human: str,
) -> dict:
    """把已批准的提案应用进 SkillBank。

    红线：提案状态不是 approved、或执行人与批准人不一致，直接拒绝。
    Designer 没有任何路径可以绕过这一步自动生效。
    """
    human = (by_human or "").strip()
    if not human:
        raise SkillGovernanceError("refused: applying a proposal requires a named human.")
    path, proposals = _load_proposals(proposals_path)
    p = _find(proposals, proposal_id)
    if p["status"] != "approved":
        raise SkillGovernanceError(
            "refused: proposal %s is '%s', not approved. The Designer may only propose; "
            "it can never apply." % (proposal_id, p["status"])
        )
    if p.get("approved_by") != human:
        raise SkillGovernanceError(
            "refused: proposal %s was approved by %r, but apply was requested by %r. "
            "Approver and applier must be the same human."
            % (proposal_id, p.get("approved_by"), human)
        )
    skill_dict = copy.deepcopy(p["skill"])
    skill = Skill(
        name=skill_dict["name"],
        version="1.0.0",
        description=skill_dict.get("description", ""),
        when_to_use=skill_dict.get("when_to_use", ""),
        instructions=skill_dict.get("instructions", ""),
        constraints=tuple(skill_dict.get("constraints", ())),
        triggers=tuple(skill_dict.get("triggers", ())),
        source="designer:" + p["id"],
    )
    bank.register(skill, approved_by=human)
    p["status"] = "applied"
    p["applied_at"] = _now()
    _save_proposals(path, proposals)
    return p


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rv = sub.add_parser("review")
    rv.add_argument("--cases", required=True, help="hard cases JSON file")
    rv.add_argument("--proposals", required=True, help="proposals output JSON file")
    rv.add_argument("--analyst", default="designer")
    li = sub.add_parser("list")
    li.add_argument("--proposals", required=True)
    sh = sub.add_parser("show")
    sh.add_argument("proposal_id")
    sh.add_argument("--proposals", required=True)
    ap = sub.add_parser("approve")
    ap.add_argument("proposal_id")
    ap.add_argument("--proposals", required=True)
    ap.add_argument("--by", required=True, dest="by_human")
    ap.add_argument("--confirm", default="")
    ay = sub.add_parser("apply")
    ay.add_argument("proposal_id")
    ay.add_argument("--proposals", required=True)
    ay.add_argument("--by", required=True, dest="by_human")
    ay.add_argument("--bank-overlay", default=None)
    args = parser.parse_args(argv)

    if args.command == "review":
        cases = collect_hard_cases(args.cases)
        made = propose(cases, args.proposals, analyst=args.analyst)
        print(
            json.dumps(
                {"proposed": [p["id"] for p in made], "status": "pending"}, ensure_ascii=False
            )
        )
        return 0
    if args.command == "list":
        _, proposals = _load_proposals(args.proposals)
        print(
            json.dumps(
                [{"id": p["id"], "type": p["type"], "status": p["status"]} for p in proposals],
                ensure_ascii=False,
            )
        )
        return 0
    if args.command == "show":
        _, proposals = _load_proposals(args.proposals)
        print(json.dumps(_find(proposals, args.proposal_id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "approve":
        try:
            p = approve(args.proposals, args.proposal_id, args.by_human, args.confirm)
        except SkillGovernanceError as e:
            print("REFUSED: " + str(e))
            return 2
        print(json.dumps({"id": p["id"], "status": p["status"]}, ensure_ascii=False))
        return 0
    if args.command == "apply":
        try:
            bank = SkillBank(overlay_path=args.bank_overlay)
            p = apply(args.proposals, args.proposal_id, bank, args.by_human)
        except (SkillGovernanceError, ValueError, KeyError) as e:
            print("REFUSED: " + str(e))
            return 2
        print(json.dumps({"id": p["id"], "status": p["status"]}, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
