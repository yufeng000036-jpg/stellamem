"""Frozen controlled tasks. Each scenario has an expected observable consequence."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import jarvis as j
from storage import load_records, render_records


def run():
    results = []

    def check(label, actual):
        results.append((label, bool(actual)))
        print(("PASS " if actual else "FAIL ") + label)

    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp).resolve()
        home = base / "lab"
        j.initialize(home)
        history = []

        def call(line, yes=True):
            return j.handle(home, line, lambda _: yes, history)

        def restart(line, other=home):
            out = subprocess.run(
                [sys.executable, str(Path(j.__file__)), "--home", str(other)],
                input=line + "\n退出\n",
                capture_output=True,
                encoding="utf-8",
                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                timeout=20,
                check=True,
            )
            return out.stdout.split("你 › ")[1].strip()

        check("01 空任务列表", call("待办") == "暂无待办")
        call("添加 写项目摘要", False)
        check("02 拒绝新增后无任务", call("待办") == "暂无待办")
        check("03 新增任务并返回编号", call("添加 写项目摘要") == "已添加 #1 写项目摘要")
        call("添加 整理照片")
        check("04 两个任务独立编号", "#2 待办 整理照片" in call("待办"))
        call("完成 1", False)
        check("05 取消完成后仍待办", "#1 待办 写项目摘要" in call("待办"))
        call("完成 1")
        check("06 重启仍已完成", "#1 完成 写项目摘要" in restart("待办"))
        call("重开 1")
        check(
            "07 重开只改指定事项",
            call("待办").splitlines() == ["#1 待办 写项目摘要", "#2 待办 整理照片"],
        )
        check("08 错误编号拒绝", call("完成 999").startswith("未执行"))
        doc = home / "documents"
        (doc / "会议.md").write_text("周五十点，三楼。", encoding="utf-8-sig")
        (doc / "sub").mkdir()
        (doc / "sub/notes.txt").write_text("图书馆", encoding="utf-8")
        check("09 列出中文文件", "会议.md" in call("文件"))
        check("10 按原文读中文 BOM", call("读 会议.md") == "周五十点，三楼。")
        check("11 读取授权子目录", call("读 sub/notes.txt") == "图书馆")
        check("12 拒绝父目录", call("读 ../workspace.id").startswith("未执行"))
        check("13 拒绝 Windows 绝对路径", call("读 C:/other.txt").startswith("未执行"))
        check(
            "14 拒绝缺失文件后能继续",
            call("读 无.txt").startswith("未执行") and call("读 sub/notes.txt") == "图书馆",
        )
        (doc / "big.txt").write_bytes(b"x" * 65537)
        check("15 文件大小上限", call("读 big.txt").startswith("未执行"))
        (doc / "bad.txt").write_bytes(b"\xff")
        check("16 非 UTF-8 拒读", call("读 bad.txt").startswith("未执行"))
        (doc / "note.cmd").write_text("echo hidden")
        check(
            "17 非授权类型拒读且不列出",
            call("读 note.cmd").startswith("未执行") and "note.cmd" not in call("文件"),
        )
        check("18 缺少模型时仍可列待办", "未配置" in call("你好") and "#2" in call("待办"))
        note = "星桥会议定在周五上午"
        check(
            "19 未确认不产生候选",
            call("记下 " + note, False) == "已取消" and call("待审核") == "无待审核记忆",
        )
        rid = call("记下 " + note).split()[-1]
        check("20 原文进入审核队列", rid.startswith("r-") and note in call("待审核"))
        check("21 未审核原文不可召回", call("查记忆 星桥会议") == "无可核验记忆")
        check(
            "22 取消批准仍不可召回",
            call("批准 " + rid, False) == "已取消" and call("查记忆 星桥会议") == "无可核验记忆",
        )
        approved = call("批准 " + rid).split()
        cid, tx = approved[1], approved[-1]
        answer = restart("查记忆 星桥会议")
        check("23 跨进程召回已审核记忆", note in answer)
        ref = answer.split()[-1]
        check(
            "24 引文可按路径核对原文",
            f"[{cid}]" in answer and (home / "memory" / ref).read_text("utf-8") == note,
        )
        original = home / "memory" / ref
        original.write_text("改写", encoding="utf-8")
        check("25 原文变更拒绝展示", call("查记忆 星桥会议") == "无可核验记忆")
        original.write_text(note, encoding="utf-8")
        call("撤销批准 " + tx)
        check(
            "26 撤销使已批准记忆退出召回并回到待审",
            call("查记忆 星桥会议") == "无可核验记忆" and rid in call("待审核"),
        )
        call("批准 " + rid)
        call("遗忘 " + cid, False)
        check("27 取消遗忘后仍可查", note in call("查记忆 星桥会议"))
        call("遗忘 " + cid)
        check("28 遗忘后重启不再投影", restart("查记忆 星桥会议") == "无可核验记忆")
        new = "星桥会议改到周六下午"
        rid = call("记下 " + new).split()[-1]
        call("批准 " + rid)
        corrected = call("查记忆 星桥会议")
        check("29 更正后只提供新原文", new in corrected and note not in corrected)
        # Inject valid same-topic foreign subjects in actual store files, retaining an owner positive control.
        fact = home / "memory/semantic/facts.md"
        prefix, rows = load_records(fact)
        owner = next(r for r in rows if r["status"] == "active")
        for subject in ("persona", "viewer"):
            clone = dict(
                owner, namespace=subject, claim_id="c-" + subject, value="星桥会议 OTHER_" + subject
            )
            text = clone["value"]
            foreign_ref = "notes/" + hashlib.sha256(subject.encode()).hexdigest()[:32] + ".txt"
            (home / "memory" / foreign_ref).write_bytes(text.encode())
            clone.update(
                evidence=text,
                provenance=dict(
                    owner["provenance"],
                    source_ref=foreign_ref,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                ),
            )
            rows.append(clone)
        fact.write_text(render_records(prefix, rows), encoding="utf-8")
        check(
            "30 其他主体同主题记忆不出现在结果",
            new in call("查记忆 星桥会议") and "OTHER_" not in call("查记忆 星桥会议"),
        )
        check("31 无关查询无支持", call("查记忆 珊瑚邮轮") == "无可核验记忆")
        call("停止")
        check("32 停止跨进程生效", "已停止" in restart("读 sub/notes.txt"))
        call("恢复", False)
        check("33 取消恢复仍停止", "已停止" in call("待办"))
        call("恢复")
        check("34 恢复后任务仍在", "#1 待办" in call("待办"))
        logs = call("日志")
        check("35 操作日志含结果且无原文", "恢复 成功" in logs and new not in logs)
        path = Path(call("备份").removeprefix("备份完成 "))
        check(
            "36 备份可在独立目录恢复任务与记忆",
            "#2 待办" in restart("待办", path) and new in restart("查记忆 星桥会议", path),
        )
        # Fixture validates request/response transport only; no language-model score.
        received = []
        fixture_answer = "添加 自动创建"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/v1/models" else 404)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"fixture"}]}')

            def do_POST(self):
                received.append(
                    (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                )
                body = json.dumps({"choices": [{"message": {"content": fixture_answer}}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            cfg = home / "model.json"
            cfg.write_text(json.dumps(dict(port=server.server_port, model="fixture")))
            try:
                out = call("聊 你好")
                call("聊 接上句")
                check(
                    "37 会话历史经本地兼容接口传递",
                    received[-1][0] == "/v1/chat/completions"
                    and received[-1][1]["messages"][-3]["content"] == "你好",
                )
                check(
                    "38 模型文本不能执行操作",
                    "添加 自动创建" in out and "自动创建" not in call("待办"),
                )
                fixture_answer = corrected.split("[")[1].split("]")[0]
                check("43 本地阅读器选中证据后显示已核对原文", call("问记忆 星桥会议") == corrected)
                fixture_answer = "c-forged"
                check("44 阅读器虚构编号则拒答", call("问记忆 星桥会议") == "没有足够证据回答")
            finally:
                server.shutdown()
                worker.join()
        check(
            "39 模型断开后工具继续可用",
            call("聊 再试").startswith("未执行") and call("读 sub/notes.txt") == "图书馆",
        )
        call("帮我记个待办，检查相机电池")
        check("40 自然短句记录后可重新查询", "#3 待办 检查相机电池" in restart("我的待办有哪些"))
        check("41 自然短句读取授权文件", call("帮我读一下 sub/notes.txt") == "图书馆")
        check("42 自然短句查询原文", new in call("查一下记忆，星桥会议"))
        print("\n跨会话来源演示（全部为虚构样本）\n" + answer + "\n更正后\n" + corrected)
        print("\n冻结检索样本（预期记忆均为更正后的同一条）")
        hits = 0
        for prompt in ("星桥会议", "周六下午", "星桥", "改期后的聚会时间"):
            hit = any(new in line for line in restart("查记忆 " + prompt).splitlines()[:5])
            hits += hit
            print(prompt, "命中" if hit else "未命中")
        print(f"命中@5 {hits}/4；自由生成回答正确率未测。")
    print(f"受控场景 {sum(ok for _, ok in results)}/{len(results)}")
    return all(ok for _, ok in results)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
