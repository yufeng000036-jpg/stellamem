"""Manual LM Studio acceptance. Uses synthetic prompts and a temporary model configuration."""

import json
import tempfile
import uuid
from pathlib import Path

from evaluate import evaluate
from jarvis import chat, error_message, initialize


def run():
    print("先在 LM Studio 加载本地模型并启动本地服务器。此项只使用虚构题，不访问个人记忆。")
    if (
        input("确认此端口仅使用本机推理，且同意运行 50 题？输入 本地验收 继续：").strip()
        != "本地验收"
    ):
        print("已取消")
        return
    model = input("已加载的模型 ID：").strip()
    port = int(input("端口（回车为 1234）：").strip() or "1234")
    if not model or not 1 <= port <= 65535:
        raise ValueError("模型配置无效")
    with tempfile.TemporaryDirectory(prefix="Jarvis 模型测试 ") as temp:
        h = Path(temp).resolve()
        initialize(h)
        (h / "model.json").write_text(json.dumps(dict(port=port, model=model)), "utf-8")
        history = []
        first = chat(h, "本轮用于测试的虚构代号是青竹七号。请回答收到。", history)
        second = chat(h, "刚才告诉你的代号是什么？请只回复代号。", history)
        print(first, second, sep="\n")
    results = evaluate(model, port)
    positive = [r for r in results if r["gold_id"]]
    negative = [r for r in results if not r["gold_id"]]
    summary = {
        "model": model,
        "port": port,
        "synthetic_multiturn_pass": "青竹七号" in second,
        "正例数": len(positive),
        "召回前五": sum(r["recall"] for r in positive),
        "选证正确": sum(r["selection_correct"] for r in positive),
        "最终正确": sum(r["correct"] for r in positive),
        "负例数": len(negative),
        "正确拒答": sum(r["abstained"] for r in negative),
        "有引用数": sum(bool(r["selected"]) for r in results),
        "有效引用数": sum(r["citation_valid"] is True for r in results),
    }
    path = Path.home() / ("Jarvis-本机结果-" + uuid.uuid4().hex[:12] + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(dict(summary=summary, results=results), f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2), "结果文件", path, sep="\n")


if __name__ == "__main__":
    try:
        run()
    except (KeyboardInterrupt, EOFError):
        print("已取消")
    except Exception as exc:
        print("验收中止：" + error_message(exc))
        raise SystemExit(1)
