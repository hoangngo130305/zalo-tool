from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm
from django.db.models import Count, OuterRef, Subquery
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from . import services as sv
from .models import (AnhTinNhan, BoNhom, CaiDat, ChienDich, DotGui, KetQua, KhachHang, KhongLienHe, LichHen, LichSuGui,
                     LichSuGuiNhom, MauTinNhan, NguoiDung, NguoiNhan, NhatKy, q_thanh_cong)


# ---------- tài khoản ----------
class TaoTaiKhoanForm(BaseUserCreationForm):
    class Meta:
        model = NguoiDung
        fields = ("email", "ho_ten", "vai_tro", "doi")


class SuaTaiKhoanForm(UserChangeForm):
    class Meta:
        model = NguoiDung
        fields = ("email", "ho_ten", "vai_tro", "is_active", "doi", "so_dien_thoai")


def con_quan_tri_khac(user):
    return NguoiDung.objects.filter(vai_tro=NguoiDung.QUAN_TRI, is_active=True).exclude(pk=user.pk).exists()


@admin.register(NguoiDung)
class NguoiDungAdmin(UserAdmin):
    form = SuaTaiKhoanForm
    add_form = TaoTaiKhoanForm
    ordering = ("vai_tro", "ho_ten")
    list_display = ("ho_ten", "email", "vai_tro_pill", "doi", "so_duoc_giao", "tam_dung", "is_active", "last_login")
    list_filter = ("vai_tro", "doi", "is_active", "tu_cai_thoi_gian")
    search_fields = ("email", "ho_ten")
    fieldsets = (
        ("Thông tin", {"fields": ("email", "ho_ten", "password", "so_dien_thoai")}),
        ("Tin nhắn", {"fields": ("tin_nhan_mac_dinh", "loi_moi_ket_ban")}),
        ("Giới hạn & tạm dừng gửi", {"fields": ("gioi_han_ngay", "tam_dung_den"),
                                     "description": "Bỏ tạm dừng: xoá trống ô “Tạm dừng gửi đến” rồi Lưu, hoặc ở danh sách Tài khoản "
                                                    "tick tài khoản → chọn thao tác “Bỏ tạm dừng gửi”."}),
        ("Thời gian gửi riêng", {"fields": ("tu_cai_thoi_gian", "cho_toi_thieu_giay", "cho_toi_da_giay", "nghi_sau_so_lan", "nghi_phut"),
                                 "description": "Tick “Được tự cài thời gian gửi” để nhân viên tự đặt trên trang gửi tin (khung “Thời gian gửi của tôi”). "
                                                "Các ô để trống = theo Cài đặt gửi tin. Bỏ tick thì luôn dùng Cài đặt gửi tin."}),
        ("Phân quyền", {"fields": ("vai_tro", "doi", "is_active"),
                        "description": "Quản trị: toàn quyền, vào được trang này. Trưởng nhóm: dùng trang gửi tin như nhân viên, "
                                       "thêm trang “Đội của tôi” để xem và giao số cho nhân viên cùng đội (cùng tên đội). "
                                       "Nhân viên: chỉ thấy và gửi các số được giao. Chỉ xem: xem tiến độ và báo cáo, không sửa được gì. "
                                       "Bỏ chọn “Hoạt động” để khoá tài khoản."}),
        ("Thời gian", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "ho_ten", "vai_tro", "doi", "password1", "password2")}),
    )
    readonly_fields = ("last_login", "date_joined")
    actions = ["bo_tam_dung"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(n_giao=Count("nguoi_nhan"))

    @admin.display(description="Tạm dừng gửi", ordering="tam_dung_den")
    def tam_dung(self, obj):
        if not obj.tam_dung_den or obj.tam_dung_den <= timezone.now():
            return "–"
        return format_html('<span class="badge badge-danger">đến {}</span>', timezone.localtime(obj.tam_dung_den).strftime("%H:%M %d/%m"))

    @admin.action(description="Bỏ tạm dừng gửi (cho Zalo gửi lại ngay)")
    def bo_tam_dung(self, request, queryset):
        qs = queryset.filter(tam_dung_den__gt=timezone.now())
        ten = ", ".join(str(u) for u in qs)
        n = qs.update(tam_dung_den=None)
        if not n:
            messages.info(request, "Các tài khoản đã chọn không bị tạm dừng.")
            return
        sv.ghi_log(f"{request.user} đã bỏ tạm dừng gửi cho: {ten}.", "warn", request.user)
        messages.success(request, f"Đã bỏ tạm dừng cho {n} tài khoản. Nên kiểm tra Zalo trên điện thoại và gửi ít trước khi chạy đợt lớn.")

    @admin.display(description="Vai trò", ordering="vai_tro")
    def vai_tro_pill(self, obj):
        mau = {"admin": "danger", "truongnhom": "warning", "nhanvien": "primary", "xem": "secondary"}[obj.vai_tro]
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
        sv.tang_phien_ban([obj.id])     # trang gửi tin của người này tải lại (quyền / thời gian gửi riêng)
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
        return [("cho", "Chờ gửi"), ("da_gui", "Đã gửi"), ("loi", "Lỗi"), ("so_sai", "Số sai"), ("tu_choi", "Khách từ chối")]

    def queryset(self, request, qs):
        v = self.value()
        if v == "so_sai":
            return qs.filter(sdt__isnull=True)
        if v == "tu_choi":
            return qs.filter(tu_choi=True)
        if v == "cho":
            return sv.loc_cho_gui(qs, CaiDat.lay())
        if v == "da_gui":
            return qs.filter(sv._da_gui_dong(True))
        if v == "loi":
            return qs.filter(sv._da_gui_dong()).exclude(sv._da_gui_dong(True))
        return qs


class NhapDanhSachForm(forms.Form):
    tep = forms.FileField(label="File Excel / CSV", required=False,
                          help_text="File .xlsx, .xls hoặc .csv. Có dòng tên cột: SĐT (hoặc Số điện thoại / Phone), Họ tên, "
                                    "và Nội dung (không bắt buộc).")
    dan = forms.CharField(label="Hoặc dán danh sách", required=False, widget=forms.Textarea(attrs={"rows": 8}),
                          help_text="Mỗi dòng một người: SĐT, Họ tên, Nội dung tin nhắn. Có thể copy thẳng 2–4 cột từ Excel "
                                    "(SĐT, Họ tên, Nội dung, Dự án).")
    phu_trach = forms.ModelChoiceField(label="Giao cho nhân viên", required=False, queryset=NguoiDung.objects.none(),
                                       empty_label="Chưa giao (giao sau)")
    chien_dich = forms.ModelChoiceField(label="Chiến dịch", required=False, queryset=ChienDich.objects.all(),
                                        empty_label="Chung (không thuộc chiến dịch nào)",
                                        help_text="Một khách có thể nằm trong nhiều chiến dịch; số trùng chỉ tính trong cùng chiến dịch.")
    nguon = forms.CharField(label="Nguồn danh sách", required=True, max_length=255,
                            help_text="Danh sách lấy từ đâu (bắt buộc, để biết nguồn gốc dữ liệu khách).")
    du_an = forms.CharField(label="Dự án", required=False, max_length=100,
                            help_text="Ví dụ: Palm City. Ghi vào tên gợi nhớ của các số trong lần nhập này "
                                      "(dòng nào trong file có cột Dự án thì dùng giá trị của dòng đó).")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phu_trach"].queryset = NguoiDung.objects.filter(
            is_active=True, vai_tro__in=[NguoiDung.NHAN_VIEN, NguoiDung.TRUONG_NHOM, NguoiDung.QUAN_TRI]).order_by("vai_tro", "ho_ten")

    def clean(self):
        d = super().clean()
        if not d.get("tep") and not (d.get("dan") or "").strip() and not self.data.get("ma_tam"):
            raise forms.ValidationError("Chọn file hoặc dán danh sách.")
        return d


