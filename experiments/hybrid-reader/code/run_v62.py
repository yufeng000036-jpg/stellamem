#!/usr/bin/env python3
"""Submit a V6.2 reader kernel to Kaggle, poll, download outputs, and
append the GPU quota delta to gpu_ledger_v62.jsonl. Same mechanics as
../run_hybrid.py (urllib + surrogate credential).

Usage: python3 run_v62.py --kernel v62_kernel.py --slug muse-reader-v62 \
    --out-dir kaggle-output-v62 --note eval2-v62

Segment budget (V6.3 stage 1): cumulative quota used must stay
<= SEGMENT_START_USED + 6500 s. The runner refuses to push when the
segment is already over cap and reports the delta after the run.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import (
    add_surrogate_to_request,
    dynamic_credential_entry,
    read_response_body,
)

USER = "yufenf"
HERE = os.path.dirname(os.path.abspath(__file__))
SEGMENT_START_USED = 85523.8
SEGMENT_CAP = 6500.0


def surr():
    return str(
        dynamic_credential_entry("custom.kaggle", "access_token")["surrogate"]
    ).strip()


def post_api(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {surr()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, read_response_body(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused")


def get_www(url):
    req = urllib.request.Request(url)
    add_surrogate_to_request(req, "custom.kaggle", allowed_hosts=["www.kaggle.com"])
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=60) as r:
            return r.status, read_response_body(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def quota(tag):
    s, b = post_api(
        "https://api.kaggle.com/v1/kernels.KernelsApiService/GetAcceleratorQuotaStatistics",
        {},
    )
    try:
        j = json.loads(b.decode(errors="replace"))
        used = float(j["gpuQuota"]["timeUsed"].rstrip("s"))
        print(f"quota[{tag}] gpu_timeUsed={used}", flush=True)
        return used
    except Exception:
        print(f"quota[{tag}] HTTP {s} unparsable", flush=True)
        return None


def kernel_status(slug):
    s, b = get_www(
        f"https://www.kaggle.com/api/v1/kernels/status?userName={USER}&kernelSlug={slug}"
    )
    try:
        return json.loads(b.decode(errors="replace")).get("status")
    except Exception:
        return f"http{s}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--note", required=True)
    args = ap.parse_args()

    q_before = quota("before")
    if q_before is None:
        print("quota unreadable; refusing to push.", flush=True)
        return 2
    if q_before - SEGMENT_START_USED > SEGMENT_CAP:
        print("SEGMENT CAP ALREADY EXCEEDED -- aborting", flush=True)
        return 2

    src = open(args.kernel, encoding="utf-8").read()
    payload = {
        "slug": f"{USER}/{args.slug}",
        "newTitle": args.slug,
        "text": src,
        "language": "python",
        "kernelType": "script",
        "isPrivate": True,
        "enableGpu": True,
        "enableTpu": False,
        "enableInternet": True,
        "machineShape": "NvidiaTeslaT4",
        "datasetDataSources": [],
        "kernelDataSources": [f"{USER}/muse-reader-train1"],
        "competitionDataSources": [],
        "modelDataSources": [],
        "categoryIds": [],
    }
    s, b = post_api(
        "https://api.kaggle.com/v1/kernels.KernelsApiService/SaveKernel", payload
    )
    print(f"[{args.slug}] SaveKernel", s, b[:400].decode(errors="replace"), flush=True)
    if s != 200:
        print("push failed; stopping.", flush=True)
        return 1
    st = None
    for i in range(240):
        time.sleep(30)
        st = kernel_status(args.slug)
        if i % 2 == 0 or st in ("complete", "error", "cancelAcknowledged"):
            print(f"[{args.slug}] poll {i + 1}: status={st}", flush=True)
        if st in ("complete", "error", "cancelAcknowledged"):
            break
    print(f"[{args.slug}] FINAL {st}", flush=True)
    outdir = os.path.join(HERE, args.out_dir)
    os.makedirs(outdir, exist_ok=True)
    s, b = get_www(
        f"https://www.kaggle.com/api/v1/kernels/output?userName={USER}&kernelSlug={args.slug}&pageSize=100"
    )
    print(f"[{args.slug}] output list HTTP", s, flush=True)
    try:
        files = json.loads(b.decode(errors="replace")).get("files", [])
    except Exception:
        files = []
    print(
        f"[{args.slug}] files",
        [(f.get("fileName"), f.get("totalBytes")) for f in files],
        flush=True,
    )
    for f in files:
        url = f.get("url")
        if not url:
            continue
        name = f.get("fileName")
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
        with open(os.path.join(outdir, name), "wb") as fh:
            fh.write(data)
        print("saved", name, len(data), flush=True)
    q_after = quota("after")
    if q_after is not None:
        delta = q_after - q_before
        seg = q_after - SEGMENT_START_USED
        print(
            f"GPU DELTA this kernel: {delta:.1f}s | segment cumulative: "
            f"{seg:.1f}s of {SEGMENT_CAP:.0f}s",
            flush=True,
        )
        with open(
            os.path.join(HERE, "gpu_ledger_v62.jsonl"), "a", encoding="utf-8"
        ) as fh:
            fh.write(
                json.dumps(
                    {
                        "note": args.note,
                        "slug": args.slug,
                        "quota_before": q_before,
                        "quota_after": q_after,
                        "delta": delta,
                        "segment_cumulative": seg,
                        "final_status": st,
                    }
                )
                + "\n"
            )
    return 0 if st == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
