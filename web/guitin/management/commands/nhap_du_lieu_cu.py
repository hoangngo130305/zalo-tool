"""Chuyển dữ liệu từ các bảng của bản Node cũ (nguoi_nhan, ket_qua, cai_dat) sang bảng Django.
Chỉ nhập vào bảng Django còn trống, nên chạy lại nhiều lần cũng không bị trùng."""
import json
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from guitin.models import CaiDat, KetQua, NguoiNhan, chuan_hoa_sdt

BANG_CU = ["phien_dang_nhap", "nhat_ky", "nguoi_nhan", "ket_qua", "cai_dat", "tai_khoan"]


def co_bang(ten):
    return ten in connection.introspection.table_names()


class Command(BaseCommand):
    help = "Nhập dữ liệu từ bản Node cũ vào Django (danh sách, kết quả, nội dung tin nhắn, phiên Zalo)."

    def add_arguments(self, parser):
        parser.add_argument("--xoa-bang-cu", action="store_true", help="Xoá các bảng cũ sau khi đã nhập xong")

    @transaction.atomic
    def handle(self, *args, xoa_bang_cu=False, **kwargs):
        cur = connection.cursor()
        log = lambda m: self.stdout.write(self.style.SUCCESS(m))  # noqa: E731

        if co_bang("nguoi_nhan") and not NguoiNhan.objects.exists():
            cur.execute("SELECT thu_tu, sdt_goc, ten FROM nguoi_nhan ORDER BY thu_tu, id")
            rows = cur.fetchall()
            NguoiNhan.objects.bulk_create([NguoiNhan(thu_tu=t, sdt_goc=s, sdt=chuan_hoa_sdt(s), ten=n) for t, s, n in rows])
            log(f"Đã nhập {len(rows)} người nhận.")

        if co_bang("ket_qua") and not KetQua.objects.exists():
            cur.execute("SELECT sdt, ten, ten_zalo, ket_ban, tin_nhan, ghi_chu, thoi_gian FROM ket_qua")
            rows = cur.fetchall()
            tz = timezone.get_current_timezone()
            for sdt, ten, ten_zalo, ket_ban, tin_nhan, ghi_chu, tg in rows:
                if isinstance(tg, str):
                    tg = datetime.fromisoformat(tg)
                KetQua.objects.create(sdt=sdt, ten=ten or "", ten_zalo=ten_zalo or "", ket_ban=ket_ban, tin_nhan=tin_nhan,
                                      ghi_chu=ghi_chu or "", thoi_gian=timezone.make_aware(tg, tz) if timezone.is_naive(tg) else tg)
            log(f"Đã nhập {len(rows)} kết quả gửi.")

        if co_bang("cai_dat"):
            cur.execute("SELECT khoa, gia_tri FROM cai_dat")
            cu = {k: json.loads(v) for k, v in cur.fetchall()}
            if "cauHinh" in cu and not CaiDat.objects.exists():
                c = cu["cauHinh"]
                CaiDat(loi_moi_ket_ban=c.get("loiMoiKetBan", ""), noi_dung_tin_nhan=c.get("noiDungTinNhan", ""),
                       gui_ket_ban=c.get("guiKetBan", True), gui_tin_nhan=c.get("guiTinNhan", True),
                       gioi_han_moi_lan=c.get("gioiHanMoiLan", 20), cho_toi_thieu_giay=c.get("choToiThieuGiay", 60),
                       cho_toi_da_giay=c.get("choToiDaGiay", 150)).save()
                log("Đã nhập nội dung tin nhắn & cài đặt.")
            from guitin.models import NguoiDung
            qt = NguoiDung.objects.filter(vai_tro=NguoiDung.QUAN_TRI).order_by("id").first()
            session_file = settings.ROOT_DIR / "data" / "zalo-sessions" / f"{qt.id if qt else 1}.json"
            if "zaloSession" in cu and not session_file.exists():
                session_file.parent.mkdir(parents=True, exist_ok=True)
                session_file.write_text(json.dumps(cu["zaloSession"]), encoding="utf-8")
                log(f"Đã chuyển phiên đăng nhập Zalo cũ sang tài khoản quản trị ({session_file.name}).")

        if xoa_bang_cu:
            for t in BANG_CU:
                if co_bang(t):
                    cur.execute(f"DROP TABLE `{t}`")
            log("Đã xoá các bảng cũ của bản Node.")
