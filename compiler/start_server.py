"""Start a local llama-server without platform-specific GPU assumptions."""
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from config_loader import load_config
from model_client import endpoint


def build_cmd(config):
    endpoint(config["server_url"])
    binary = config.get("llama_bin") or shutil.which("llama-server") or shutil.which("llama-server.exe")
    if not binary:
        raise ValueError("Set llama_bin to an existing llama-server executable")
    model = config.get("model_name", "")
    if not Path(model).is_file():
        raise ValueError("Set model_name to a local GGUF file")
    url = urlsplit(config["server_url"])
    return [binary, "-m", model, "--host", url.hostname, "--port", str(url.port or 8081),
            "-c", str(config.get("context_size", 8192)),
            "-ngl", str(config.get("gpu_layers", 0))]


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bg", action="store_true")
    args = parser.parse_args()
    try:
        cmd = build_cmd(load_config())
        if args.bg:
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
            print("Started; verify setup.py check before claiming model readiness")
            return 0
        return subprocess.run(cmd).returncode
    except (ValueError, OSError) as exc:
        print("PROBLEM: model startup failed (" + type(exc).__name__ + ")")
        print("HOW TO FIX: set llama_bin/model_name and verify the local server manually.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
