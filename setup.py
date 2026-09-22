#!/usr/bin/env python3
"""Stellamem local installer and read-only diagnostics (Python standard library)."""

import argparse
import json
import os
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "compiler"))
from model_client import endpoint
from storage import atomic_write, sha

SUBDIRS = ("core", "long-term", "semantic", "episodic", "system", "working", "daily")


def planned_files(args):
    root = Path(args.mem_root or Path.home() / ".stellamem").expanduser().resolve()
    config_path = Path(
        args.config or os.environ.get("STELLAMEM_CONFIG", HERE / "config.json")
    ).resolve()
    out = Path(args.out_dir or HERE / "setup-out").resolve()
    if root == HERE or HERE.is_relative_to(root):
        raise ValueError("Memory root must be separate from repository")
    endpoint(args.model_url)
    cfg = {
        "schema_version": 2,
        "memories_root": str(root),
        "server_url": args.model_url,
        "model_id": args.model_id or "",
        "model_name": "",
        "llama_bin": "",
        "chunk_chars": 4000,
        "model_timeout": 90,
        "telemetry": False,
    }
    host = Path(args.oc_root).expanduser().resolve() if args.oc_root else None
    if host:
        cfg["sessions_dir"] = str(
            Path(args.sessions_dir).resolve()
            if args.sessions_dir
            else host / "agents/main/sessions"
        )
    files = {}
    for src in sorted((HERE / "templates").rglob("*")):
        if src.is_file():
            dst = root / src.relative_to(HERE / "templates")
            if dst.resolve() != dst or not dst.resolve().is_relative_to(root):
                raise ValueError("Memory template target is a symlink")
            if not dst.exists():
                files[dst] = src.read_bytes()
    if config_path.exists():
        current = json.loads(config_path.read_text(encoding="utf-8-sig"))
        if Path(current.get("memories_root", "")).resolve() != root:
            raise ValueError(
                "Existing config points elsewhere; choose --config or matching --mem-root"
            )
        if current.get("server_url") != args.model_url:
            raise ValueError("Existing model URL differs; review a backed-up config change first")
        if host and current.get("sessions_dir") != cfg.get("sessions_dir"):
            raise ValueError("Existing sessions_dir differs; review config before installing hook")
    else:
        files[config_path] = (json.dumps(cfg, indent=2) + "\n").encode()
    if host:
        for src_dir, target, entry in (
            (HERE / "hooks/daily-autolog", host / "hooks/daily-autolog", "handler.js"),
            (HERE / "adapters/openclaw", host / "extensions/stellamem-adapter", "index.js"),
        ):
            for src in sorted(src_dir.rglob("*")):
                if src.is_file():
                    data = src.read_text(encoding="utf-8")
                    if src.name == entry:
                        data = data.replace("../../shared/runtime.mjs", "./shared/runtime.mjs")
                        replacements = {
                            '"<YOUR_OPENCLAW_SESSIONS_DIR>"': json.dumps(cfg.get("sessions_dir")),
                            '"<YOUR_DAILY_DIR>"': json.dumps(str(root / "daily")),
                            '"<YOUR_TZ_OFFSET>"': json.dumps(str(args.tz_offset)),
                            '"<YOUR_MEMORIES_PATH>"': json.dumps(str(root)),
                        }
                        for old, new in replacements.items():
                            data = data.replace(old, new)
                    files[target / src.relative_to(src_dir)] = data.encode("utf-8")
            for src in (HERE / "shared").glob("*"):
                if src.is_file():
                    files[target / "shared" / src.name] = src.read_bytes()
            files[target / "package.json"] = b'{"private":true,"type":"module"}\n'
        snippet = {
            "hooks": {
                "internal": {"enabled": True, "entries": {"daily-autolog": {"enabled": True}}}
            },
            "plugins": {
                "entries": {
                    "stellamem-adapter": {
                        "enabled": True,
                        "config": {"coreDir": str(root), "maxInjectChars": 4000},
                    }
                }
            },
        }
        files[out / "openclaw.snippet.json"] = (json.dumps(snippet, indent=2) + "\n").encode()
    manifest_path = out / "install-manifest.json"
    previous = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    actions = []
    for dst, content in files.items():
        if dst.resolve() != dst:
            raise ValueError("Install target resolves through symlink")
        if dst.exists():
            old = dst.read_bytes()
            if old == content:
                continue
            if not args.upgrade or previous.get(str(dst)) != sha(old):
                raise ValueError("Existing customized file; compare before --upgrade")
            actions.append((dst, content, old))
        else:
            actions.append((dst, content, None))
    return root, config_path, out, files, actions


def cmd_install(args):
    root, config_path, out, files, actions = planned_files(args)
    print("PLAN: " + str(len(actions)) + " file writes; existing memory preserved")
    for dst, _, before in actions:
        print(("BACKUP + UPDATE " if before is not None else "CREATE ") + str(dst))
    if args.dry_run:
        print("DRY RUN: no files changed")
        return 0
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    for dst, content, before in actions:
        if before is not None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            atomic_write(dst.with_name(dst.name + ".bak-" + stamp), before)
        atomic_write(dst, content)
    atomic_write(
        out / "install-manifest.json",
        json.dumps({str(p): sha(data) for p, data in files.items()}, indent=2),
    )
    report = (
        "# Installation report\n\nFiles installed; runtime verification remains required.\n"
        "Config: " + str(config_path) + "\nMemory root: " + str(root) + "\n"
        "OpenClaw config was not read or changed. If installing host integration,\n"
        "review setup snippet and obtain authorization before enabling/restarting host.\n"
        "Run check --config <path>, demo --offline, and a local-model smoke test.\n"
    )
    atomic_write(out / "install-report.md", report)
    print("INSTALL: OK (files only; model and host activation are separate checks)")
    return 0


