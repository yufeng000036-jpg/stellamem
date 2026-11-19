"""Local-only paired study. The index and approved synthetic store live in a fresh temp folder."""

import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "experiments/jarvis"))
import jarvis
import memory_tools as mem

MODEL_HASH = "f93897db57c4385f1cde3f59234bececa234c0f9bafc646dfdaeebe7f65ea84d"
ABSTAIN = "没有足够证据回答"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def post(port, path, body):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=90)
    try:
        conn.request("POST", path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        res = conn.getresponse()
        data = res.read(4_000_001)
        if res.status != 200 or len(data) > 4_000_000:
            raise RuntimeError(f"localhost:{port} status {res.status} or invalid response size")
        return json.loads(data)
    finally:
        conn.close()


def embed(texts, model):
    data = post(8082, "/v1/embeddings", dict(model=model, input=texts, encoding_format="float"))[
        "data"
    ]
    data = sorted(data, key=lambda r: r["index"])
    if [r["index"] for r in data] != list(range(len(texts))):
        raise ValueError("embedding batch index mismatch")
    vectors = []
    for row in data:
        v = row["embedding"]
        if len(v) != 1024 or not all(type(x) in (float, int) and math.isfinite(x) for x in v):
            raise ValueError("invalid vector")
        norm = math.hypot(*v)
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("invalid vector norm")
        vectors.append([x / norm for x in v])
    return vectors


def seed(home, notes):
    ids = {}
    for note in notes:
        root = home / "memory"
        receipt = mem.operate(root, "记下", note["text"])
        approved = mem.operate(root, "批准", receipt.split()[-1])
        cid = approved.split()[1]
        ids[note["id"]] = cid
        if note["status"] == "superseded":
            mem.operate(root, "遗忘", cid)
    return ids


def verified_active(root):
    return {
        r["claim_id"]: r
        for path, records in mem.MemoryStore(root).records.items()
        if path != mem.INBOX
        for r in records
        if r["status"] == "active"
        and mem.subject_of(r) == "user"
        and r.get("scope") == "global"
        and r.get("injection_policy") == "recall"
        and mem.verified(root, r)
    }


def project(home, question, ranks):
    root = home / "memory"
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    with mem.writer_lock(root):
        rows = verified_active(root)
        output = subprocess.run(
            ["node", str(HERE / "project.mjs"), str(root), str(home / "scratch")],
            input=json.dumps(dict(question=question, ranks=ranks)),
            encoding="utf-8",
            capture_output=True,
            check=True,
            timeout=30,
            env=env,
        )
        data = json.loads(output.stdout)
        lines = {}
        for line in data["context"].splitlines()[1:]:
            item = json.loads(line)
            rec = rows.get(item["id"])
            if rec and item.get("value") == rec["value"]:
                cid = rec["claim_id"]
                lines[cid] = f"{rec['value']} [{cid}] 本人原文 {rec['provenance']['source_ref']}"
    return lines, data["records"]


def validate(data, blind):
    notes, questions = data["notes"], data["questions"]
    ids = {n["id"] for n in notes}
    current = {n["id"] for n in notes if n["status"] == "active"}
    if (
        len(ids) != len(notes)
        or len({n["text"] for n in notes}) != len(notes)
        or any(n["status"] not in ("active", "superseded") for n in notes)
    ):
        raise ValueError("duplicate note or invalid status")
    if len({q["id"] for q in questions}) != len(questions) or len(
        {q["text"] for q in questions}
    ) != len(questions):
        raise ValueError("duplicate question")
    if any(q["gold"] is not None and q["gold"] not in current for q in questions):
        raise ValueError("gold must identify an active note or be null")
    if blind and (
        len(notes),
        len(current),
        len(questions),
        sum(q["gold"] is None for q in questions),
    ) != (18, 16, 32, 10):
        raise ValueError("sealed manifest count mismatch")
    return notes, questions


def study(args, sink, data):
    notes, qs = validate(data, args.sealed_original is not None)
    with tempfile.TemporaryDirectory(prefix="Jarvis AB ") as tmp:
        home = Path(tmp).resolve()
        jarvis.initialize(home)
        ids = seed(home, notes)
        root = home / "memory"
        start = time.perf_counter()
        active = verified_active(root)
        if set(active) != {ids[n["id"]] for n in notes if n["status"] == "active"}:
            raise ValueError("active source set differs from manifest")
        # The study store is private to this process, with one unique slot per note.
        # Only send documents to the encoder after the real adapter has cleared all
        # policy/conflict gates. A neutral query need not admit them lexically.
        _, policy = project(home, "", None)
        allowed = {
            r["claim_id"] for r in policy if r.get("reason") == "no-relevance" or r.get("preRank")
        }
        if not set(active) <= allowed:
            raise ValueError(
                "governance preflight withheld at least one note; abort before embedding"
            )
        recs = list(active.values())
        vectors = embed([r["value"] for r in recs], args.embed_model)
        index = list(zip([r["revision_id"] for r in recs], vectors))

        def log(obj):
            sink.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sink.flush()

        log(
            dict(
                type="index",
                items=len(index),
                build_seconds=time.perf_counter() - start,
                sealed_hash=args.sealed_hash,
                manifest_hash=sha(args.manifest),
                embedding_hash=MODEL_HASH,
                embedding_model=args.embed_model,
                reader_model=args.reader_model,
                reader_port=args.reader_port,
            )
        )
        pairs = []
        for i, q in enumerate(qs):
            pair = {}
            t = time.perf_counter()
            query = embed([q["text"]], args.embed_model)[0]
            ranked = sorted(
                index, key=lambda pair: (-sum(a * b for a, b in zip(query, pair[1])), pair[0])
            )
            ranks = [rid for rid, _ in ranked[:8]]
            retrieval = time.perf_counter() - t
            for arm in ("A", "B") if i % 2 == 0 else ("B", "A"):
                t = time.perf_counter()
                lines, trace = project(home, q["text"], None if arm == "A" else ranks)
                duration = time.perf_counter() - t
                offered = list(lines)[:5]
                choices = []
                reader_ids = []

                def reader(_, messages):
                    materials = json.loads(messages[-1]["content"])["材料"]
                    reader_ids.extend(k for k in offered if lines[k] in materials)
                    answer = post(
                        args.reader_port,
                        "/v1/chat/completions",
                        dict(
                            model=args.reader_model,
                            messages=messages,
                            temperature=0,
                            seed=7,
                            max_tokens=384,
                            stream=False,
                        ),
                    )["choices"][0]["message"]["content"]
                    if not isinstance(answer, str) or not answer.strip():
                        raise ValueError("reader returned empty content")
                    choices.append(answer.strip())
                    return answer.strip()

                evidence = "\n".join(lines.values())
                with (
                    patch.object(mem, "operate", return_value=evidence),
                    patch.object(jarvis, "model_reply", reader),
                ):
                    answer = jarvis.ask_memory(home, q["text"])
                selected = next((k for k in offered if lines[k] == answer), None)
                gold = ids.get(q["gold"])
                target = next((row for row in trace if row["claim_id"] == gold), {})
                refused = answer == ABSTAIN
                pair[arm] = dict(
                    type="question",
                    id=q["id"],
                    category=q.get("category"),
                    arm=arm,
                    gold=gold,
                    query_embedding_seconds=retrieval if arm == "B" else 0,
                    projection_seconds=duration,
                    gold_pre_rank=target.get("preRank"),
                    drop_reason=target.get("reason"),
                    top5=offered,
                    in_top5=gold in offered if gold else None,
                    semantic_candidates=ranks if arm == "B" else [],
                    reader_candidates=reader_ids,
                    reader_output=choices,
                    selected=selected,
                    correct=selected == gold if gold else refused,
                    abstained=refused,
                    citation_valid=selected in offered if selected else None,
                    answer=answer,
                    invalid_selection=bool(
                        choices and choices[0] not in reader_ids and choices[0] != "NONE"
                    ),
                )
                log(pair[arm])
            pairs.append(pair)
        for arm in ("A", "B"):
            rows = [p[arm] for p in pairs]
            positive = [r for r in rows if r["gold"]]
            negative = [r for r in rows if not r["gold"]]
            log(
                dict(
                    type="metrics",
                    arm=arm,
                    positive=len(positive),
                    negative=len(negative),
                    recall5=sum(r["in_top5"] for r in positive),
                    final_correct=sum(r["correct"] for r in positive),
                    negative_refusals=sum(r["abstained"] for r in negative),
                    invalid_selections=sum(r["invalid_selection"] for r in rows),
                    valid_citations=sum(r["citation_valid"] is True for r in rows),
                    citations=sum(r["selected"] is not None for r in rows),
                )
            )
        log(
            dict(
                type="paired_delta",
                gains=[p["A"]["id"] for p in pairs if p["B"]["correct"] and not p["A"]["correct"]],
                losses=[p["A"]["id"] for p in pairs if p["A"]["correct"] and not p["B"]["correct"]],
            )
        )
        log(dict(type="complete", pairs=len(qs)))


def main():
    p = argparse.ArgumentParser()
    for name in ("manifest", "gguf", "out"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--sealed-original", type=Path)
    p.add_argument("--sealed-hash")
    p.add_argument("--public-smoke", action="store_true")
    p.add_argument("--embed-model", required=True)
    p.add_argument("--reader-model", required=True)
    p.add_argument("--reader-port", type=int, required=True)
    args = p.parse_args()
    if sha(args.gguf) != MODEL_HASH or args.reader_port == 8082 or not 0 < args.reader_port < 65536:
        p.error("model fingerprint or reader port mismatch")
    if args.public_smoke == bool(args.sealed_original):
        p.error("select --public-smoke or --sealed-original")
    if args.sealed_original and (
        sha(args.sealed_original) != args.sealed_hash
        or not args.sealed_hash.startswith("83bf5912")
        or not args.sealed_hash.endswith("10e")
    ):
        p.error("original sealed file fingerprint mismatch")
    if args.sealed_original and any(
        path.resolve().is_relative_to(HERE.parents[1])
        for path in (args.manifest, args.sealed_original, args.out)
    ):
        p.error("keep blind inputs and output outside the checkout")
    for port, model in ((8082, args.embed_model), (args.reader_port, args.reader_model)):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
        try:
            conn.request("GET", "/v1/models")
            resp = conn.getresponse()
            listing = json.loads(resp.read(1 << 20))
            if resp.status != 200 or model not in [row["id"] for row in listing["data"]]:
                raise ValueError("configured local model ID is not listed")
        finally:
            conn.close()
    data = json.loads(args.manifest.read_text("utf-8-sig"))
    with args.out.open("x", encoding="utf-8") as sink:
        study(args, sink, data)


if __name__ == "__main__":
    main()
