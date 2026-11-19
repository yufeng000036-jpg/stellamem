"""Isolated, model-free projection and source verification for frozen query sets."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "experiments/jarvis"))
import memory_tools
from claim_normalizer import claim_id, claim_revision_id
from storage import load_records, render_records

SETS = [
    (
        "regression",
        ROOT / "experiments/jarvis/cases.tsv",
        "5b6dd978cac13404f6eb53f79da17af8b594c4e716c67646d24bebbf7927ec5c",
    ),
    (
        "holdout",
        HERE / "heldout.tsv",
        "ad7081130d96e69ca2059bdb0bdaeb7fdf632fb529c39920cdbe30e11d4a737b",
    ),
]
TOPICS = (
    "会议室的投影仪",
    "读书会的签到册",
    "摄影课的灯架",
    "画室的窗帘",
    "采访提纲的封面",
    "剧院的玻璃门",
    "夜校的讲台",
    "笔记本的标签",
    "借书证的卡套",
    "相册的封底",
    "维修铺的椅子",
    "录音练习的谱架",
    "排练厅的幕布",
    "展柜的底座",
    "播客的台本",
    "影院的海报",
)
COLORS = ("浅蓝色", "深灰色", "米白色", "橄榄绿", "暖黄色", "棕褐色", "淡紫色")


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def questions():
    tasks = []
    for name, path, expected in SETS:
        text = path.read_text(encoding="utf-8")
        if digest(text) != expected:
            raise ValueError("frozen inputs changed")
        for line in text.splitlines():
            if line.startswith("#"):
                continue
            gold, *qs = line.split("\t")
            tasks.extend(dict(dataset=name, question=q, gold=gold) for q in qs)
    return tasks


def fixtures(root, notes):
    root.mkdir()
    sample = notes[0]
    receipt = memory_tools.operate(root, "记下", sample)
    memory_tools.operate(root, "批准", receipt.split()[-1])
    path = root / "semantic/facts.md"
    prefix, rows = load_records(path)
    template = rows[0]
    records = {}
    for value in notes:
        ref = "notes/" + digest(value)[:32] + ".txt"
        (root / ref).write_bytes(value.encode())
        rec = dict(
            template,
            value=value,
            predicate=value,
            evidence=value,
            provenance=dict(
                template["provenance"],
                source_ref=ref,
                content_hash=digest(value),
                timestamp="2026-01-01T10:00:00+00:00",
            ),
        )
        rec.update(claim_id=claim_id(rec), revision_id=claim_revision_id(rec))
        records[rec["claim_id"]] = rec
    assert len(records) == len(notes)
    path.write_text(
        render_records(prefix, sorted(records.values(), key=lambda r: digest(r["value"]))), "utf-8"
    )
    return records


def measure(root, records, tasks, adapter, arm):
    config = root / "requests.json"
    config.write_text(json.dumps(tasks), "utf-8")
    diag = root / "diagnostics"
    env = dict(os.environ, STELLAMEM_PROJECT_DIAGNOSTICS_DIR=str(diag))
    env.pop("NODE_OPTIONS", None)
    raw = subprocess.check_output(
        ["node", str(HERE / "project.mjs"), str(adapter), str(root), str(config)],
        env=env,
        encoding="utf-8",
        timeout=360,
    )
    outputs = list(map(json.loads, raw.splitlines()))
    assert len(outputs) == len(tasks)
    rows = []
    for task, result in zip(tasks, outputs):
        ids = []
        for item in result["items"]:
            key = item.get("id")
            if key in records:
                rec = records[key]
                assert item["value"] == rec["value"] and memory_tools.verified(root, rec)
                ids.append(key)
        target = task["gold_id"]
        rank = ids.index(target) + 1 if target in ids else 0
        rows.append(
            dict(
                **task,
                size=len(records),
                arm=arm,
                rank=rank,
                projected=len(ids),
                reason=result.get("reason") or result.get("stage") or "",
                top5=" ".join(ids[:5]),
            )
        )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("new_output", type=Path)
    args = parser.parse_args()
    tasks = questions()
    truths = list(dict.fromkeys(t["gold"] for t in tasks if t["gold"] != "?"))
    assert len(truths) == 21 and len(tasks) == 94
    rows = []
    with tempfile.TemporaryDirectory() as name:
        temp = Path(name)
        base = ROOT / "adapters/openclaw/index.js"
        other = temp / "candidate" / "adapters/openclaw/index.js"
        other.parent.mkdir(parents=True)
        shutil.copy2(base, other)
        shutil.copytree(ROOT / "shared", temp / "candidate/shared")
        (temp / "candidate/package.json").write_text('{"type":"module"}')
        subprocess.run(
            ["git", "apply", str(HERE / "candidate.patch")], cwd=temp / "candidate", check=True
        )
        for size in (21, 500, 2000):
            notes = truths + [
                f"第{i // len(TOPICS) + 1}分馆的{TOPICS[i % len(TOPICS)]}为{COLORS[i % len(COLORS)]}。"
                for i in range(size - len(truths))
            ]
            root = temp / f"store-{size}"
            records = fixtures(root, notes)
            lookup = {r["value"]: k for k, r in records.items()}
            queries = [dict(t, gold_id=lookup.get(t["gold"], "")) for t in tasks]
            for arm, adapter in (("A", base), ("B", other)):
                rows.extend(measure(root, records, queries, adapter, arm))
    with args.new_output.open("x", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
