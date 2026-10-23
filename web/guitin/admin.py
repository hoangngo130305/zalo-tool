from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.db.models import Count, OuterRef, Subquery
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html

from . import services as sv
from .models import AnhTinNhan, CaiDat, KetQua, LichSuGui, LichSuGuiNhom, NguoiDung, NguoiNhan, NhatKy


# ---------- tài khoản ----------
class TaoTaiKhoanForm(BaseUserCreationForm):
    class Meta:
        model = NguoiDung
        fields = ("email", "ho_ten", "vai_tro")


class SuaTaiKhoanForm(UserChangeForm):
    class Meta:
        model = NguoiDung
        fields = ("email", "ho_ten", "vai_tro", "is_active")


def con_quan_tri_khac(user):
    return NguoiDung.objects.filter(vai_tro=NguoiDung.QUAN_TRI, is_active=True).exclude(pk=user.pk).exists()


@admin.register(NguoiDung)
class NguoiDungAdmin(UserAdmin):
    form = SuaTaiKhoanForm
    add_form = TaoTaiKhoanForm
    ordering = ("vai_tro", "ho_ten")
    list_display = ("ho_ten", "email", "vai_tro_pill", "so_duoc_giao", "is_active", "last_login")
    list_filter = ("vai_tro", "is_active")
    search_fields = ("email", "ho_ten")
    fieldsets = (
        ("Thông tin", {"fields": ("email", "ho_ten", "password")}),
        ("Tin nhắn", {"fields": ("tin_nhan_mac_dinh",)}),
        ("Phân quyền", {"fields": ("vai_tro", "is_active"),
                        "description": "Quản trị: toàn quyền, vào được trang này. Nhân viên: chỉ thấy và gửi các số được giao. "
                                       "Chỉ xem: xem tiến độ và báo cáo, không sửa được gì. Bỏ chọn “Hoạt động” để khoá tài khoản."}),
        ("Thời gian", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "ho_ten", "vai_tro", "password1", "password2")}),
    )
    readonly_fields = ("last_login", "date_joined")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(n_giao=Count("nguoi_nhan"))

    @admin.display(description="Vai trò", ordering="vai_tro")
    def vai_tro_pill(self, obj):
        mau = {"admin": "danger", "nhanvien": "primary", "xem": "secondary"}[obj.vai_tro]
        return format_html('<span class="badge badge-{}">{}</span>', mau, obj.get_vai_tro_display())

    @admin.display(description="Số được giao", ordering="n_giao")
    def so_duoc_giao(self, obj):
        if not obj.n_giao:
            return "–"
        url = reverse("admin:guitin_nguoinhan_changelist") + f"?phu_trach__id__exact={obj.pk}"
        return format_html('<a href="{}">{}</a>', url, obj.n_giao)

    def save_model(self, request, obj, form, change):
        if change and obj.pk == request.user.pk and (obj.vai_tro != NguoiDung.QUAN_TRI or not obj.is_active):
            messages.error(request, "Không thể tự hạ quyền hoặc tự khoá tài khoản đang dùng. Thay đổi vai trò / trạng thái không được lưu.")
            obj.vai_tro, obj.is_active = NguoiDung.QUAN_TRI, True
        super().save_model(request, obj, form, change)
        sv.ghi_log(f"{request.user} đã {'cập nhật' if change else 'tạo'} tài khoản {obj.email} ({obj.get_vai_tro_display()}).",
                   nguoi_dung=request.user)

    def delete_model(self, request, obj):
        if obj.pk == request.user.pk:
            messages.error(request, "Không thể tự xoá tài khoản đang dùng.")
            return
        if obj.vai_tro == NguoiDung.QUAN_TRI and not con_quan_tri_khac(obj):
            messages.error(request, "Phải còn ít nhất một tài khoản quản trị.")
            return
        sv.ghi_log(f"{request.user} đã xoá tài khoản {obj.email}. Các số người này phụ trách chuyển về “chưa giao”.", "warn", request.user)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)


