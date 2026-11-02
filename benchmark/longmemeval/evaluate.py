"""Run frozen official cases without uploading source text or predicted answers."""

import argparse
import concurrent.futures
import json
import re
import socket
import subprocess
import sys
import time
import threading
import urllib.request
from collections import Counter
from pathlib import Path

from freeze import DATA_SHA, digest

HERE = Path(__file__).resolve().parent


def validate_preflight(payload):
    choices = payload.get("choices", [])
    if (
        not choices
        or not isinstance(choices[0].get("message", {}).get("content"), str)
        or not choices[0]["message"]["content"]
    ):
        raise ValueError("health endpoint alone does not prove inference works")
    return payload.get("usage", {})


def normalized(value):
    return re.sub(r"\W+", " ", str(value).casefold()).strip()


def score(case, result):
    answer = result.pop("answer", "")
    result["answer_sha256"] = digest(answer.encode())
    absent = case["question_id"].endswith("_abs")
    good = result.get("error") is None
    result["exact_match"] = good and (
        result.get("abstain") is True
        if absent
        else not result.get("abstain") and normalized(answer) == normalized(case["answer"])
    )
    result["correct_abstention"] = good and result.get("abstain") is True if absent else None
    gold = set(case["answer_session_ids"])
    result["retrieval_recall_at_1"] = (
        len(set(result["retrieved_sessions"][:1]) & gold) / len(gold)
        if gold and not absent
        else None
    )
    result["retrieval_recall_at_5"] = (
        len(set(result["retrieved_sessions"][:5]) & gold) / len(gold)
        if gold and not absent
        else None
    )
    result["citation_gold_session_recall"] = (
        len(set(result["cited_sessions"]) & gold) / len(gold) if gold and not absent else None
    )
    gold_turns = {
        sid + ":" + str(i)
        for sid, turns in zip(case["haystack_session_ids"], case["haystack_sessions"])
        for i, turn in enumerate(turns)
        if turn.get("has_answer")
    }
    result["extracted_evidence_turn_coverage"] = (
        len(set(result["extracted_turns"]) & gold_turns) / len(gold_turns)
        if gold_turns and not absent
        else None
    )
    # Keep only anonymous IDs/hashes; do not publish conversation or prediction text.
    for key in ("extracted_turns", "retrieved_sessions", "cited_sessions"):
        result[key] = [digest(str(x).encode())[:16] for x in result[key]]
    return result


def one(case, args):
    clean = {
        "question": case["question"],
        "question_date": case["question_date"],
        "sessions": [
            {
                "id": sid,
                "date": date,
                "turns": [{"role": t["role"], "content": t["content"]} for t in turns],
            }
            for sid, date, turns in zip(
                case["haystack_session_ids"], case["haystack_dates"], case["haystack_sessions"]
            )
        ],
    }
    results = {}
    for arm in ("main", "candidate", "none", "direct"):
        repo = args.baseline if arm == "main" else args.candidate
        proc = subprocess.run(
            [
                sys.executable,
                str(HERE / "worker.py"),
                "--repo",
                str(repo),
                "--arm",
                arm,
                "--endpoint",
                args.endpoint,
                "--cache",
                str(args.work / "cache" / digest(case["question_id"].encode())),
            ],
            input=json.dumps(clean),
            text=True,
            capture_output=True,
            timeout=4200,
        )
        if proc.returncode:
            raise RuntimeError("worker failed: " + arm + " " + proc.stderr[-800:])
        results[arm] = score(case, json.loads(proc.stdout))
    return {
        "case_id_sha256": digest(case["question_id"].encode()),
        "category": "abstention" if case["question_id"].endswith("_abs") else case["question_type"],
        "arms": results,
    }


def aggregate(rows):
    summary = {}
    for arm in ("main", "candidate", "none", "direct"):
        rs = [r["arms"][arm] for r in rows]

        def mean(key):
            xs = [r[key] for r in rs if r.get(key) is not None]
            return sum(xs) / len(xs) if xs else None

        summary[arm] = {
            "n": len(rs),
            "errors": dict(Counter(r["error"] for r in rs if r["error"])),
            "exact_match": mean("exact_match"),
            "correct_abstention": mean("correct_abstention"),
            "recall_at_1": mean("retrieval_recall_at_1"),
            "recall_at_5": mean("retrieval_recall_at_5"),
            "citation_reference_valid_rate": mean("citation_valid"),
            "extracted_evidence_turn_coverage": mean("extracted_evidence_turn_coverage"),
            "mean_elapsed_seconds": mean("elapsed_seconds"),
            "peak_worker_rss_kib": max(r["peak_rss_kib"] for r in rs),
            "logical_model_calls": sum(r["logical_calls"] for r in rs),
            "physical_model_calls": sum(r["physical_calls"] for r in rs),
            "cache_hits": sum(r["cache_hits"] for r in rs),
        }
    return summary