@admin.register(NguoiNhan)
class NguoiNhanAdmin(admin.ModelAdmin):
    change_list_template = "admin/guitin/nguoinhan/change_list.html"
    list_display = ("sdt_goc", "ten", "du_an", "chien_dich", "noi_dung_ngan", "phu_trach", "trang_thai", "tao_luc")
    list_editable = ("phu_trach",)
    list_filter = ("phu_trach", "chien_dich", "du_an", TrangThaiFilter, "quan_tri_giao", "nguon")
    search_fields = ("sdt_goc", "sdt", "ten", "noi_dung", "du_an", "nguon")
    list_per_page = 100
    fields = ("sdt_goc", "ten", "xung_ho", "du_an", "chien_dich", "noi_dung", "phu_trach", "quan_tri_giao", "nguon", "them", "thu_tu")

    @admin.display(description="Nội dung riêng")
    def noi_dung_ngan(self, obj):
        if not obj.noi_dung:
            return format_html('<span class="text-muted">(mặc định)</span>')
        return (obj.noi_dung[:50] + "…") if len(obj.noi_dung) > 50 else obj.noi_dung
    actions = ["chia_deu", "gui_lai", "bo_giao", "xoa_du_lieu_khach"]

    def get_queryset(self, request):
        from django.db.models import Exists
        kq = KetQua.objects.filter(sdt=OuterRef("sdt"))
        return super().get_queryset(request).select_related("phu_trach", "chien_dich").annotate(
            kq_tin_nhan=Subquery(kq.values("tin_nhan")[:1]), kq_ket_ban=Subquery(kq.values("ket_ban")[:1]),
            tu_choi=Exists(KhongLienHe.objects.filter(sdt=OuterRef("sdt"))))

    @admin.display(description="Trạng thái")
    def trang_thai(self, obj):
        from .models import thanh_cong
        if not obj.sdt:
            return format_html('<span class="badge badge-danger">Số sai</span>')
        if obj.chien_dich_id:      # số trong chiến dịch: kết quả theo lịch sử của chiến dịch đó
            k = sv.ket_qua_cac_dong([obj]).get(obj.id)
            obj.kq_tin_nhan, obj.kq_ket_ban = (k.tin_nhan, k.ket_ban) if k else (None, None)
        if obj.kq_tin_nhan is None:
            if obj.tu_choi:
                return format_html('<span class="badge badge-dark">Khách từ chối</span>')
            return format_html('<span class="badge badge-info">Chờ gửi</span>')
        if not thanh_cong(obj.kq_ket_ban, obj.kq_tin_nhan):
            return format_html('<span class="badge badge-warning">Lỗi</span>')
        return format_html('<span class="badge badge-success">Đã gửi</span>')

    # Thêm thao tác "Giao cho <nhân viên>" cho từng người (chỉ hiện với người có quyền sửa)
    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self.has_change_permission(request):
            return actions
        for u in sv.nguoi_duoc_giao():
            def giao(modeladmin, req, qs, u=u):
                n = qs.update(phu_trach=u, quan_tri_giao=True)
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
        """Nhập danh sách 2 bước: chọn file / dán -> xem trước (đếm số hợp lệ, trùng, từ chối; chọn cột) -> nhập."""
        import json
        import uuid

        from django.conf import settings
        from django.core.exceptions import PermissionDenied
        from django.template.response import TemplateResponse
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = NhapDanhSachForm(request.POST or None, request.FILES or None)
        ctx = {**self.admin_site.each_context(request), "title": "Nhập danh sách người nhận", "form": form,
               "opts": self.model._meta}
        tam = settings.ROOT_DIR / "data" / "tam-nhap"
        if request.method == "POST" and form.is_valid():
            try:
                ma = request.POST.get("ma_tam") or ""
                if ma:          # bước 2: file đã lưu tạm ở bước 1
                    if not ma.replace("-", "").isalnum():
                        raise ValueError("Phiên nhập không hợp lệ, hãy chọn file lại.")
                    f = tam / f"{ma}.json"
                    if not f.is_file():
                        raise ValueError("Phiên nhập đã hết hạn, hãy chọn file lại.")
                    du_lieu = json.loads(f.read_text(encoding="utf-8"))
                else:
                    du_lieu = {"tep": form.cleaned_data["tep"].read().hex() if form.cleaned_data["tep"] else "",
                               "dan": form.cleaned_data["dan"] or ""}
                    ma = uuid.uuid4().hex
                    tam.mkdir(parents=True, exist_ok=True)
                    for cu in tam.glob("*.json"):       # dọn file tạm cũ hơn 1 ngày
                        if cu.stat().st_mtime < __import__("time").time() - 86400:
                            cu.unlink(missing_ok=True)
                    (tam / f"{ma}.json").write_text(json.dumps(du_lieu), encoding="utf-8")
                data = bytes.fromhex(du_lieu["tep"]) if du_lieu["tep"] else b""
                ghep = {k: request.POST.get(f"cot_{k}") for k, _ in sv.COT_NHAP if f"cot_{k}" in request.POST}
                rows = sv.doc_file(data, ghep or None) if data else []
                rows += sv.doc_van_ban(du_lieu["dan"])
                if not rows:
                    raise ValueError("Không đọc được số nào. Kiểm tra lại file hoặc nội dung đã dán.")
                cd = form.cleaned_data["chien_dich"]
                if request.POST.get("buoc") != "nhap":
                    pt = sv.phan_tich_file(data) if data else None
                    if pt and ghep:
                        pt["ghep"].update({k: int(v) for k, v in ghep.items() if v not in (None, "")})
                        pt["ghep"].update({k: None for k, v in ghep.items() if v in (None, "")})
                    ctx.update(xem_truoc=sv.xem_truoc_nhap(rows, cd), ma_tam=ma, mau=rows[:5],
                               tieu_de=pt["tieu_de"] if pt else None,
                               ghep=[(k, ten, pt["ghep"].get(k)) for k, ten in sv.COT_NHAP] if pt else None)
                    return TemplateResponse(request, "admin/guitin/nguoinhan/nhap.html", ctx)
                nv = form.cleaned_data["phu_trach"]
                du_an = form.cleaned_data["du_an"].strip()
                kq = sv.nhap_them(rows, nv, du_an, cd, form.cleaned_data["nguon"])
                (tam / f"{ma}.json").unlink(missing_ok=True)
                giao = (f", giao cho {nv}" if nv else " (chưa giao cho ai)") + (f", dự án {du_an}" if du_an else "") \
                    + (f", chiến dịch {cd}" if cd else "")
                sv.ghi_log(f"{request.user} đã nhập {kq['them']} số{giao}, nguồn: {form.cleaned_data['nguon']}.", "ok", request.user)
                them = [f"Đã thêm {kq['them']} số{giao}."]
                if kq["trung"]:
                    them.append(f"Bỏ qua {kq['trung']} số đã có trong danh sách (của bạn hoặc nhân viên khác).")
                if kq["tuChoi"]:
                    them.append(f"Bỏ qua {kq['tuChoi']} số khách đã từ chối liên hệ.")
                if kq["daGui"]:
                    them.append(f"Lưu ý: {kq['daGui']} số mới thêm đã từng được gửi trước đây.")
                self.message_user(request, " ".join(them))
                return redirect(reverse("admin:guitin_nguoinhan_changelist"))
            except ValueError as e:
                form.add_error(None, str(e))
        return TemplateResponse(request, "admin/guitin/nguoinhan/nhap.html", ctx)

    @admin.action(description="Bỏ giao (chuyển về “chưa giao”)", permissions=["change"])
    def bo_giao(self, request, qs):
        n = qs.update(phu_trach=None, quan_tri_giao=False)
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

    @admin.action(description="Xoá dữ liệu của các khách đã chọn (khách yêu cầu)", permissions=["delete"])
    def xoa_du_lieu_khach(self, request, qs):
        n = sv.xoa_du_lieu_khach(set(qs.exclude(sdt__isnull=True).values_list("sdt", flat=True)), request.user)
        self.message_user(request, f"Đã xoá dữ liệu của {n} khách và đưa vào danh sách khách từ chối liên hệ.")

    def delete_model(self, request, obj):
        sv.ghi_log(f"{request.user} đã xoá người nhận {obj.sdt_goc} {obj.ten} (trang quản trị).", "warn", request.user)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        n = queryset.count()
        ds = ", ".join(queryset.values_list("sdt_goc", flat=True)[:20])
        super().delete_queryset(request, queryset)
        sv.ghi_log(f"{request.user} đã xoá {n} người nhận (trang quản trị): {ds}{'…' if n > 20 else ''}.", "warn", request.user)
        sv.tang_phien_ban()

    @admin.action(description="Gửi lại các số đã chọn (xoá kết quả cũ)", permissions=["change"])
    def gui_lai(self, request, qs):
        from django.utils import timezone
        qs.filter(chien_dich__isnull=False).update(gui_lai_tu=timezone.now())
        n, _ = KetQua.objects.filter(sdt__in=qs.filter(chien_dich__isnull=True).exclude(sdt__isnull=True).values("sdt")).delete()
        sv.tang_phien_ban()
        sv.ghi_log(f"{request.user} đánh dấu {n} số để gửi lại.", "warn", request.user)
        self.message_user(request, f"Đã đánh dấu {n} số để gửi lại ở lần chạy sau.")

    def save_model(self, request, obj, form, change):
        if not change:
            from django.db.models import Max
            obj.thu_tu = obj.thu_tu or (NguoiNhan.objects.aggregate(m=Max("thu_tu"))["m"] or 0) + 1
        # Quản trị giao / đổi người phụ trách (kể cả sửa nhanh ở danh sách) thì nhân viên không xoá được số này
        if "phu_trach" in form.changed_data and "quan_tri_giao" not in form.changed_data:
            obj.quan_tri_giao = obj.phu_trach_id is not None
        super().save_model(request, obj, form, change)
        sv.tang_phien_ban()


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
            sv.dong_excel(ws, [k.sdt, k.ten, k.ten_zalo, k.ket_ban, k.tin_nhan, k.ghi_chu,
                               timezone.localtime(k.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), str(k.nguoi_gui or "")])
        buf = io.BytesIO()
        wb.save(buf)
        sv.ghi_log(f"{request.user} đã xuất Excel {qs.count()} kết quả gửi (trang quản trị).", nguoi_dung=request.user)
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
        ok = q_thanh_cong()
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
        sv.ghi_log(f"{request.user} đã xuất Excel {qs.count()} dòng lịch sử gửi (trang quản trị).", nguoi_dung=request.user)
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