# ---------- người nhận ----------
class TrangThaiFilter(admin.SimpleListFilter):
    title = "trạng thái"
    parameter_name = "trang_thai"

    def lookups(self, request, model_admin):
        return [("cho", "Chờ gửi"), ("da_gui", "Đã gửi"), ("loi", "Lỗi"), ("so_sai", "Số sai")]

    def queryset(self, request, qs):
        v = self.value()
        if v == "so_sai":
            return qs.filter(sdt__isnull=True)
        if v == "cho":
            return qs.filter(sdt__isnull=False, kq_tin_nhan__isnull=True)
        if v == "da_gui":
            return qs.filter(kq_tin_nhan__isnull=False).exclude(kq_tin_nhan="Lỗi")
        if v == "loi":
            return qs.filter(kq_tin_nhan="Lỗi")
        return qs


class NhapDanhSachForm(forms.Form):
    tep = forms.FileField(label="File Excel / CSV", required=False,
                          help_text="File .xlsx, .xls hoặc .csv. Có dòng tên cột: SĐT (hoặc Số điện thoại / Phone), Họ tên, "
                                    "và Nội dung (không bắt buộc).")
    dan = forms.CharField(label="Hoặc dán danh sách", required=False, widget=forms.Textarea(attrs={"rows": 8}),
                          help_text="Mỗi dòng một người: SĐT, Họ tên, Nội dung tin nhắn. Có thể copy thẳng 2–3 cột từ Excel.")
    phu_trach = forms.ModelChoiceField(label="Giao cho nhân viên", required=False, queryset=NguoiDung.objects.none(),
                                       empty_label="Chưa giao (giao sau)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phu_trach"].queryset = NguoiDung.objects.filter(
            is_active=True, vai_tro__in=[NguoiDung.NHAN_VIEN, NguoiDung.QUAN_TRI]).order_by("vai_tro", "ho_ten")

    def clean(self):
        d = super().clean()
        if not d.get("tep") and not (d.get("dan") or "").strip():
            raise forms.ValidationError("Chọn file hoặc dán danh sách.")
        return d


