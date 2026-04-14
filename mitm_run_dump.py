"""Запуск mitmdump через venv python.

Без аргументов — цепочка из mitm_addon_chain.py (как start.bat / start_vps.sh).
С аргументами — проброс в mitmdump как раньше (ручной вызов).
"""
import os
import sys

from mitmproxy.tools.main import mitmdump


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    if len(sys.argv) > 1:
        mitmdump(sys.argv[1:])
        return

    from mitm_addon_chain import build_mitmdump_argv

    mitmdump(build_mitmdump_argv(script_dir))


if __name__ == "__main__":
    main()