class CaiDatForm(forms.ModelForm):
    gmail_mat_khau = forms.CharField(
        label="Mật khẩu ứng dụng của Gmail", required=False, widget=forms.PasswordInput(render_value=False),
        help_text=CaiDat._meta.get_field("gmail_mat_khau").help_text)

    class Meta:
        model = CaiDat
        fields = "__all__"

    def clean_gmail_mat_khau(self):
        # Để trống khi lưu thì giữ mật khẩu cũ (ô mật khẩu không hiện lại giá trị đã lưu)
        return (self.cleaned_data.get("gmail_mat_khau") or "").replace(" ", "") or self.instance.gmail_mat_khau


@admin.register(CaiDat)
class CaiDatAdmin(admin.ModelAdmin):
    form = CaiDatForm
    change_form_template = "admin/guitin/caidat/change_form.html"
    inlines = [AnhTinNhanInline]
    fieldsets = (
        ("Nội dung", {"fields": ("loi_moi_ket_ban", "noi_dung_tin_nhan")}),
        ("Gửi gì", {"fields": ("gui_ket_ban", "gui_tin_nhan")}),
        ("Tên gợi nhớ khi kết bạn", {"fields": ("doi_ten_goi_nho", "mau_ten_goi_nho", "ten_du_an"),
                                     "description": "Sau khi gửi lời mời kết bạn, tool đặt tên gợi nhớ cho người đó trong Zalo của người gửi."}),
        ("Giới hạn an toàn", {"fields": ("gioi_han_moi_ngay", "gioi_han_moi_lan", "cho_toi_thieu_giay", "cho_toi_da_giay",
                                         "dung_khi_loi_lien_tiep", "tam_dung_gio"),
                              "description": "Nên giữ 20–30 người mỗi ngày và chờ từ 60 giây trở lên để tránh bị Zalo khoá tài khoản. "
                                             "Thời gian chờ giữa 2 lần gửi được chọn ngẫu nhiên trong khoảng tối thiểu – tối đa, "
                                             "áp dụng cho cả gửi từng người lẫn gửi vào nhóm."}),
        ("Khung giờ gửi", {"fields": ("gio_bat_dau", "gio_ket_thuc", "nghi_chu_nhat"),
                           "description": "Giờ Việt Nam. Ngoài khung giờ tool tự tạm nghỉ (đợt đang gửi không bị huỷ), "
                                          "đến giờ thì gửi tiếp. Áp dụng cho cả gửi từng người lẫn gửi vào nhóm."}),
        ("Chăm sóc tự động", {"fields": ("theo_doi_phan_hoi", "gui_tin_khi_dong_y", "tin_khi_dong_y",
                                         "nhac_lai_sau_ngay", "noi_dung_nhac_lai", "khong_nhan_lai_ngay", "tu_de_spam"),
                              "description": "Tool theo dõi khách đồng ý kết bạn và khách nhắn lại (không lưu nội dung khách nhắn). "
                                             "Khi khách đồng ý kết bạn thì tự gửi tin; khách chưa trả lời sau N ngày thì tự nhắc lại một lần. "
                                             "Chiến dịch có thể đặt nội dung riêng."}),
        ("Gửi vào nhóm", {"fields": ("nhom_cho_toi_thieu_giay", "nhom_cho_toi_da_giay", "nhom_nghi_sau_so_lan", "nhom_nghi_phut"),
                          "description": "Thời gian chờ / nghỉ riêng khi gửi vào nhóm Zalo. Để 0 là dùng chung với gửi từng người."}),
        ("Quyền xuất dữ liệu", {"fields": ("nhan_vien_xuat_excel",)}),
        ("Báo cáo qua email", {"fields": ("bao_cao_tan_suat", "bao_cao_gio", "bao_cao_thu", "bao_cao_email_nhan",
                                          "gmail_gui", "gmail_mat_khau"),
                               "description": "Tool tự gửi file Excel báo cáo (tổng quan, theo nhân viên, dự án, chiến dịch, "
                                              "lịch sử gửi) cho quản lý: cuối ngày (báo cáo trong ngày) hoặc cuối tuần (7 ngày). "
                                              "Lưu xong bấm “Gửi thử báo cáo ngay” ở góc trên để kiểm tra."}),
        ("Nghỉ giữa các lượt", {"fields": ("nghi_sau_so_lan", "nghi_phut"),
                                "description": "Ví dụ: 5 lần và 15 phút = cứ gửi xong 5 người (hoặc 5 nhóm) thì nghỉ 15 phút rồi gửi tiếp. "
                                               "Để 0 lần là không nghỉ."}),
    )

    def has_add_permission(self, request):
        return not CaiDat.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if form.changed_data:       # nhật ký thao tác: ai đổi cài đặt gì
            ten = ", ".join(str(form.fields[f].label) for f in form.changed_data if f in form.fields)
            sv.ghi_log(f"{request.user} đã đổi cài đặt gửi tin: {ten}.", "warn", request.user)

    # Bấm vào "Cài đặt gửi tin" là mở thẳng trang sửa
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:guitin_caidat_change", args=[CaiDat.lay().pk]))

    def get_urls(self):
        from django.urls import path
        return [path("gui-thu-bao-cao/", self.admin_site.admin_view(self.gui_thu_bao_cao), name="guitin_caidat_gui_thu")] \
            + super().get_urls()

    def gui_thu_bao_cao(self, request):
        """Gửi thử email báo cáo ngay (theo cài đặt đã lưu)."""
        from django.core.exceptions import PermissionDenied
        from django.http import JsonResponse

        from .bao_cao_email import gui_bao_cao
        if request.method != "POST" or not self.has_change_permission(request):
            raise PermissionDenied
        try:
            nhan = gui_bao_cao()
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        sv.ghi_log(f"{request.user} đã gửi thử email báo cáo cho {', '.join(nhan)}.", "ok", request.user)
        return JsonResponse({"ok": f"Đã gửi báo cáo tới {', '.join(nhan)}. Kiểm tra hộp thư (cả mục Spam)."})


