"""Trang “Tình trạng hệ thống” trong /admin/: worker, database, ổ đĩa, sao lưu, phiên Zalo, hàng đợi, lịch tự động."""
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone
from django.utils.timezone import localtime

from . import worker
from .models import DotGui, NguoiDung


def _co(x):
    return f"{x / 1024 ** 3:.1f} GB" if x >= 1024 ** 3 else f"{x / 1024 ** 2:.1f} MB"


def thu_muc_sao_luu():
    return Path(str(settings.CONF.get("saoLuu", {}).get("thuMuc") or Path.home() / "sao-luu")).expanduser()


def kiem_tra():
    """Danh sách mục kiểm tra: {"ten", "ok": True/False/None (None = chỉ thông tin), "chi_tiet"}."""
    out = []
    st = worker.tat_ca()
    out.append({"ten": "Zalo worker", "ok": st is not None,
                "chi_tiet": "Đang chạy" if st is not None else "Không chạy: chưa gửi được tin, kiểm tra dịch vụ zalo-tool-worker"})
    if st is not None:
        ten = {u.id: str(u) for u in NguoiDung.objects.filter(id__in=list(st))}
        for uid, s in sorted(st.items()):
            dang = {"in": "đã kết nối", "qr": "đang chờ quét QR", "checking": "đang kết nối", "out": "chưa kết nối"}.get(s.get("login"), "?")
            them = []
            if s.get("running"):
                p = s.get("progress") or {}
                them.append(f"đang gửi {p.get('i', 0)}/{p.get('total', '?')}")
            if s.get("login") == "in":
                them.append("đang theo dõi phản hồi" if s.get("listening") else "chưa theo dõi phản hồi")
            if s.get("choTuGui"):
                them.append(f"{s['choTuGui']} tin tự gửi đang chờ")
            if s.get("saved") and s.get("login") == "out":
                them.append("phiên Zalo đã hết hạn, cần quét QR lại")
            out.append({"ten": f"Zalo của {ten.get(uid, f'#{uid}')}", "ok": s.get("login") == "in",
                        "chi_tiet": f"{s.get('account') or ''} {dang}" + (f" ({', '.join(them)})" if them else "")})
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        co = ""
        if connection.vendor == "mysql":
            with connection.cursor() as c:
                c.execute("SELECT SUM(data_length + index_length) FROM information_schema.tables WHERE table_schema = DATABASE()")
                co = f", dung lượng {_co(int(c.fetchone()[0] or 0))}"
        out.append({"ten": "Database", "ok": True, "chi_tiet": f"{connection.vendor} kết nối được{co}"})
    except Exception as e:  # noqa: BLE001
        out.append({"ten": "Database", "ok": False, "chi_tiet": str(e)})
    o = shutil.disk_usage(settings.ROOT_DIR)
    out.append({"ten": "Ổ đĩa", "ok": o.free > 2 * 1024 ** 3,
                "chi_tiet": f"Còn trống {_co(o.free)} / {_co(o.total)}" + ("" if o.free > 2 * 1024 ** 3 else " — sắp đầy, dọn bớt")})
    tm = thu_muc_sao_luu()
    files = sorted(tm.glob("*.gz"), key=lambda f: f.stat().st_mtime) if tm.is_dir() else []
    if files:
        moi = files[-1]
        luc = datetime.fromtimestamp(moi.stat().st_mtime, tz=timezone.get_current_timezone())
        cu = (timezone.now() - luc).total_seconds() > 36 * 3600
        out.append({"ten": "Sao lưu", "ok": not cu,
                    "chi_tiet": f"Gần nhất {localtime(luc):%H:%M %d/%m/%Y} ({moi.name}, {_co(moi.stat().st_size)}), "
                                f"giữ {len(files)} bản tại {tm}" + (" — quá 36 giờ chưa sao lưu" if cu else "")})
    else:
        out.append({"ten": "Sao lưu", "ok": False, "chi_tiet": f"Chưa có bản sao lưu nào trong {tm}"})
    q = settings.ROOT_DIR / "data" / "cho-bao-cao.jsonl"
    n = len([x for x in q.read_text(encoding="utf-8").splitlines() if x.strip()]) if q.is_file() else 0
    out.append({"ten": "Kết quả chưa lưu", "ok": n == 0,
                "chi_tiet": "Không có" if not n else f"{n} kết quả worker chưa báo được về web (sẽ tự gửi lại)"})
    f = settings.ROOT_DIR / "data" / "chay-lich.txt"
    if f.is_file():
        luc = datetime.fromisoformat(f.read_text().strip())
        tre = (timezone.now() - luc).total_seconds() > 300
        out.append({"ten": "Lịch tự động", "ok": not tre,
                    "chi_tiet": f"Chạy lần cuối {localtime(luc):%H:%M:%S %d/%m}" + (" — đã quá 5 phút, kiểm tra zalo-tool-lich.timer" if tre else "")})
    else:
        out.append({"ten": "Lịch tự động", "ok": False,
                    "chi_tiet": "Chưa chạy lần nào (hẹn giờ gửi, nhắc lại, phát hiện đợt bị gián đoạn cần lệnh chay_lich)"})
    gd = DotGui.objects.filter(trang_thai=DotGui.GIAN_DOAN, bat_dau__gte=timezone.now() - timedelta(days=7)).count()
    out.append({"ten": "Đợt gửi bị gián đoạn (7 ngày)", "ok": None if not gd else False, "chi_tiet": str(gd)})
    return out