@admin.register(NguoiNhan)
class NguoiNhanAdmin(admin.ModelAdmin):
    change_list_template = "admin/guitin/nguoinhan/change_list.html"
    list_display = ("sdt_goc", "ten", "noi_dung_ngan", "phu_trach", "trang_thai", "tao_luc")
    list_editable = ("phu_trach",)
    list_filter = ("phu_trach", TrangThaiFilter)
    search_fields = ("sdt_goc", "sdt", "ten", "noi_dung")
    list_per_page = 100
    fields = ("sdt_goc", "ten", "noi_dung", "phu_trach", "thu_tu")

    @admin.display(description="Nội dung riêng")
    def noi_dung_ngan(self, obj):
        if not obj.noi_dung:
            return format_html('<span class="text-muted">(mặc định)</span>')
        return (obj.noi_dung[:50] + "…") if len(obj.noi_dung) > 50 else obj.noi_dung
    actions = ["chia_deu", "gui_lai", "bo_giao"]

    def get_queryset(self, request):
        kq = KetQua.objects.filter(sdt=OuterRef("sdt"))
        return super().get_queryset(request).select_related("phu_trach").annotate(
            kq_tin_nhan=Subquery(kq.values("tin_nhan")[:1]), kq_ket_ban=Subquery(kq.values("ket_ban")[:1]))

    @admin.display(description="Trạng thái")
    def trang_thai(self, obj):
        if not obj.sdt:
            return format_html('<span class="badge badge-danger">Số sai</span>')
        if obj.kq_tin_nhan is None:
            return format_html('<span class="badge badge-info">Chờ gửi</span>')
        if obj.kq_tin_nhan == "Lỗi":
            return format_html('<span class="badge badge-warning">Lỗi</span>')
        return format_html('<span class="badge badge-success">Đã gửi</span>')

    # Thêm thao tác "Giao cho <nhân viên>" cho từng người (chỉ hiện với người có quyền sửa)
    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self.has_change_permission(request):
            return actions
        for u in sv.nguoi_duoc_giao():
            def giao(modeladmin, req, qs, u=u):
                n = qs.update(phu_trach=u)
                sv.tang_phien_ban()
                sv.ghi_log(f"{req.user} đã giao {n} số cho {u}.", "ok", req.user)
                modeladmin.message_user(req, f"Đã giao {n} số cho {u}.")
            name = f"giao_{u.pk}"
            actions[name] = (giao, name, f"Giao các số đã chọn cho {u}")
        return actions

    # ---------- trang nhập danh sách ----------
    def get_urls(self):
        from django.urls import path
        return [path("nhap/", self.admin_site.admin_view(self.nhap_view), name="guitin_nguoinhan_nhap")] + super().get_urls()

    def nhap_view(self, request):
        from django.core.exceptions import PermissionDenied
        from django.template.response import TemplateResponse
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = NhapDanhSachForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            try:
                rows = sv.doc_file(form.cleaned_data["tep"].read()) if form.cleaned_data["tep"] else []
                rows += sv.doc_van_ban(form.cleaned_data["dan"])
                if not rows:
                    raise ValueError("Không đọc được số nào. Kiểm tra lại file hoặc nội dung đã dán.")
                nv = form.cleaned_data["phu_trach"]
                them, trung = sv.nhap_them(rows, nv)
                giao = f", giao cho {nv}" if nv else " (chưa giao cho ai)"
                sv.ghi_log(f"{request.user} đã nhập {them} số{giao}.", "ok", request.user)
                self.message_user(request, f"Đã thêm {them} số{giao}." + (f" Bỏ qua {trung} số đã có trong danh sách." if trung else ""))
                return redirect(reverse("admin:guitin_nguoinhan_changelist"))
            except ValueError as e:
                form.add_error(None, str(e))
        ctx = {**self.admin_site.each_context(request), "title": "Nhập danh sách người nhận", "form": form,
               "opts": self.model._meta}
        return TemplateResponse(request, "admin/guitin/nguoinhan/nhap.html", ctx)

    @admin.action(description="Bỏ giao (chuyển về “chưa giao”)", permissions=["change"])
    def bo_giao(self, request, qs):
        n = qs.update(phu_trach=None)
        sv.tang_phien_ban()
        self.message_user(request, f"Đã bỏ giao {n} số.")

    @admin.action(description="Chia đều TẤT CẢ số chưa giao cho nhân viên", permissions=["change"])
    def chia_deu(self, request, qs):
        try:
            n, so_nv = sv.chia_deu()
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
            return
        sv.ghi_log(f"{request.user} đã chia {n} số chưa giao cho {so_nv} nhân viên.", "ok", request.user)
        self.message_user(request, f"Đã chia {n} số chưa giao cho {so_nv} nhân viên.")

    @admin.action(description="Gửi lại các số đã chọn (xoá kết quả cũ)", permissions=["change"])
    def gui_lai(self, request, qs):
        n, _ = KetQua.objects.filter(sdt__in=qs.exclude(sdt__isnull=True).values("sdt")).delete()
        sv.tang_phien_ban()
        sv.ghi_log(f"{request.user} đánh dấu {n} số để gửi lại.", "warn", request.user)
        self.message_user(request, f"Đã đánh dấu {n} số để gửi lại ở lần chạy sau.")

    def save_model(self, request, obj, form, change):
        if not change:
            from django.db.models import Max
            obj.thu_tu = obj.thu_tu or (NguoiNhan.objects.aggregate(m=Max("thu_tu"))["m"] or 0) + 1
        super().save_model(request, obj, form, change)