def check_report(config_path=None, offline=False):
    checks = []

    def add(name, status, cause="", fix=""):
        checks.append({"name": name, "status": status, "cause": cause, "fix": fix})

    add(
        "Python",
        "OK" if sys.version_info >= (3, 10) else "FAIL",
        sys.version.split()[0],
        "Use Python 3.10+",
    )
    add("OS", "OK", platform.system() + " " + platform.release())
    node = shutil.which("node")
    add(
        "Node", "OK" if node else "OPTIONAL", "Needed only for host integration", "Install Node 20+"
    )
    path = Path(config_path or os.environ.get("STELLAMEM_CONFIG", HERE / "config.json"))
    try:
        cfg = json.loads(path.read_text(encoding="utf-8-sig"))
        root = Path(cfg["memories_root"])
        endpoint(cfg["server_url"])
        add("Config", "OK")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        add(
            "Config",
            "FAIL",
            "Missing or invalid config/local model URL",
            "Run install --dry-run --mem-root <path>, then install",
        )
        return checks
    add("Memory root", "OK" if root.is_dir() else "FAIL", "", "Run install with this memory root")
    writable = root.is_dir() and os.access(root, os.W_OK)
    add(
        "Permissions",
        "OK" if writable else "FAIL",
        "Read-only access check; actual writes verified during install",
        "Check directory permissions",
    )
    for rel in (
        "core/preferences.md",
        "core/identity.md",
        "core/user-core.md",
        "system/version-chain.json",
        "system/memory-inbox.md",
    ):
        add(
            "File " + rel,
            "OK" if (root / rel).is_file() else "FAIL",
            "",
            "Re-run install; templates never overwrite",
        )
    from storage import load_records
    from security import contains_secret
    from store import FILES

    try:
        count = 0
        for rel in FILES:
            path2 = root / rel
            _, records = load_records(path2)
            count += len(records)
            if path2.exists() and contains_secret(path2.read_text(encoding="utf-8")):
                raise ValueError("secret")
        add("Secret scan", "OK", "Current managed files scanned; not Git history")
        add("Memory records", "OK", str(count))
    except (ValueError, OSError):
        add(
            "Secret scan / records",
            "FAIL",
            "Secret-like content or invalid record",
            "Inspect locally; never paste contents into public issues",
        )
    try:
        cursor = root / "system/extraction-cursor.json"
        if cursor.exists():
            data = json.loads(cursor.read_text(encoding="utf-8"))
            if data.get("schema_version") != 2 or not isinstance(data.get("sources"), dict):
                raise ValueError("cursor")
        add("Cursor", "OK", "Fresh or valid v2 cursor")
    except (OSError, ValueError, TypeError):
        add("Cursor", "FAIL", "Invalid cursor", "Restore a reviewed backup; do not delete memories")
    if (root / "system/transaction.json").exists():
        add(
            "Transaction",
            "FAIL",
            "Interrupted write",
            "Re-run compiler to recover before projection",
        )
    if offline:
        add("Model server", "SKIP", "Offline check requested")
    else:
        try:
            import urllib.request
            from model_client import NoRedirect

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(endpoint(cfg["server_url"], "models"), timeout=3) as response:
                models = json.loads(response.read(1024 * 1024))
            if not isinstance(models.get("data"), list):
                raise ValueError("invalid model response")
            add("Model server", "OK", "Local endpoint responds; run live demo to verify generation")
            available = [m.get("id") for m in models["data"] if isinstance(m, dict)]
            model_id = cfg.get("model_id")
            add(
                "Model",
                "OK" if model_id in available or len(available) == 1 else "FAIL",
                "Configured selection must identify an available model",
                "Set model_id from local model list",
            )
        except Exception:
            add(
                "Model server",
                "FAIL",
                "Local model unavailable or invalid response",
                "Start llama.cpp/Ollama/LM Studio and check server_url/model_id",
            )
    add("Tests", "NOT_RUN", "check is read-only", "python -m unittest discover -s tests")
    add(
        "OpenClaw hook/adapter",
        "NOT_VERIFIED",
        "Core installation does not imply host activation",
        "Verify installed hooks and prompt projection in a test host",
    )
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--config")
    check.add_argument("--offline", action="store_true")
    check.add_argument("--json", action="store_true")
    install = sub.add_parser("install")
    for name in ("mem-root", "config", "out-dir", "oc-root", "sessions-dir", "model-id"):
        install.add_argument("--" + name)
    install.add_argument("--model-url", default="http://127.0.0.1:8081")
    install.add_argument("--tz-offset", type=float, default=0)
    install.add_argument("--dry-run", action="store_true")
    install.add_argument(
        "--yes", action="store_true", help="Compatibility flag; never enables dangerous changes"
    )
    install.add_argument(
        "--upgrade",
        action="store_true",
        help="Back up and update only unmodified installer-owned files",
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            return cmd_install(args)
        checks = check_report(args.config, args.offline)
        if args.json:
            print(json.dumps(checks, indent=2))
        else:
            for c in checks:
                print(c["name"] + ": " + c["status"])
                if c["cause"]:
                    print("  CAUSE: " + c["cause"])
                if c["status"] == "FAIL":
                    print("  HOW TO FIX: " + c["fix"])
        return 1 if any(c["status"] == "FAIL" for c in checks) else 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print("PROBLEM: operation stopped (" + type(exc).__name__ + ")")
        print(
            "CAUSE: " + str(exc)
            if isinstance(exc, (ValueError, RuntimeError))
            else "CAUSE: file or configuration operation failed"
        )
        print(
            "HOW TO FIX: verify paths/permissions/config and compare existing files; retry dry-run."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
