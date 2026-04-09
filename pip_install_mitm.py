# -*- coding: utf-8 -*-
"""Install mitmproxy into venv without using any proxy (fixes ProxyError / 10061)."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if sys.platform == "win32":
    VENV_PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
else:
    VENV_PY = os.path.join(ROOT, "venv", "bin", "python")


def clean_env():
    env = dict(os.environ)
    for key in list(env.keys()):
        low = key.lower()
        if "proxy" in low:
            del env[key]
    # Ignore %APPDATA%\pip\pip.ini (often contains proxy=)
    env["PIP_CONFIG_FILE"] = "nul" if sys.platform == "win32" else os.devnull
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def main():
    if not os.path.isfile(VENV_PY):
        print("ERROR: venv not found. Create venv first (setup.bat step 1).")
        return 1
    env = clean_env()
    pip_base = [VENV_PY, "-m", "pip"]
    extra = [
        "--isolated",
        "--no-cache-dir",
        "--trusted-host",
        "pypi.org",
        "--trusted-host",
        "files.pythonhosted.org",
    ]
    steps = [
        pip_base + ["install", "--upgrade", "pip"] + extra,
        pip_base + ["install", "mitmproxy"] + extra,
        pip_base + ["install", "pymupdf"] + extra,
    ]
    for cmd in steps:
        print(">>", " ".join(cmd[3:]))
        p = subprocess.run(cmd, cwd=ROOT, env=env)
        if p.returncode != 0:
            print("ERROR: pip exited with", p.returncode)
            return p.returncode
    p = subprocess.run(
        [
            VENV_PY,
            "-c",
            "import mitmproxy; import fitz; print('mitmproxy OK, PyMuPDF (fitz) OK')",
        ],
        cwd=ROOT,
        env=env,
    )
    return p.returncode


if __name__ == "__main__":
    sys.exit(main() or 0)
