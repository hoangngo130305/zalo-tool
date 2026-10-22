from django.contrib.admin.apps import AdminConfig
from django.apps import AppConfig


class GuiTinConfig(AppConfig):
    name = "guitin"
    verbose_name = "Gửi tin Zalo"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from .models import AnhTinNhan, CaiDat, KetQua, NguoiDung, NguoiNhan
        from .services import tang_phien_ban

        # Sửa trong trang quản trị thì trang gửi tin đang mở cũng tải lại
        def doi(**kwargs):
            tang_phien_ban()

        for model in (NguoiNhan, KetQua, NguoiDung, CaiDat, AnhTinNhan):
            post_save.connect(doi, sender=model, dispatch_uid=f"pb-save-{model.__name__}")
            post_delete.connect(doi, sender=model, dispatch_uid=f"pb-del-{model.__name__}")


class ZaloAdminConfig(AdminConfig):
    """Dùng trang quản trị riêng (có bảng điều khiển) thay cho trang mặc định của Django."""
    default_site = "guitin.admin_site.ZaloAdminSite"
