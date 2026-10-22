from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from guitin import views as v

admin.site.site_header = "Zalo Tool – Quản trị"
admin.site.site_title = "Zalo Tool"
admin.site.index_title = "Quản trị Zalo Tool"

urlpatterns = [
    path("", v.trang_chu, name="trang-chu"),
    path("dang-nhap/", v.DangNhapView.as_view(), name="dang-nhap"),
    path("dang-xuat/", auth_views.LogoutView.as_view(), name="dang-xuat"),
    path("doi-mat-khau/", auth_views.PasswordChangeView.as_view(
        template_name="registration/doi_mat_khau.html", success_url="/doi-mat-khau/xong/"), name="doi-mat-khau"),
    path("doi-mat-khau/xong/", auth_views.PasswordChangeDoneView.as_view(
        template_name="registration/doi_mat_khau_xong.html"), name="doi-mat-khau-xong"),
    path("admin/", admin.site.urls),

    path("api/state", v.api_state),
    path("api/poll", v.api_poll),
    path("api/thong-ke", v.api_thong_ke),
    path("api/contacts", v.api_contacts),
    path("api/tin-nhan-cua-toi", v.api_tin_nhan_cua_toi),
    path("api/import", v.api_import),
    path("api/preview", v.api_preview),
    path("api/reset", v.api_reset),
    path("api/zalo/login", v.api_zalo_login),
    path("api/zalo/login/cancel", v.api_zalo_cancel),
    path("api/zalo/logout", v.api_zalo_logout),
    path("api/zalo/tat-ca", v.api_zalo_tat_ca),
    path("api/start", v.api_start),
    path("api/stop", v.api_stop),
    path("api/report", v.api_report),
    path("api/lich-su", v.api_lich_su),
    path("file-mau.xlsx", v.file_mau, name="file-mau"),
    path("api/lich-su/excel", v.api_lich_su_excel),
    path("api/worker/event", v.api_worker_event),

    path("gui-nhom/", v.trang_gui_nhom, name="gui-nhom"),
    path("api/nhom/state", v.api_nhom_state),
    path("api/nhom/danh-sach", v.api_nhom_danh_sach),
    path("api/nhom/cau-hinh", v.api_nhom_cau_hinh),
    path("api/nhom/anh", v.api_nhom_anh),
    path("api/nhom/anh/xoa", v.api_nhom_anh_xoa),
    path("api/nhom/anh/<str:ten>", v.xem_anh_nhom, name="anh-nhom"),
    path("api/nhom/gui", v.api_nhom_gui),
    path("api/anh-tin-nhan/<int:pk>", v.xem_anh_tin_nhan, name="anh-tin-nhan"),
    path("api/nhom/lich-su", v.api_nhom_lich_su),
]