# ---------- khách từ chối liên hệ ----------
@admin.register(KhongLienHe)
class KhongLienHeAdmin(admin.ModelAdmin):
    list_display = ("sdt", "ly_do", "nguoi_them", "thoi_gian")
    search_fields = ("sdt", "ly_do")
    list_filter = ("nguoi_them", ("thoi_gian", admin.DateFieldListFilter))
    fields = ("sdt", "ly_do")
    list_per_page = 100

    def get_readonly_fields(self, request, obj=None):
        return ("sdt",) if obj else ()

    def save_model(self, request, obj, form, change):
        if not change:
            obj.nguoi_them = request.user
        super().save_model(request, obj, form, change)
        sv.tang_phien_ban()
        sv.ghi_log(f"{request.user} đã thêm {obj.sdt} vào danh sách khách từ chối liên hệ.", "warn", request.user)

    def delete_model(self, request, obj):
        sv.ghi_log(f"{request.user} đã bỏ {obj.sdt} khỏi danh sách khách từ chối liên hệ.", "warn", request.user)
        super().delete_model(request, obj)
        sv.tang_phien_ban()

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.delete_model(request, obj)


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


# ---------- chiến dịch ----------
@admin.register(ChienDich)
class ChienDichAdmin(admin.ModelAdmin):
    list_display = ("ten", "du_an", "dang_chay", "bat_dau", "ket_thuc", "so_khach", "so_da_gui", "so_dong_y", "so_tra_loi")
    list_filter = ("dang_chay",)
    search_fields = ("ten", "du_an")
    actions = ["xuat_excel"]
    fieldsets = (
        ("Thông tin chung", {"fields": ("ten", "du_an", "dang_chay", "bat_dau", "ket_thuc")}),
        ("Nội dung (để trống thì dùng của nhân viên / cài đặt chung)", {"fields": ("loi_moi", "noi_dung")}),
        ("Chăm sóc tiếp", {"fields": ("tin_khi_dong_y", "nhac_sau_ngay", "noi_dung_nhac"),
                           "description": "Chuỗi chăm sóc: ngày 0 kết bạn + gửi tin; khi khách đồng ý kết bạn thì gửi “Tin khi đồng ý”; "
                                          "sau N ngày khách chưa trả lời thì gửi “Tin nhắc lại” một lần."}),
    )

    def _ls(self, obj):
        return LichSuGui.objects.filter(chien_dich=obj, loai=LichSuGui.LAN_DAU)

    @admin.display(description="Khách")
    def so_khach(self, obj):
        return obj.nguoi_nhan.count()

    @admin.display(description="Đã gửi (thành công)")
    def so_da_gui(self, obj):
        ls = self._ls(obj)
        return f"{ls.values('sdt').distinct().count()} ({ls.filter(q_thanh_cong()).values('sdt').distinct().count()})"

    @admin.display(description="Đồng ý kết bạn")
    def so_dong_y(self, obj):
        return KhachHang.objects.filter(sdt__in=self._ls(obj).values("sdt"), dong_y_luc__isnull=False).count()

    @admin.display(description="Trả lời")
    def so_tra_loi(self, obj):
        return KhachHang.objects.filter(sdt__in=self._ls(obj).values("sdt"), tra_loi_luc__isnull=False).count()

    @admin.action(description="Xuất Excel báo cáo các chiến dịch đã chọn")
    def xuat_excel(self, request, qs):
        from django.http import HttpResponse
        from django.utils.timezone import localtime
        from openpyxl import Workbook
        wb = Workbook()
        wb.remove(wb.active)
        for cd in qs:
            ws = wb.create_sheet(cd.ten[:28] or f"CD{cd.id}")
            ws.append(["SĐT", "Họ tên", "Nhân viên", "Gửi lúc", "Kết bạn", "Tin nhắn", "Đồng ý kết bạn", "Trả lời", "Trạng thái khách"])
            kh = {k.sdt: k for k in KhachHang.objects.filter(sdt__in=cd.nguoi_nhan.values("sdt"))}
            gui = {}
            for h in self._ls(cd).order_by("thoi_gian"):
                gui[h.sdt] = h
            for r in cd.nguoi_nhan.select_related("phu_trach"):
                h, k = gui.get(r.sdt), kh.get(r.sdt)
                sv.dong_excel(ws, [r.sdt_goc, r.ten, str(r.phu_trach or ""),
                                   localtime(h.thoi_gian).strftime("%d/%m/%Y %H:%M") if h else "", h.ket_ban if h else "",
                                   h.tin_nhan if h else "", localtime(k.dong_y_luc).strftime("%d/%m/%Y %H:%M") if k and k.dong_y_luc else "",
                                   localtime(k.tra_loi_luc).strftime("%d/%m/%Y %H:%M") if k and k.tra_loi_luc else "",
                                   k.get_trang_thai_display() if k else ""])
        import io
        buf = io.BytesIO()
        wb.save(buf)
        sv.ghi_log(f"{request.user} đã xuất Excel báo cáo {qs.count()} chiến dịch.", nguoi_dung=request.user)
        res = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        res["Content-Disposition"] = 'attachment; filename="bao-cao-chien-dich.xlsx"'
        return res


