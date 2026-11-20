"""Score frozen Chinese questions on an isolated instance. Optional real loopback inference."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import jarvis

HERE = Path(__file__).parent
GOLD_HASH = "5b6dd978cac13404f6eb53f79da17af8b594c4e716c67646d24bebbf7927ec5c"


def evaluate(model=None, port=1234):
    cases = (HERE / "cases.tsv").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(cases).hexdigest() == GOLD_HASH, "冻结集校验失败"
    rows = [line.split("\t") for line in cases.decode().splitlines() if not line.startswith("#")]
    with tempfile.TemporaryDirectory(prefix="Jarvis 中文 验收 ") as temp:
        home = Path(temp).resolve()
        jarvis.initialize(home)
        if model:
            (home / "model.json").write_text(json.dumps(dict(model=model, port=port)), "utf-8")

        def command(text):
            return jarvis.handle(home, text, lambda _: True, [])

        def add(text):
            revision = command("记下 " + text).split()[-1]
            approved = command("批准 " + revision)
            assert approved.startswith("已批准 "), approved
            return approved.split()[1]

        old = add("星桥会议原定周五上午十点。")
        assert "停止召回" in command("遗忘 " + old)
        command("记下 助手推荐耳机但本人未确认购买。")
        ids = {row[0]: add(row[0]) for row in rows if row[0] != "?"}
        # A distinct interpreter reads the approved store before the scored questions.
        warm = subprocess.run(
            [sys.executable, str(Path(jarvis.__file__)), "--home", str(home)],
            input="查记忆 星桥会议\n退出\n",
            capture_output=True,
            encoding="utf-8",
            check=True,
            timeout=20,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
        )
        assert ids[rows[0][0]] in warm.stdout
        report = []
        for gold, *questions in rows:
            for question in questions:
                recalled = command("查记忆 " + question).splitlines()[:5]
                target = ids.get(gold)
                hit = any(f"[{target}]" in line for line in recalled) if target else None
                answer = command("问记忆 " + question) if model else None
                refused = answer == "没有足够证据回答" if model else None
                correct = (
                    (
                        any(
                            answer == line and line.startswith(gold + " [" + target + "]")
                            for line in recalled
                        )
                        if target
                        else refused
                    )
                    if model
                    else None
                )
                selected = next(
                    (key for key in ids.values() if answer and f"[{key}]" in answer), None
                )
                report.append(
                    dict(
                        question=question,
                        gold=gold,
                        gold_id=target,
                        top5=recalled,
                        recall=hit,
                        answer=answer,
                        selected=selected,
                        selection_correct=(selected == target if target and model else None),
                        correct=correct,
                        abstained=refused,
                        citation_valid=(answer in recalled if selected else None),
                    )
                )
        return report


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--local-model", help="只填已在本机 LM Studio 加载、使用本地推理的模型 ID")
    p.add_argument("--port", type=int, default=1234)
    p.add_argument("--out", type=Path, required=True, help="新建结果文件")
    args = p.parse_args()
    results = evaluate(args.local_model, args.port)
    with args.out.open("x", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    pos = [r for r in results if r["gold_id"]]
    neg = [r for r in results if not r["gold_id"]]
    print("召回@5", sum(r["recall"] for r in pos), "/", len(pos))
    if args.local_model:
        print(
            "最终正确",
            sum(r["correct"] for r in pos),
            "/",
            len(pos),
            "无证据拒答",
            sum(r["abstained"] for r in neg),
            "/",
            len(neg),
        )
    else:
        print("真实模型选证、最终答案、拒答和引用未测")
