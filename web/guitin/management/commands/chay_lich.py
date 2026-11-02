"""Chạy mỗi phút (systemd timer trên server, hoặc Task Scheduler trên Windows):
lịch hẹn gửi, nhắc lại khách chưa trả lời, phát hiện đợt gửi bị gián đoạn.

    python manage.py chay_lich            # chạy 1 lần rồi thoát
    python manage.py chay_lich --lap 60   # chạy mãi, mỗi 60 giây một lần (khi không có bộ hẹn giờ của hệ điều hành)
"""
import time

from django.core.management.base import BaseCommand

from guitin import cham_soc


class Command(BaseCommand):
    help = "Chạy lịch hẹn gửi, nhắc lại khách chưa trả lời, phát hiện đợt gửi bị gián đoạn"

    def add_arguments(self, parser):
        parser.add_argument("--lap", type=int, default=0, help="Lặp lại sau mỗi bấy nhiêu giây (0 = chạy 1 lần)")

    def handle(self, *args, lap=0, **kwargs):
        while True:
            try:
                for v in cham_soc.chay_lich():
                    self.stdout.write(v)
                # Trang “Tình trạng hệ thống” đọc file này để biết lịch tự động còn chạy không
                from django.conf import settings
                from django.utils import timezone
                (settings.ROOT_DIR / "data").mkdir(exist_ok=True)
                (settings.ROOT_DIR / "data" / "chay-lich.txt").write_text(timezone.now().isoformat())
            except Exception as e:  # noqa: BLE001 — chạy mãi thì lỗi một lần không được làm dừng hẳn
                self.stderr.write(f"Lỗi khi chạy lịch: {e}")
                if not lap:
                    raise
            if not lap:
                return
            time.sleep(max(10, lap))