# ---------- khách hàng (CRM) ----------
class PhanHoiFilter(admin.SimpleListFilter):
    title = "phản hồi"
    parameter_name = "phan_hoi"

    def lookups(self, request, model_admin):
        return [("dong_y", "Đã đồng ý kết bạn"), ("tra_loi", "Đã trả lời"), ("chua", "Chưa phản hồi")]

    def queryset(self, request, qs):
        v = self.value()
        if v == "dong_y":
            return qs.filter(dong_y_luc__isnull=False)
        if v == "tra_loi":
            return qs.filter(tra_loi_luc__isnull=False)
        if v == "chua":
            return qs.filter(dong_y_luc__isnull=True, tra_loi_luc__isnull=True)
        return qs


@admin.register(KhachHang)
class KhachHangAdmin(admin.ModelAdmin):
    list_display = ("sdt", "ten_zalo", "trang_thai", "nhan_vien", "gui_dau_luc", "dong_y_luc", "tra_loi_luc", "da_nhac_lai")
    list_editable = ("trang_thai",)
    list_filter = ("trang_thai", PhanHoiFilter, "nhan_vien", "da_nhac_lai")
    search_fields = ("sdt", "ten_zalo", "ghi_chu")
    readonly_fields = ("sdt", "zalo_uid", "ten_zalo", "gui_dau_luc", "dong_y_luc", "tra_loi_luc", "so_lan_tra_loi", "cap_nhat")
    fields = ("sdt", "ten_zalo", "trang_thai", "nhan_vien", "ghi_chu", "gui_dau_luc", "dong_y_luc", "tra_loi_luc",
              "so_lan_tra_loi", "da_nhac_lai", "zalo_uid", "cap_nhat")
    list_per_page = 100
    actions = ["xoa_du_lieu_khach"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Xoá dữ liệu của các khách đã chọn (khách yêu cầu)", permissions=["delete"])
    def xoa_du_lieu_khach(self, request, qs):
        n = sv.xoa_du_lieu_khach(set(qs.values_list("sdt", flat=True)), request.user)
        self.message_user(request, f"Đã xoá dữ liệu của {n} khách và đưa vào danh sách khách từ chối liên hệ.")


# ---------- đợt gửi, lịch hẹn, bộ nhóm, mẫu tin ----------
@admin.register(DotGui)
class DotGuiAdmin(admin.ModelAdmin):
    list_display = ("id", "nguoi_gui", "loai", "trang_thai", "da_gui", "tong", "bat_dau", "ket_thuc", "ghi_chu")
    list_filter = ("trang_thai", "loai", "nguoi_gui")
    readonly_fields = ("nguoi_gui", "loai", "bat_dau", "ket_thuc", "tong", "da_gui", "trang_thai", "danh_sach", "ghi_chu")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LichHen)
