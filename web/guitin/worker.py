"""Gọi Zalo worker (Node) chạy nội bộ ở 127.0.0.1."""
import json
import urllib.error
import urllib.request

from django.conf import settings


class WorkerLoi(Exception):
    pass


CHUA_CHAY = "Zalo worker chưa chạy. Kiểm tra cửa sổ \"Zalo worker\" (mở bằng chay-web.bat) còn mở không."


def goi(method, path, body=None, timeout=8):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        settings.WORKER_URL + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Worker-Token": settings.WORKER_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:  # noqa: BLE001
            msg = None
        raise WorkerLoi(msg or f"Zalo worker báo lỗi {e.code}") from e
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise WorkerLoi(CHUA_CHAY) from e


OFFLINE = {"login": "out", "account": None, "qr": None, "running": False, "runBy": None,
           "stopRequested": False, "current": None, "progress": None, "waitUntil": None}


def trang_thai(uid):
    """Trạng thái Zalo của 1 tài khoản; nếu worker không chạy thì trả về 'offline' thay vì báo lỗi."""
    try:
        return {**goi("GET", f"/status?uid={int(uid)}", timeout=3), "worker": True}
    except WorkerLoi:
        return {**OFFLINE, "worker": False}


def tat_ca():
    """Trạng thái Zalo của mọi tài khoản: {id: trạng thái}. Rỗng nếu worker không chạy."""
    try:
        return {int(k): v for k, v in goi("GET", "/status/all", timeout=3).get("accounts", {}).items()}
    except WorkerLoi:
        return None
