"""Reading-side contract for recall results: structured evidence with
stable claim ids, a per-claim note slot, and the reading scaffold.

Stage-2's citation bottleneck is reader-side volume: the share of
projected claims the reader actually cites fell to 10.0% even as the
absolute count rose (286 -> 414), because the recall format never asks
the reader to work claim by claim. The structured half of the fix
lives here:

- build_evidence(items): the recall items as citable evidence entries
  {"claim_id", "kind", "value", "occurred_at", "note": ""} — the note
  slot is the Chain-of-Note position a reader fills before answering.
- READING_SCAFFOLD: the instruction that goes with the evidence. The
  identical text is exported by the JS projection layer
  (adapters/openclaw/index.js, READING_SCAFFOLD) and appended by the
  OpenClaw host hook; tests/test_citation_check.py guards the two
  copies against drift.

compiler/citation_check.py verifies, deterministically, that the
citations in an answer produced under this contract are real and
supported.
"""

from __future__ import annotations

READING_SCAFFOLD = (
    "For each memory line you rely on, first write a one-line note tying it to the question, "
    "then answer and cite the line id like [c-...] for every fact you use. "
    "If no line supports an answer, say you do not know instead of guessing."
)


def build_evidence(items) -> list[dict]:
    """Citable evidence entries for recall items.

    Only items carrying a stable record id become evidence; injected
    extras without one (preference cards / patterns, which are
    summaries rather than citable claims) are skipped. Input order is
    preserved — it is the recall ranking.

    Recall item ids are composite ("<claim_id>:<revision_id>"); the
    citable identity is the bare claim id — the same id the JS
    projection prints and citation_check's id pattern matches — so the
    revision suffix is stripped here.
    """
    out: list[dict] = []
    for rec in items or []:
        claim_id = rec.get("id")
        if not claim_id:
            continue
        claim_id = str(claim_id).split(":", 1)[0]
        out.append(
            {
                "claim_id": claim_id,
                "kind": rec.get("kind"),
                "value": rec.get("value"),
                "occurred_at": rec.get("occurred_at"),
                # 读者判读有效性的依据：没有这两个字段时，一条已过
                # valid_to 的 active 记录与现行事实无从区分。
                "valid_to": rec.get("valid_to"),
                "status": rec.get("status"),
                "note": "",
            }
        )
    return out
