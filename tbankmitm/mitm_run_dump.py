"""Запуск mitmdump через тот же python, что и venv (без mitmdump.exe и без -m mitmproxy.tools.dump)."""
import sys

from mitmproxy.tools.main import mitmdump

if __name__ == "__main__":
    mitmdump(sys.argv[1:])
