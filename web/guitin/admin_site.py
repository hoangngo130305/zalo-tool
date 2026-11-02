from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from . import services as sv


class ZaloAdminSite(admin.AdminSite):
    """Trang quản trị: trang chủ là bảng điều khiển (Zalo, gửi tin, thống kê theo nhân viên, báo cáo, nhật ký)."""
    site_header = "Zalo Tool – Quản trị"
    site_title = "Zalo Tool"
    index_title = "Bảng điều khiển"
    index_template = "admin/dieu_khien.html"

    def index(self, request, extra_context=None):
        u = request.user
        ctx = {
            "thong_ke": sv.thong_ke(),
            "nguoi_giao": sv.nguoi_duoc_giao(),
            "co_quyen_zalo": u.co_quyen("send"),
            "co_quyen_gui": u.co_quyen("send"),
            "me_id": u.id,
            **(extra_context or {}),
        }
        return super().index(request, ctx)

    def get_urls(self):
        return [path("tinh-trang/", self.admin_view(self.tinh_trang), name="tinh_trang")] + super().get_urls()

    def tinh_trang(self, request):
        """Tình trạng hệ thống: worker, database, ổ đĩa, sao lưu, phiên Zalo, hàng đợi kết quả, lịch tự động."""
        from .tinh_trang import kiem_tra
        ctx = {**self.each_context(request), "title": "Tình trạng hệ thống", "muc": kiem_tra()}
        return TemplateResponse(request, "admin/tinh_trang.html", ctx)