class LichHenAdmin(admin.ModelAdmin):
    list_display = ("nguoi", "loai", "mo_ta_lich", "bo_nhom", "dang_bat", "lan_chay_cuoi", "ket_qua_cuoi")
    list_filter = ("loai", "lap", "dang_bat", "nguoi")
    list_editable = ("dang_bat",)
    fields = ("nguoi", "loai", "lap", "luc", "gio", "thu", "bo_nhom", "dang_bat", "lan_chay_cuoi", "ket_qua_cuoi")
    readonly_fields = ("lan_chay_cuoi", "ket_qua_cuoi")

    @admin.display(description="Lịch")
    def mo_ta_lich(self, obj):
        return obj.mo_ta()


@admin.register(BoNhom)
class BoNhomAdmin(admin.ModelAdmin):
    list_display = ("ten", "nguoi", "so_nhom")
    list_filter = ("nguoi",)

    @admin.display(description="Số nhóm")
    def so_nhom(self, obj):
        return len(obj.nhom or [])


@admin.register(MauTinNhan)
class MauTinNhanAdmin(admin.ModelAdmin):
    list_display = ("ten", "loai", "da_duyet", "nguoi_tao", "canh_bao", "tao_luc")
    list_filter = ("loai", "da_duyet")
    list_editable = ("da_duyet",)
    search_fields = ("ten", "noi_dung")
    fields = ("ten", "loai", "noi_dung", "da_duyet")
    actions = ["duyet"]

    @admin.display(description="Cảnh báo")
    def canh_bao(self, obj):
        cb = sv.canh_bao_noi_dung(obj.noi_dung, obj.loai, CaiDat.lay().cac_tu_spam())
        return format_html('<span class="text-warning">{}</span>', "; ".join(cb)) if cb else "–"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.nguoi_tao = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Duyệt các mẫu đã chọn")
    def duyet(self, request, qs):
        n = qs.update(da_duyet=True)
        self.message_user(request, f"Đã duyệt {n} mẫu.")