# ---------- kết quả ----------
@admin.register(KetQua)
class KetQuaAdmin(admin.ModelAdmin):
    list_display = ("sdt", "ten", "ten_zalo", "ket_ban_pill", "tin_nhan_pill", "thoi_gian", "nguoi_gui", "ghi_chu_ngan")
    list_filter = ("tin_nhan", "ket_ban", "nguoi_gui", ("thoi_gian", admin.DateFieldListFilter))
    search_fields = ("sdt", "ten", "ten_zalo", "noi_dung")
    list_per_page = 100
    actions = ["xuat_excel", "gui_lai"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @staticmethod
    def _pill(v):
        mau = {"Đã gửi": "success", "Bỏ qua": "secondary", "Lỗi": "danger"}.get(v, "light")
        return format_html('<span class="badge badge-{}">{}</span>', mau, v)

    @admin.display(description="Kết bạn", ordering="ket_ban")
    def ket_ban_pill(self, obj):
        return self._pill(obj.ket_ban)

    @admin.display(description="Tin nhắn", ordering="tin_nhan")
    def tin_nhan_pill(self, obj):
        return self._pill(obj.tin_nhan)

    @admin.display(description="Ghi chú")
    def ghi_chu_ngan(self, obj):
        return (obj.ghi_chu[:60] + "…") if len(obj.ghi_chu) > 60 else obj.ghi_chu

    @admin.action(description="Xuất Excel các dòng đã chọn")
    def xuat_excel(self, request, qs):
        import io

        from django.http import HttpResponse
        from django.utils import timezone
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["SĐT", "Tên", "Tên Zalo", "Kết bạn", "Tin nhắn", "Ghi chú", "Thời gian", "Người bấm gửi"])
        for k in qs.select_related("nguoi_gui"):
            ws.append([k.sdt, k.ten, k.ten_zalo, k.ket_ban, k.tin_nhan, k.ghi_chu,
                       timezone.localtime(k.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), str(k.nguoi_gui or "")])
        buf = io.BytesIO()
        wb.save(buf)
        res = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        res["Content-Disposition"] = 'attachment; filename="ket-qua.xlsx"'
        return res

    @admin.action(description="Gửi lại các số đã chọn (xoá kết quả)", permissions=["delete"])
    def gui_lai(self, request, qs):
        n, _ = qs.delete()
        sv.tang_phien_ban()
        self.message_user(request, f"Đã xoá {n} kết quả, các số này sẽ được gửi lại ở lần chạy sau.")


# ---------- lịch sử gửi (chỉ xem) ----------
class KetQuaGuiFilter(admin.SimpleListFilter):
    title = "kết quả"
    parameter_name = "ket_qua"

    def lookups(self, request, model_admin):
        return [("ok", "Thành công"), ("loi", "Lỗi")]

    def queryset(self, request, qs):
        from django.db.models import Q
        ok = Q(tin_nhan="Đã gửi") | Q(tin_nhan="-", ket_ban="Đã gửi")
        if self.value() == "ok":
            return qs.filter(ok)
        if self.value() == "loi":
            return qs.exclude(ok)
        return qs


