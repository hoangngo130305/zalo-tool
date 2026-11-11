from django.contrib.admin.apps import AdminConfig
from django.apps import AppConfig


class GuiTinConfig(AppConfig):
    name = "guitin"
    verbose_name = "Gửi tin Zalo"

    def ready(self):
        from django.db.models.signals import post_delete, post_save, pre_delete

        from .models import AnhTinNhan, BoNhom, CaiDat, ChienDich, LichHen, NguoiDung, NguoiNhan
        from .services import ghi_log, tang_phien_ban

        # Sửa trong trang quản trị thì trang gửi tin đang mở cũng tải lại
        def doi(**kwargs):
            tang_phien_ban()

        # KetQua không có ở đây: kết quả gửi chỉ làm tải lại trang của người liên quan (services.tang_phien_ban(uids))
        # weak=False: hàm định nghĩa trong ready() sẽ bị dọn khỏi bộ nhớ nếu chỉ giữ tham chiếu yếu (tín hiệu mất tác dụng)
        for model in (NguoiNhan, NguoiDung, CaiDat, AnhTinNhan, ChienDich):
            post_save.connect(doi, sender=model, weak=False, dispatch_uid=f"pb-save-{model.__name__}")
            post_delete.connect(doi, sender=model, weak=False, dispatch_uid=f"pb-del-{model.__name__}")

        # Xoá chiến dịch: số của chiến dịch về “Chung”; số đã có sẵn ở Chung thì bỏ dòng của chiến dịch (không để 2 dòng trùng)
        def xoa_chien_dich(instance, **kwargs):
            chung = NguoiNhan.objects.filter(chien_dich__isnull=True, sdt__isnull=False).values("sdt")
            n, _ = NguoiNhan.objects.filter(chien_dich=instance, sdt__in=chung).delete()
            if n:
                ghi_log(f"Xoá chiến dịch “{instance}”: bỏ {n} số đã có sẵn ở danh sách Chung (không để trùng).", "warn")
        pre_delete.connect(xoa_chien_dich, sender=ChienDich, weak=False, dispatch_uid="xoa-chien-dich")

        # Xoá bộ nhóm: tắt các lịch hẹn gửi theo bộ đó (không để lịch gửi nhầm vào nhóm đang chọn)
        def xoa_bo_nhom(instance, **kwargs):
            n = LichHen.objects.filter(bo_nhom=instance, dang_bat=True).update(
                dang_bat=False, ket_qua_cuoi=f"Đã tắt: bộ nhóm “{instance.ten}” bị xoá"[:255])
            if n:
                # không gắn tài khoản: bộ nhóm có thể đang bị xoá theo tài khoản
                ghi_log(f"Bộ nhóm “{instance.ten}” bị xoá: đã tắt {n} lịch hẹn gửi theo bộ này.", "warn")
        pre_delete.connect(xoa_bo_nhom, sender=BoNhom, weak=False, dispatch_uid="xoa-bo-nhom")


class ZaloAdminConfig(AdminConfig):
    """Dùng trang quản trị riêng (có bảng điều khiển) thay cho trang mặc định của Django."""
    default_site = "guitin.admin_site.ZaloAdminSite"
