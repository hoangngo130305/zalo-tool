#!/usr/bin/env python
import os
import sys

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")  # in được tiếng Việt trên cửa sổ lệnh Windows

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zalotool.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
