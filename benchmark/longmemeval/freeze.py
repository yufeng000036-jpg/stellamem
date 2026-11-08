"""Freeze public labels by deterministic ID sampling, before running inference.

续跑前核对：verify_frozen_files() 核对冻结文件（worker.py、evaluate.py、
PROTOCOL.md、manifest.json、freeze.py）的哈希与物理 byte_count，两者都对
才放行。哈希抓编码转换/等长篡改，字节数抓未落盘截断文件（size 小于预期
直接拦住）；两者缺一不可。用法：python freeze.py --verify。
"""

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
DATA_SHA = "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c"

# 冻结文件清单（核对逻辑的一部分；冻结内容口径本身——清单与哈希值——不动）。
FROZEN_FILES = ("worker.py", "evaluate.py", "PROTOCOL.md", "manifest.json", "freeze.py")
EVALUATION_LOCK_FILENAME = "evaluation-lock.json"

# 冻结文件的物理字节数期望值（核对逻辑的一部分，不是冻结内容本身）。
# 改动任一冻结文件后，须同步更新这里的期望值并说明原因；否则核对直接拦住。
FROZEN_FILE_BYTE_COUNTS = {
    "worker.py": 11128,
    "evaluate.py": 11862,
    "PROTOCOL.md": 5245,
    "manifest.json": 25455,
    # freeze.py 自身只核对字节数：自哈希无法自举；改动 freeze.py 会改变其
    # 字节数从而触发拦截，除非改动者同步更新期望值，那是有意、可复核的改动。
    # 2026-11-08：__main__ 加 UTF-8 输出重配（Windows 控制台中文会炸，
    # 见 _ensure_utf8_streams），字节数 5627 → 6856。
    "freeze.py": 6856,
}


def verify_frozen_files(directory=None):
    """评测续跑前核对冻结文件：哈希与物理 byte_count 都对才放行。

    对 FROZEN_FILES 里的每个文件：存在、字节数等于 FROZEN_FILE_BYTE_COUNTS、
    sha256 等于 evaluation-lock.json 里的值（freeze.py 自身没有锁内哈希，
    只核对字节数）。返回每文件的 {"sha256": ..., "byte_count": ...}；
    任一不符抛 ValueError，不返回半真半假的结果。
    """
    base = Path(directory) if directory is not None else Path(__file__).resolve().parent
    lock_path = base / EVALUATION_LOCK_FILENAME
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"evaluation lock 不可读：{lock_path}：{exc}") from exc
    checked = {}
    for filename in FROZEN_FILES:
        path = base / filename
        if not path.exists():
            raise ValueError(f"冻结文件缺失：{filename}")
        raw = path.read_bytes()
        byte_count = len(raw)
        expected_size = FROZEN_FILE_BYTE_COUNTS[filename]
        if byte_count != expected_size:
            raise ValueError(
                f"冻结文件字节数不符：{filename}：实际 {byte_count} ≠ 期望 {expected_size}"
                "（疑似未落盘截断文件）"
            )
        actual_sha = digest(raw)
        if filename in lock:
            if actual_sha != lock[filename]:
                raise ValueError(f"冻结文件哈希不符：{filename}：{actual_sha} ≠ {lock[filename]}")
        checked[filename] = {"sha256": actual_sha, "byte_count": byte_count}
    return checked


def digest(data):
    return hashlib.sha256(data).hexdigest()


def category(case):
    return "abstention" if case["question_id"].endswith("_abs") else case["question_type"]


def freeze(path):
    raw = path.read_bytes()
    if digest(raw) != DATA_SHA:
        raise ValueError("dataset hash mismatch")
    groups = defaultdict(list)
    for case in json.loads(raw):
        groups[category(case)].append(case)
    selected = [
        case
        for name in sorted(groups)
        for case in sorted(
            groups[name], key=lambda c: digest(("stellamem-pr15-v1:" + c["question_id"]).encode())
        )[:20]
    ]
    return {
        "dataset": "xiaowu0162/longmemeval-cleaned",
        "revision": REVISION,
        "file": "longmemeval_oracle.json",
        "sha256": DATA_SHA,
        "license": "MIT",
        "selection": "20 per category; SHA256(stellamem-pr15-v1:question_id) ascending",
        "scope": "oracle evidence sessions, NOT LongMemEval_S/M or independent claim labels",
        "counts": dict(Counter(category(c) for c in selected)),
        "cases": [
            {
                "question_id": c["question_id"],
                "category": category(c),
                "record_sha256": digest(json.dumps(c, ensure_ascii=False, sort_keys=True).encode()),
            }
            for c in selected
        ],
    }


def _ensure_utf8_streams():
    """CLI 输出改 UTF-8（best-effort，失败不抛错）。

    Windows 控制台默认编码（cp936/cp1252）下中文报错/输出会炸
    （CI 里 Windows 两档就是这么红的：ValueError 里的中文在
    traceback 里被转义成 \\uXXXX，测试断言不到原文）。脚本直接
    运行时仓库根不在 sys.path，先补上再复用 compiler/cli_output
    的共享 helper；复用失败就地重配，同样 best-effort。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from compiler.cli_output import ensure_utf8_stderr, ensure_utf8_stdout

        ensure_utf8_stdout()
        ensure_utf8_stderr()
        return
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


if __name__ == "__main__":
    _ensure_utf8_streams()
    p = argparse.ArgumentParser()
    p.add_argument(
        "--verify", action="store_true", help="只核对冻结文件（哈希 + 字节数），不生成 manifest"
    )
    p.add_argument(
        "--directory", type=Path, default=None, help="核对目录（默认 freeze.py 所在目录）"
    )
    p.add_argument("dataset", type=Path, nargs="?")
    p.add_argument("output", type=Path, nargs="?")
    a = p.parse_args()
    if a.verify:
        checked = verify_frozen_files(a.directory)
        for filename in FROZEN_FILES:
            info = checked[filename]
            print(f"{filename}: sha256={info['sha256']} byte_count={info['byte_count']}")
        print("frozen files verified: hash + byte_count match")
    else:
        if a.dataset is None or a.output is None:
            p.error("需要 dataset 与 output 两个参数（或用 --verify 只核对）")
        result = json.dumps(freeze(a.dataset), ensure_ascii=False, indent=2) + "\n"
        with a.output.open("x", encoding="utf-8") as out:
            out.write(result)
