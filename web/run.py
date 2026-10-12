"""Chạy Zalo Tool bằng waitress (ổn định hơn runserver, chạy tốt trên Windows)."""
import os
import sys
import webbrowser

for _s in (sys.stdout, sys.stderr):
    _s.reconfigure(encoding="utf-8", errors="replace")  # in được tiếng Việt trên cửa sổ lệnh Windows
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zalotool.settings")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import socket  # noqa: E402

from django.conf import settings  # noqa: E402
from waitress import serve  # noqa: E402

from zalotool.wsgi import application  # noqa: E402

if __name__ == "__main__":
    port = settings.DJANGO_PORT
    host = "0.0.0.0" if settings.LAN else "127.0.0.1"
    link = f"http://localhost:{port}"
    print(f"Zalo Tool đang chạy tại {link}  (trang quản trị: {link}/admin/)")
    if settings.LAN:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            print(f"Máy khác cùng mạng truy cập: http://{ip}:{port}")
        except OSError:
            pass
    print("Giữ cửa sổ này mở trong lúc dùng.")
    if "--khong-mo" not in sys.argv:
        webbrowser.open(link)
    serve(application, host=host, port=port, threads=8, ident="ZaloTool")
