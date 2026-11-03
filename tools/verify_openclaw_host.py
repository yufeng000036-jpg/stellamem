"""Real OpenClaw CLI/hook acceptance with a local transport fixture, not an LLM score."""

import argparse
import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


def verify(repo, host, out):
    sys.path.insert(0, str(repo / "compiler"))
    from pipeline import compile_messages
    from source_reader import parse_messages
    from store import MemoryStore, INBOX
    from storage import Transaction, writer_lock, load_records, render_records
    from inbox import cmd_approve

    results = []
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            chunk = {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "transport-fixture",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": "Isolated transport fixture reply.",
                        },
                        "finish_reason": None,
                    }
                ],
            }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            chunk["choices"][0] = {"index": 0, "delta": {}, "finish_reason": "stop"}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="stellamem-host-acceptance-") as tmp:
        root = Path(tmp)
        mem = root / "memory"
        env = {
            k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR", "SYSTEMROOT") if k in os.environ
        }
        env.update(
            OPENCLAW_HOME=str(root / "home"),
            OPENCLAW_STATE_DIR=str(root / "host"),
            OPENCLAW_CONFIG_PATH=str(root / "host.json"),
            OPENCLAW_SKIP_CHANNELS="1",
        )
        install = [
            sys.executable,
            str(repo / "setup.py"),
            "install",
            "--mem-root",
            str(mem),
            "--oc-root",
            str(root / "host"),
            "--config",
            str(root / "compiler.json"),
            "--out-dir",
            str(root / "setup"),
        ]
        for name in ("first_install", "repeat_install"):
            p = subprocess.run(install, capture_output=True, timeout=30)
            assert p.returncode == 0, p.stderr.decode()
            if name == "repeat_install":
                assert "PLAN: 0 file writes" in p.stdout.decode()
            results.append({"case": name, "pass": True})
        snippet = json.loads((root / "setup/openclaw.snippet.json").read_text())
        config = {
            "plugins": snippet["plugins"],
            "tools": {"deny": ["*"]},
            "agents": {
                "defaults": {
                    "workspace": str(root / "workspace"),
                    "skipBootstrap": True,
                    "model": {"primary": "local/transport-fixture"},
                    "models": {"local/transport-fixture": {}},
                }
            },
            "models": {
                "mode": "replace",
                "providers": {
                    "local": {
                        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1",
                        "apiKey": "local-fixture",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "transport-fixture",
                                "name": "Transport fixture",
                                "contextWindow": 16384,
                                "maxTokens": 128,
                                "compat": {"supportsTools": False},
                            }
                        ],
                    }
                },
            },
        }
        config["plugins"]["allow"] = ["stellamem-adapter"]
        config["plugins"]["slots"] = {"memory": "none"}
        entry = config["plugins"]["entries"]["stellamem-adapter"]
        entry["config"]["activeScopes"] = ["garden", "studio"]
        logs = []

        def turn(name, expected=(), excluded=(), config_error=False):
            (root / "host.json").write_text(json.dumps(config))
            before = len(requests)
            started = time.monotonic()
            p = subprocess.run(
                [
                    "node",
                    str(host),
                    "agent",
                    "--local",
                    "--agent",
                    "main",
                    "--session-id",
                    str(uuid.uuid4()),
                    "--message",
                    "fictional habitat preference",
                    "--thinking",
                    "off",
                    "--json",
                    "--timeout",
                    "30",
                ],
                env=env,
                capture_output=True,
                timeout=65,
            )
            try:
                receipt = json.loads(p.stdout.decode())
            except ValueError:
                receipt = {}
            meta = receipt.get("meta", {})
            logs.append(
                {
                    "case": name,
                    "exit": p.returncode,
                    "final_prompt": meta.get("finalPromptText"),
                    "runner": meta.get("executionTrace", {}).get("runner"),
                    "effective_model": meta.get("executionTrace", {}).get("winnerModel"),
                    "stderr": p.stderr.decode(errors="replace").replace(tmp, "<ISOLATED>"),
                }
            )
            payload = json.dumps(requests[before:])
            ok = (
                (p.returncode != 0 and len(requests) == before)
                if config_error
                else (
                    p.returncode == 0
                    and len(requests) > before
                    and all(x in payload for x in expected)
                    and all(x not in payload for x in excluded)
                )
            )
            results.append(
                {
                    "case": name,
                    "pass": ok,
                    "exit": p.returncode,
                    "provider_calls": len(requests) - before,
                    "seconds": time.monotonic() - started,
                }
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"results": results, "logs": logs}, indent=2))
            print(name, ok, "calls", len(requests) - before, flush=True)
            if not ok:
                raise RuntimeError("host case failed: " + name)

        def seed(value, scope="garden", namespace=None, predicate="layout"):
            source = "[12:00:00] user: " + value + "\n"

            def extract(_system, payload):
                return json.dumps(
                    [
                        dict(
                            kind="preference",
                            subject="Fictional Habitat",
                            scope=scope,
                            predicate=predicate,
                            value=value,
                            evidence=value,
                            confidence=0.4,
                            message_index=0,
                            source="user",
                        )
                    ]
                )

            claims = [
                c
                for b in compile_messages(
                    parse_messages(source, "2026-01-01.md"), {"chunk_chars": 2500}, extract
                )
                for c in b["claims"]
            ]
            assert len(claims) == 1
            if namespace is not None:
                claims[0]["namespace"] = namespace
            with writer_lock(mem):
                store = MemoryStore(mem)
                store.ingest(claims[0], mode="archive")
                store.commit()
            return load_records(mem / INBOX)[1][-1]

        def approve(c, namespace="user"):
            with writer_lock(mem), contextlib.redirect_stdout(io.StringIO()) as buf:
                cmd_approve(
                    mem,
                    argparse.Namespace(
                        id=c["revision_id"],
                        source="user",
                        namespace=namespace,
                        kind=None,
                        subject=None,
                        scope=None,
                        predicate=None,
                        value=None,
                    ),
                )
            return json.loads(buf.getvalue())["transaction"]

        a = seed("prefer fictional habitat cobalt gardens")
        turn("pending_not_injected", excluded=["cobalt gardens"])
        tx = approve(a)
        saved_hooks = entry.pop("hooks", None)
        turn("missing_host_permission_negative_control", excluded=["cobalt gardens"])
        assert saved_hooks == {"allowConversationAccess": True}, (
            "installer must provide the reviewed host grant"
        )
        entry["hooks"] = saved_hooks
        turn("approved_actual_hook", expected=["cobalt gardens"])
        turn("fresh_host_process_recall", expected=["cobalt gardens"])
        with writer_lock(mem):
            Transaction(mem).rollback(tx, yes=True)
        turn("approval_rollback", excluded=["cobalt gardens"])
        approve(load_records(mem / INBOX)[1][0])
        approve(seed("prefer fictional habitat amber studio", scope="studio"))
        turn("same_user_two_explicit_scopes", expected=["cobalt gardens", "amber studio"])
        entry["config"]["activeScopes"] = []
        turn("ungranted_scopes", excluded=["cobalt gardens", "amber studio"])
        entry["config"]["activeScopes"] = ["garden", "studio"]
        approve(
            seed(
                "prefer fictional habitat violet persona",
                scope="global",
                predicate="persona-layout",
            ),
            "persona",
        )
        approve(
            seed(
                "prefer fictional habitat silver viewer", scope="global", predicate="viewer-layout"
            ),
            "viewer",
        )
        turn(
            "user_isolation",
            expected=["cobalt gardens"],
            excluded=["violet persona", "silver viewer"],
        )
        entry["config"]["requesterSubject"] = "persona"
        turn(
            "persona_isolation",
            expected=["violet persona"],
            excluded=["cobalt gardens", "silver viewer"],
        )
        entry["config"]["requesterSubject"] = "viewer"
        turn(
            "viewer_isolation",
            expected=["silver viewer"],
            excluded=["cobalt gardens", "violet persona"],
        )
        entry["config"]["requesterSubject"] = "user"
        conflict = seed("prefer fictional habitat scarlet gardens", namespace="user")
        assert conflict["relation"] == "UNCERTAIN"
        turn(
            "unresolved_conflict",
            expected=["amber studio"],
            excluded=["cobalt gardens", "scarlet gardens"],
        )
        # Test a valid expired record in a fresh slot without damaging conflict history.
        approve(seed("prefer fictional habitat copper expired", predicate="expired-layout"))
        from claim_normalizer import ROUTE

        file = ROUTE["preference"]
        prefix, records = load_records(mem / file)
        for record in records:
            if "copper expired" in record.get("value", ""):
                record["valid_to"] = "2020-01-01T00:00:00Z"
        with writer_lock(mem):
            Transaction(mem).commit({file: render_records(prefix, records)})
        turn("expired_fact", expected=["amber studio"], excluded=["copper expired"])
        # Legacy v2 namespace encoded in subject still works; free prose stays inert.
        prefix, records = load_records(mem / file)
        legacy = dict(next(r for r in records if "amber studio" in r["value"]))
        legacy.pop("namespace", None)
        legacy.update(
            subject="user",
            claim_id="legacy-user",
            revision_id="legacy-revision",
            value="prefer fictional habitat legacy teal",
            predicate="legacy-layout",
        )
        records.append(legacy)
        with writer_lock(mem):
            Transaction(mem).commit(
                {
                    file: render_records(
                        prefix + "\nUnmanaged fictional legacy mauve prose.\n", records
                    )
                }
            )
        turn("legacy_v2_and_prose", expected=["legacy teal"], excluded=["legacy mauve"])
        entry["config"]["requesterSubject"] = "invalid"
        turn("invalid_config_fail_closed", config_error=True)
        entry["config"]["requesterSubject"] = "user"
        ready = root / "interrupted"
        code = """import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import storage
root=Path(sys.argv[2]);ready=Path(sys.argv[3]);original=storage.atomic_write
def pause(path,content):
 original(path,content)
 if Path(path).name=='host-left.json':
  ready.write_text('ready')
  while True:time.sleep(.1)
storage.atomic_write=pause
with storage.writer_lock(root):storage.Transaction(root).commit({'system/host-left.json':'left','system/host-right.json':'right'})
"""
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(repo / "compiler"), str(mem), str(ready)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists()
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        turn("interrupted_transaction_blocks_hook", excluded=["amber studio", "legacy teal"])
        with writer_lock(mem):
            Transaction(mem).recover()
            Transaction(mem).recover()
        assert (mem / "system/host-right.json").read_text() == "right"
        turn("recovery_restores_actual_hook", expected=["amber studio", "legacy teal"])
        probe = subprocess.run(
            [
                sys.executable,
                str(repo / "setup.py"),
                "check",
                "--config",
                str(root / "compiler.json"),
                "--offline",
                "--json",
            ],
            capture_output=True,
            timeout=30,
        )
        capture = next(c for c in json.loads(probe.stdout) if c["name"] == "Host source capture")
        sqlite_present = (root / "host/agents/main/agent/openclaw-agent.sqlite").is_file()
        jsonl_present = (root / "host/agents/main/sessions").is_dir()
        assert sqlite_present and not jsonl_present
        assert probe.returncode == 1 and capture["status"] == "FAIL"
        report = {
            "source_capture_acceptance": "NOT_MET: SQLite host; legacy autolog requires JSONL",
            "source_probe": {
                "sqlite_present": sqlite_present,
                "jsonl_directory_present": jsonl_present,
                "exit": probe.returncode,
                "diagnostic": capture,
            },
            "host_version": "2026.10.9",
            "kind": "REAL embedded OpenClaw CLI and before_prompt_build hook; deterministic local provider fixture",
            "not_measured": [
                "model answer intelligence",
                "production gateway service",
                "daily-autolog capture",
                "OS reboot",
            ],
            "results": results,
            "logs": logs,
            "model_calls": len(requests),
        }
        out.write_text(json.dumps(report, indent=2) + "\n")
    server.shutdown()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--host-cli", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    verify(a.repo.resolve(), a.host_cli.resolve(), a.out.resolve())
