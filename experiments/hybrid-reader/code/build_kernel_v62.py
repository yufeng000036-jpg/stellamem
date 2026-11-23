#!/usr/bin/env python3
"""Build a V6.2 reader kernel by surgical cloning of the PROVEN v1
hybrid kernel (../hybrid_kernel.py): identical model loading (4-bit
nf4), identical batched generation, identical JSON-payload user format,
identical decoding. Only four things change: the embedded inputs, the
system prompt (prompt_v2.txt), the output file name, and the row-count
assert. This avoids re-deriving inference conditions (an earlier
from-scratch kernel both crashed on a peft/torchao dispatch and would
have changed the inference conditions vs the v1 run).

Usage: python3 build_kernel_v62.py --inputs v62_inputs.jsonl --n 420 \
    --out-name v62_outputs.jsonl --kernel-out v62_kernel.py
"""

import argparse
import ast
import base64
import hashlib
import json
import lzma
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
HP = os.path.dirname(HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--out-name", required=True)
    ap.add_argument("--kernel-out", required=True)
    args = ap.parse_args()

    src = open(os.path.join(HP, "hybrid_kernel.py"), encoding="utf-8").read()

    prompt_path = os.path.join(HERE, "prompt_v2.txt")
    if not os.path.exists(prompt_path):
        prompt_path = os.path.join(HP, "prompt_v2.txt")  # package layout
    prompt = open(prompt_path, encoding="utf-8").read()
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    raw = open(args.inputs, "rb").read()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    assert len(rows) == args.n, len(rows)
    for f in ("qid", "question", "question_date", "context", "set"):
        assert f in rows[0], f"inputs row missing field {f}"
    inputs_sha = hashlib.sha256(raw).hexdigest()
    b64 = base64.b64encode(lzma.compress(raw, preset=6)).decode()

    # 1. embedded inputs + their sha
    src, k = re.subn(
        r'INPUTS_XZ_B64 = ".*?"', lambda m: f'INPUTS_XZ_B64 = "{b64}"', src, flags=re.S
    )
    assert k == 1
    src, k = re.subn(
        r'INPUTS_SHA256 = "[0-9a-f]+"', lambda m: f'INPUTS_SHA256 = "{inputs_sha}"', src
    )
    assert k == 1
    # 2. prompt + its sha
    new_sys = (
        f'HYBRID_SYS = {json.dumps(prompt, ensure_ascii=False)}\nHYBRID_SYS_SHA256 = "{prompt_sha}"'
    )
    src, k = re.subn(
        r'HYBRID_SYS = (?:".*?"|\'.*?\')\nHYBRID_SYS_SHA256 = "[0-9a-f]+"',
        lambda m: new_sys,
        src,
        flags=re.S,
    )
    assert k == 1
    # 3. row-count assert + output name + arm label + stage label
    src, k = re.subn(
        r"assert len\(inputs\) == \d+", lambda m: f"assert len(inputs) == {args.n}", src
    )
    assert k == 1
    src, k = re.subn(
        r'WORK \+ "/hybrid_outputs\.jsonl"', lambda m: f'WORK + "/{args.out_name}"', src
    )
    assert k == 1
    src = src.replace('"arm": "H1"', '"arm": "V62"')
    src = src.replace(":H1:{row", ":V62:{row")
    src = src.replace('"stage": "eval2-HYBRID"', '"stage": "v62"')

    # verify the clone: syntax, embedded prompt, embedded inputs
    ast.parse(src)
    m = re.search(r'HYBRID_SYS = (".*?"|\'.*?\')\nHYBRID_SYS_SHA256', src, flags=re.S)
    embedded_prompt = ast.literal_eval(m.group(1))
    assert embedded_prompt == prompt, "prompt round-trip mismatch"
    m = re.search(r'INPUTS_XZ_B64 = "(.*?)"', src, flags=re.S)
    dec = lzma.decompress(base64.b64decode(m.group(1)))
    assert hashlib.sha256(dec).hexdigest() == inputs_sha, "inputs round-trip"
    assert json.dumps(prompt, ensure_ascii=False)  # sanity

    with open(args.kernel_out, "w", encoding="utf-8") as f:
        f.write(src)
    print(
        "kernel written:",
        args.kernel_out,
        "| prompt_sha:",
        prompt_sha,
        "| inputs_sha:",
        inputs_sha,
        "| n:",
        len(rows),
    )


if __name__ == "__main__":
    main()