def main():
    p = argparse.ArgumentParser()
    for name in ("dataset", "baseline", "candidate", "runtime", "model", "work", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    args = p.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    raw = args.dataset.read_bytes()
    if digest(raw) != DATA_SHA:
        raise ValueError("dataset changed")
    manifest = json.loads((HERE / "manifest.json").read_text())
    cases = {c["question_id"]: c for c in json.loads(raw)}
    selected = [cases[c["question_id"]] for c in manifest["cases"]]
    for frozen, case in zip(manifest["cases"], selected):
        if (
            digest(json.dumps(case, ensure_ascii=False, sort_keys=True).encode())
            != frozen["record_sha256"]
        ):
            raise ValueError("case changed")
    for filename in ("worker.py", "evaluate.py", "PROTOCOL.md", "manifest.json"):
        expected = json.loads((HERE / "evaluation-lock.json").read_text())[filename]
        if digest((HERE / filename).read_bytes()) != expected:
            raise ValueError("evaluation lock mismatch: " + filename)
    if (
        digest(args.model.read_bytes())
        != "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
    ):
        raise ValueError("model hash mismatch")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    args.endpoint = f"http://127.0.0.1:{port}"
    log = (args.work / "model.log").open("w")
    server = subprocess.Popen(
        [
            str(args.runtime),
            "--model",
            str(args.model),
            "--alias",
            "stellamem-eval",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ctx-size",
            "16384",
            "--parallel",
            "1",
            "--threads",
            "8",
            "--threads-batch",
            "8",
            "--jinja",
            "--chat-template-kwargs",
            '{"enable_thinking":false}',
            "--no-webui",
        ],
        stdout=log,
        stderr=log,
    )
    rows = []
    memory = {"peak_server_rss_kib": 0}
    sampling = threading.Event()
    sampling.set()

    def sample_memory():
        while sampling.is_set():
            try:
                for line in Path(f"/proc/{server.pid}/status").read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        memory["peak_server_rss_kib"] = max(
                            memory["peak_server_rss_kib"], int(line.split()[1])
                        )
            except OSError:
                pass
            time.sleep(0.2)

    monitor = threading.Thread(target=sample_memory, daemon=True)
    monitor.start()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                with opener.open(args.endpoint + "/health", timeout=2):
                    break
            except Exception:
                if server.poll() is not None:
                    raise RuntimeError("local model failed to start")
                time.sleep(0.25)
        else:
            raise RuntimeError("local model readiness timeout")
        request = urllib.request.Request(
            args.endpoint + "/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "stellamem-eval",
                    "messages": [
                        {"role": "user", "content": 'Return the JSON object {"ready":true}.'}
                    ],
                    "temperature": 0,
                    "max_tokens": 64,
                    "stream": False,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with opener.open(request, timeout=120) as response:
            preflight_usage = validate_preflight(json.load(response))
        if server.poll() is not None:
            raise RuntimeError("model died after inference preflight")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            tasks = [pool.submit(one, case, args) for case in selected]
            for future in concurrent.futures.as_completed(tasks):
                rows.append(future.result())
                if server.poll() is not None:
                    for task in tasks:
                        task.cancel()
                    raise RuntimeError("model process exited during evaluation")
                (args.work / "checkpoint.json").write_text(json.dumps(rows))
                print(json.dumps({"finished": len(rows), "total": len(selected)}), flush=True)
        result = {
            "protocol": "v2",
            "runtime_profile": {"slots": 1, "context_per_slot": 16384, "threads": 8},
            "preflight_usage": preflight_usage,
            "memory": memory,
            "dataset_sha256": DATA_SHA,
            "baseline_sha": subprocess.check_output(
                ["git", "-C", str(args.baseline), "rev-parse", "HEAD"]
            )
            .decode()
            .strip(),
            "candidate_sha": subprocess.check_output(
                ["git", "-C", str(args.candidate), "rev-parse", "HEAD"]
            )
            .decode()
            .strip(),
            "acceptance": "NOT_MET: semantic review, full-distractor and claim labels unavailable",
            "summary": aggregate(rows),
            "by_category": {
                k: aggregate([r for r in rows if r["category"] == k])
                for k in sorted({r["category"] for r in rows})
            },
            "cases": sorted(rows, key=lambda r: r["case_id_sha256"]),
        }
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    except Exception as exc:
        args.out.write_text(
            json.dumps(
                {
                    "protocol": "v2",
                    "status": "INVALID_INFRASTRUCTURE_RUN",
                    "error": type(exc).__name__,
                    "detail": str(exc),
                    "server_exit": server.poll(),
                    "completed_cases": len(rows),
                    "memory": memory,
                },
                indent=2,
            )
            + "\n"
        )
        raise
    finally:
        sampling.clear()
        monitor.join(timeout=2)
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        log.close()


if __name__ == "__main__":
    main()