@admin.register(LichSuGui)
class LichSuGuiAdmin(admin.ModelAdmin):
    list_display = ("thoi_gian", "sdt", "ten", "ten_zalo", "ket_qua_pill", "noi_dung_ngan", "nguoi_gui", "tai_khoan_zalo")
    list_filter = (KetQuaGuiFilter, "nguoi_gui", ("thoi_gian", admin.DateFieldListFilter))
    search_fields = ("sdt", "ten", "ten_zalo", "noi_dung")
    list_per_page = 100
    actions = ["xuat_excel"]
    readonly_fields = ("thoi_gian", "sdt", "ten", "ten_zalo", "ket_ban", "tin_nhan", "noi_dung", "ghi_chu", "nguoi_gui", "tai_khoan_zalo")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Kết quả")
    def ket_qua_pill(self, obj):
        if obj.thanh_cong:
            return format_html('<span class="badge badge-success">Thành công</span>')
        return format_html('<span class="badge badge-danger" title="{}">Lỗi</span>', obj.ghi_chu)

    @admin.display(description="Nội dung đã gửi")
    def noi_dung_ngan(self, obj):
        return (obj.noi_dung[:60] + "…") if len(obj.noi_dung) > 60 else obj.noi_dung

    @admin.action(description="Xuất Excel các dòng đã chọn")
    def xuat_excel(self, request, qs):
        from django.http import HttpResponse
        res = HttpResponse(sv.excel_lich_su(qs), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        res["Content-Disposition"] = 'attachment; filename="lich-su-gui.xlsx"'
        return res


@admin.register(LichSuGuiNhom)
class LichSuGuiNhomAdmin(admin.ModelAdmin):
    list_display = ("thoi_gian", "ten_nhom", "ket_qua_pill", "noi_dung_ngan", "so_anh", "nguoi_gui", "tai_khoan_zalo")
    list_filter = ("ket_qua", "nguoi_gui", ("thoi_gian", admin.DateFieldListFilter))
    search_fields = ("ten_nhom", "nhom_id", "noi_dung")
    list_per_page = 100
    readonly_fields = ("thoi_gian", "nhom_id", "ten_nhom", "ket_qua", "noi_dung", "so_anh", "ghi_chu", "nguoi_gui", "tai_khoan_zalo")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Kết quả", ordering="ket_qua")
    def ket_qua_pill(self, obj):
        if obj.thanh_cong:
            return format_html('<span class="badge badge-success">Đã gửi</span>')
        return format_html('<span class="badge badge-danger" title="{}">{}</span>', obj.ghi_chu, obj.ket_qua)

    @admin.display(description="Nội dung đã gửi")
    def noi_dung_ngan(self, obj):
        return (obj.noi_dung[:60] + "…") if len(obj.noi_dung) > 60 else obj.noi_dung


# ---------- cài đặt (1 dòng) ----------
class AnhTinNhanInline(admin.TabularInline):
    model = AnhTinNhan
    extra = 1
    max_num = AnhTinNhan.TOI_DA
    fields = ("xem", "tep", "thu_tu")
    readonly_fields = ("xem",)
    verbose_name_plural = "Hình ảnh gửi kèm tin nhắn"

    @admin.display(description="Xem")
    def xem(self, obj):
        if not obj.pk:
            return "–"
        url = reverse("anh-tin-nhan", args=[obj.pk])
        return format_html('<a href="{}" target="_blank"><img src="{}" style="height:72px;border-radius:6px"></a>', url, url)


@admin.register(CaiDat)
class CaiDatAdmin(admin.ModelAdmin):
    inlines = [AnhTinNhanInline]
    fieldsets = (
        ("Nội dung", {"fields": ("loi_moi_ket_ban", "noi_dung_tin_nhan")}),
        ("Gửi gì", {"fields": ("gui_ket_ban", "gui_tin_nhan")}),
        ("Tên gợi nhớ khi kết bạn", {"fields": ("doi_ten_goi_nho", "mau_ten_goi_nho"),
                                     "description": "Sau khi gửi lời mời kết bạn, tool đặt tên gợi nhớ cho người đó trong Zalo của người gửi."}),
        ("Giới hạn an toàn", {"fields": ("gioi_han_moi_lan", "cho_toi_thieu_giay", "cho_toi_da_giay"),
                              "description": "Nên giữ 20–30 người mỗi ngày và chờ từ 60 giây trở lên để tránh bị Zalo khoá tài khoản. "
                                             "Thời gian chờ giữa 2 lần gửi được chọn ngẫu nhiên trong khoảng tối thiểu – tối đa, "
                                             "áp dụng cho cả gửi từng người lẫn gửi vào nhóm."}),
        ("Nghỉ giữa các lượt", {"fields": ("nghi_sau_so_lan", "nghi_phut"),
                                "description": "Ví dụ: 5 lần và 15 phút = cứ gửi xong 5 người (hoặc 5 nhóm) thì nghỉ 15 phút rồi gửi tiếp. "
                                               "Để 0 lần là không nghỉ."}),
    )

    def has_add_permission(self, request):
        return not CaiDat.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    # Bấm vào "Cài đặt gửi tin" là mở thẳng trang sửa
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:guitin_caidat_change", args=[CaiDat.lay().pk]))


# ---------- nhật ký (chỉ xem) ----------
@admin.register(NhatKy)
class NhatKyAdmin(admin.ModelAdmin):
    list_display = ("thoi_gian", "muc_pill", "noi_dung", "nguoi_dung")
    list_filter = ("muc", "nguoi_dung", ("thoi_gian", admin.DateFieldListFilter))
    search_fields = ("noi_dung",)
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Mức", ordering="muc")
    def muc_pill(self, obj):
        mau = {"ok": "success", "warn": "warning", "error": "danger"}.get(obj.muc, "secondary")
        return format_html('<span class="badge badge-{}">{}</span>', mau, obj.get_muc_display())
