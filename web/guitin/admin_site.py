from django.contrib import admin

from . import services as sv


class ZaloAdminSite(admin.AdminSite):
    """Trang quản trị: trang chủ là bảng điều khiển (Zalo, gửi tin, thống kê theo nhân viên, nhật ký)."""
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
