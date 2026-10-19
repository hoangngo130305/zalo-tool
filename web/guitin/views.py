import hmac
import io
import json
from datetime import datetime
from functools import wraps

from django import forms
from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import services as sv
from . import worker
from .auth import con_bi_khoa
from .models import CaiDat, CauHinhNhom, KetQua, LichSuGui, LichSuGuiNhom, NguoiDung, NhatKy, chuan_hoa_sdt


# ---------- trang ----------
class DangNhapForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
    error_messages = {
        "invalid_login": "Sai email hoặc mật khẩu.",
        "inactive": "Tài khoản này đã bị khoá.",
    }

    def clean(self):
        phut = con_bi_khoa(self.request)
        if phut:
            raise forms.ValidationError(f"Sai quá nhiều lần, thử lại sau {phut} phút.")
        try:
            return super().clean()
        except PermissionDenied:
            raise forms.ValidationError(f"Sai quá nhiều lần, thử lại sau {con_bi_khoa(self.request) or 5} phút.") from None


class DangNhapView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = DangNhapForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        res = super().form_valid(form)
        u = self.request.user
        sv.ghi_log(f"{u} ({u.get_vai_tro_display()}) đã đăng nhập.", nguoi_dung=u)
        return res


@login_required
def trang_chu(request):
    """Quản trị / chỉ xem làm việc trong trang quản trị; nhân viên dùng trang gửi tin riêng."""
    u = request.user
    if u.is_staff:
        return redirect("admin:index")
    me = {"id": u.id, "name": str(u), "email": u.email, "role": u.vai_tro, "roleLabel": u.get_vai_tro_display(),
          "perms": sorted(u.quyen)}
    return render(request, "guitin/nhan_vien.html", {"me": me})


# ---------- khung API ----------
def api(quyen="view", methods=("GET", "POST")):
    """Bắt buộc đăng nhập + có quyền; trả lỗi dạng JSON để trang hiển thị."""
    def deco(fn):
        @wraps(fn)
        @require_http_methods(list(methods))
        def wrapper(request, *args, **kwargs):
            u = request.user
            if not u.is_authenticated:
                return JsonResponse({"error": "Phiên đăng nhập đã hết, hãy đăng nhập lại"}, status=401)
            if not u.co_quyen(quyen):
                return JsonResponse({"error": "Tài khoản của bạn không có quyền làm việc này"}, status=403)
            try:
                if request.method == "POST" and request.content_type == "application/json":
                    request.json = json.loads(request.body or b"{}")
                else:
                    request.json = {}
                out = fn(request, *args, **kwargs)
                return out if isinstance(out, HttpResponse) else JsonResponse(out)
            except (ValueError, worker.WorkerLoi) as e:
                return JsonResponse({"error": str(e)}, status=400)
        return wrapper
    return deco


def trang_thai_cho(user):
    """Trạng thái Zalo của chính người đang đăng nhập (mỗi người một tài khoản Zalo riêng)."""
    return worker.trang_thai(user.id)


def staff_cho(user):
    return sv.staff_dict(sv.nguoi_duoc_giao()) if user.co_quyen("all") else []


def can_khong_dang_gui(user):
    if worker.trang_thai(user.id).get("running"):
        raise ValueError("Đang gửi, hãy dừng hoặc chờ xong rồi làm tiếp")


def nhat_ky_cho(user):
    """Nhân viên chỉ thấy nhật ký của mình (thao tác của mình và các đợt gửi mình bấm)."""
    qs = NhatKy.objects.all()
    return qs if user.co_quyen("all") else qs.filter(nguoi_dung=user)


# ---------- API ----------
@api("view", ["GET"])
def api_state(request):
    logs = list(nhat_ky_cho(request.user).order_by("-id")[:300])
    return {
        **trang_thai_cho(request.user),
        "config": CaiDat.lay().to_dict(),
        "tinNhanCuaToi": request.user.tin_nhan_mac_dinh,
        "contacts": sv.danh_sach(request.user),
        "staff": staff_cho(request.user),
        "logs": [sv.log_dict(n) for n in reversed(logs)],
        "version": sv.phien_ban(),
    }


@api("view", ["GET"])
def api_poll(request):
    """Trang gọi mỗi 1,5 giây: trạng thái gửi, nhật ký mới, và phiên bản danh sách."""
    try:
        sau = int(request.GET.get("log") or 0)
    except ValueError:
        sau = 0
    logs = nhat_ky_cho(request.user).filter(id__gt=sau).order_by("id")[:200]
    return {**trang_thai_cho(request.user), "logs": [sv.log_dict(n) for n in logs], "version": sv.phien_ban()}


@api("all", ["GET"])
def api_thong_ke(request):
    """Số liệu cho bảng điều khiển trong trang quản trị."""
    return {**sv.thong_ke(), "version": sv.phien_ban()}


@api("view", ["GET", "POST"])
def api_contacts(request):
    if request.method == "POST":
        if not request.user.co_quyen("list"):
            return JsonResponse({"error": "Tài khoản của bạn không có quyền sửa danh sách"}, status=403)
        can_khong_dang_gui(request.user)
        rows = request.json.get("contacts") or []
        if not isinstance(rows, list):
            raise ValueError("Dữ liệu không hợp lệ")
        sv.luu_danh_sach(request.user, rows)
    return {"contacts": sv.danh_sach(request.user), "staff": staff_cho(request.user), "config": CaiDat.lay().to_dict(),
            "tinNhanCuaToi": request.user.tin_nhan_mac_dinh, "version": sv.phien_ban()}


@api("send", ["POST"])
def api_tin_nhan_cua_toi(request):
    """Nhân viên tự sửa tin nhắn mặc định gửi hàng loạt của mình (để trống = dùng nội dung của quản trị)."""
    u = request.user
    u.tin_nhan_mac_dinh = str(request.json.get("noiDung") or "").strip()[:5000]
    u.save(update_fields=["tin_nhan_mac_dinh"])
    sv.tang_phien_ban()
    return {"tinNhanCuaToi": u.tin_nhan_mac_dinh, "version": sv.phien_ban()}


@api("list", ["POST"])
def api_import(request):
    return {"rows": sv.doc_file(request.body)}


@api("view", ["POST"])
def api_preview(request):
    cfg = CaiDat.lay()
    items = sv.cho_gui(request.user, request.json.get("phamVi"), cfg.gioi_han_moi_lan)
    return {"items": [{
        "sdt": r.sdt, "ten": r.ten,
        "loiMoi": sv.dien(cfg.loi_moi_ket_ban, r.ten) if cfg.gui_ket_ban else None,
        "tenGoiNho": sv.ten_goi_nho(cfg.mau_ten_goi_nho, r.ten, r.sdt) if cfg.doi_ten_goi_nho else None,
        "tinNhan": sv.noi_dung_gui(r, cfg) if cfg.gui_tin_nhan else None,
    } for r in items]}


@api("send", ["POST"])
def api_reset(request):
    """Gửi lại 1 số (nhân viên chỉ với số của mình), hoặc xoá toàn bộ lịch sử (chỉ quản trị)."""
    can_khong_dang_gui(request.user)
    u = request.user
    sdt = request.json.get("sdt")
    if not sdt:
        if not u.co_quyen("settings"):
            raise ValueError("Chỉ quản trị viên được xoá toàn bộ lịch sử gửi")
        KetQua.objects.all().delete()
        sv.ghi_log(f"{u} đã xoá toàn bộ lịch sử gửi.", "warn", u)
    else:
        phone = chuan_hoa_sdt(sdt)
        if not u.co_quyen("all") and not u.nguoi_nhan.filter(sdt=phone).exists():
            raise ValueError("Số này không thuộc danh sách của bạn")
        KetQua.objects.filter(sdt=phone).delete()
        sv.ghi_log(f"{u} đánh dấu {phone} để gửi lại.", "warn", u)
    sv.tang_phien_ban()
    return {"contacts": sv.danh_sach(u), "version": sv.phien_ban()}


# ---------- Zalo của từng người ----------
def _chu_tai_khoan(request):
    """Tài khoản Zalo được thao tác: mặc định của chính mình; quản trị thao tác hộ được người khác."""
    uid = request.json.get("uid")
    if uid and int(uid) != request.user.id:
        if not request.user.co_quyen("settings"):
            raise ValueError("Bạn chỉ thao tác được với Zalo của chính mình")
        return NguoiDung.objects.get(id=int(uid))
    return request.user


@api("send", ["POST"])
def api_zalo_login(request):
    worker.goi("POST", "/login", {"uid": request.user.id, "qr": bool(request.json.get("qr"))})
    return {"ok": True}


@api("send", ["POST"])
def api_zalo_cancel(request):
    worker.goi("POST", "/login/cancel", {"uid": request.user.id})
    return {"ok": True}


@api("send", ["POST"])
def api_zalo_logout(request):
    chu = _chu_tai_khoan(request)
    worker.goi("POST", "/logout", {"uid": chu.id})
    ai = "Zalo của mình" if chu == request.user else f"Zalo của {chu}"
    sv.ghi_log(f"{request.user} đã ngắt kết nối {ai}.", nguoi_dung=request.user)
    return {"ok": True}


@api("all", ["GET"])
def api_zalo_tat_ca(request):
    """Zalo của từng nhân viên: đã kết nối chưa, đang gửi tới đâu (cho bảng điều khiển quản trị)."""
    st = worker.tat_ca()
    out = []
    for u in sv.nguoi_duoc_giao():
        s = (st or {}).get(u.id, {})
        out.append({"id": u.id, "name": str(u), "role": u.vai_tro, "login": s.get("login", "out"),
                    "account": s.get("account"), "running": bool(s.get("running")), "progress": s.get("progress"),
                    "current": s.get("current"), "stopRequested": bool(s.get("stopRequested"))})
    return {"worker": st is not None, "nhanVien": out}


@api("send", ["POST"])
def api_start(request):
    """Gửi bằng Zalo của chính người bấm gửi."""
    u = request.user
    if worker.trang_thai(u.id).get("login") != "in":
        raise ValueError("Bạn chưa kết nối Zalo. Quét QR để kết nối Zalo của bạn trước khi gửi")
    cfg = CaiDat.lay()
    if not cfg.gui_ket_ban and not cfg.gui_tin_nhan:
        raise ValueError("Đang tắt cả kết bạn lẫn tin nhắn, hãy bật ít nhất một mục")
    batch = sv.cho_gui(u, request.json.get("phamVi"), cfg.gioi_han_moi_lan)
    if not batch:
        raise ValueError("Không còn ai cần gửi trong phạm vi này")
    worker.goi("POST", "/run", {
        "uid": u.id,
        # Gửi kèm nội dung đã điền sẵn cho từng người (nội dung riêng hoặc mặc định)
        "batch": [{"sdt": r.sdt, "ten": r.ten, "noiDung": sv.noi_dung_gui(r, cfg)} for r in batch],
        "cfg": cfg.to_dict(),
        "runBy": {"id": u.id, "name": str(u)},
    })
    return {"ok": True}


@api("send", ["POST"])
def api_stop(request):
    """Dừng đợt gửi của mình; quản trị dừng hộ được đợt gửi của nhân viên."""
    chu = _chu_tai_khoan(request)
    worker.goi("POST", "/stop", {"uid": chu.id})
    ai = "" if chu == request.user else f" đợt gửi của {chu}"
    sv.ghi_log(f"{request.user} bấm dừng{ai}.", "warn", request.user)
    return {"ok": True}


@api("view", ["GET"])
def api_report(request):
    """Báo cáo Excel. Nhân viên chỉ tải được kết quả các số của mình."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    qs = KetQua.objects.select_related("nguoi_gui").order_by("thoi_gian")
    if not request.user.co_quyen("all"):
        qs = qs.filter(sdt__in=request.user.nguoi_nhan.exclude(sdt__isnull=True).values("sdt"))
    wb = Workbook()
    ws = wb.active
    ws.title = "KetQua"
    head = ["SĐT", "Tên trong danh sách", "Tên Zalo", "Kết bạn", "Tin nhắn", "Ghi chú", "Thời gian", "Người bấm gửi"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F5FC4")
    for k in qs:
        ws.append([k.sdt, k.ten, k.ten_zalo, k.ket_ban, k.tin_nhan, k.ghi_chu,
                   timezone.localtime(k.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), str(k.nguoi_gui or "")])
    for col, w in zip("ABCDEFGH", (13, 24, 24, 12, 12, 45, 20, 20)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    res = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    res["Content-Disposition"] = f'attachment; filename="ket-qua-{timezone.localdate():%Y%m%d}.xlsx"'
    return res


@login_required
def file_mau(request):
    """File Excel mẫu để nhân viên điền: SĐT, Họ tên, Nội dung."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "DanhSach"
    ws.append(["SĐT", "Họ tên", "Nội dung"])
    ws.append(["0901234567", "Anh Nam", "Chào {ten}, em gửi anh thông tin chương trình tháng này ạ."])
    ws.append(["0912345678", "Chị Lan", ""])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F5FC4")
    for r in (2, 3):
        ws.cell(r, 1).number_format = "@"   # giữ số 0 đầu
        ws.cell(r, 3).alignment = Alignment(wrap_text=True, vertical="top")
    ws["E1"] = "Ghi chú"
    ws["E2"] = "{ten} sẽ được thay bằng họ tên. Để trống Nội dung thì gửi nội dung mặc định."
    ws["E1"].font = Font(bold=True)
    for col, w in zip("ABCDE", (14, 22, 60, 2, 50)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    res = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    res["Content-Disposition"] = 'attachment; filename="mau-danh-sach.xlsx"'
    return res


# ---------- lịch sử gửi ----------
def _bo_loc(request):
    g = request.GET
    return {k: (g.get(k) or "").strip() for k in ("q", "tt", "tu", "den", "nguoi")}


@api("view", ["GET"])
def api_lich_su(request):
    """Lịch sử gửi có lọc & phân trang. Nhân viên chỉ thấy lịch sử mình gửi."""
    qs = sv.loc_lich_su(request.user, **_bo_loc(request))
    try:
        trang = max(1, int(request.GET.get("trang") or 1))
    except ValueError:
        trang = 1
    co = 50
    tong = qs.count()
    from django.db.models import Q
    ok = qs.filter(Q(tin_nhan="Đã gửi") | Q(tin_nhan="-", ket_ban="Đã gửi")).count()
    rows = qs[(trang - 1) * co: trang * co]
    return {"rows": [sv.lich_su_dict(h) for h in rows], "tong": tong, "thanhCong": ok, "loi": tong - ok,
            "trang": trang, "soTrang": max(1, (tong + co - 1) // co)}


@api("view", ["GET"])
def api_lich_su_excel(request):
    """Xuất Excel đúng theo bộ lọc đang chọn."""
    data = sv.excel_lich_su(sv.loc_lich_su(request.user, **_bo_loc(request)))
    res = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    res["Content-Disposition"] = f'attachment; filename="lich-su-gui-{timezone.localdate():%Y%m%d}.xlsx"'
    return res


# ---------- worker báo kết quả về ----------
@csrf_exempt
@require_http_methods(["POST"])
def api_worker_event(request):
    got = request.headers.get("X-Worker-Token", "")
    if not hmac.compare_digest(got, settings.WORKER_TOKEN):
        return JsonResponse({"error": "Sai mã worker"}, status=403)
    b = json.loads(request.body or b"{}")
    if b.get("type") == "log":
        NhatKy.objects.create(noi_dung=str(b.get("text", ""))[:5000], muc=b.get("level") or "info",
                              nguoi_dung=NguoiDung.objects.filter(id=b.get("nguoiDungId")).first())
    elif b.get("type") == "result":
        sdt = chuan_hoa_sdt(b.get("sdt"))
        r = b.get("ketQua") or {}
        if sdt:
            try:
                tg = datetime.fromisoformat(str(r.get("thoiGian")).replace("Z", "+00:00"))
            except ValueError:
                tg = timezone.now()
            du_lieu = {
                "ten": str(r.get("ten") or "")[:255], "ten_zalo": str(r.get("tenZalo") or "")[:255],
                "ket_ban": str(r.get("ketBan") or "-")[:20], "tin_nhan": str(r.get("tinNhan") or "-")[:20],
                "noi_dung": str(r.get("noiDung") or ""), "ghi_chu": str(r.get("ghiChu") or ""), "thoi_gian": tg,
                "tai_khoan_zalo": str(r.get("taiKhoanZalo") or "")[:255],
                "nguoi_gui": NguoiDung.objects.filter(id=b.get("nguoiGuiId")).first(),
            }
            # Trạng thái mới nhất của số này + một dòng lịch sử (không ghi đè)
            KetQua.objects.update_or_create(sdt=sdt, defaults=du_lieu)
            LichSuGui.objects.create(sdt=sdt, **du_lieu)
            sv.tang_phien_ban()
    elif b.get("type") == "result_nhom":
        r = b.get("ketQua") or {}
        try:
            tg = datetime.fromisoformat(str(r.get("thoiGian")).replace("Z", "+00:00"))
        except ValueError:
            tg = timezone.now()
        LichSuGuiNhom.objects.create(
            nhom_id=str(b.get("nhomId") or "")[:64], ten_nhom=str(r.get("tenNhom") or "")[:255],
            ket_qua=str(r.get("ketQua") or "-")[:20], noi_dung=str(r.get("noiDung") or ""),
            so_anh=max(0, int(r.get("soAnh") or 0)), ghi_chu=str(r.get("ghiChu") or ""), thoi_gian=tg,
            tai_khoan_zalo=str(r.get("taiKhoanZalo") or "")[:255],
            nguoi_gui=NguoiDung.objects.filter(id=b.get("nguoiGuiId")).first(),
        )
        sv.tang_phien_ban()
    return JsonResponse({"ok": True})


# ---------- gửi vào nhóm Zalo ----------
@login_required
def trang_gui_nhom(request):
    """Trang gửi tin vào nhóm Zalo, dùng chung cho quản trị và nhân viên (gửi bằng Zalo của chính mình)."""
    u = request.user
    if not u.co_quyen("send"):
        raise PermissionDenied
    me = {"id": u.id, "name": str(u), "role": u.vai_tro, "roleLabel": u.get_vai_tro_display(), "staff": u.is_staff}
    return render(request, "guitin/gui_nhom.html", {"me": me})


@api("send", ["GET"])
def api_nhom_state(request):
    c = CauHinhNhom.cua(request.user)
    logs = list(nhat_ky_cho(request.user).filter(nguoi_dung=request.user).order_by("-id")[:200])
    return {**trang_thai_cho(request.user), "config": CaiDat.lay().to_dict(), "cauHinh": sv.cau_hinh_nhom_dict(c),
            "anhToiDa": sv.ANH_TOI_DA, "anhToiDaMb": sv.ANH_TOI_DA_MB,
            "logs": [sv.log_dict(n) for n in reversed(logs)], "version": sv.phien_ban()}


@api("send", ["GET"])
def api_nhom_danh_sach(request):
    """Các nhóm Zalo mà tài khoản Zalo của mình đang tham gia."""
    return worker.goi("GET", f"/groups?uid={request.user.id}", timeout=60)


@api("send", ["POST"])
def api_nhom_cau_hinh(request):
    """Lưu nhóm đích và nội dung gửi nhóm."""
    can_khong_dang_gui(request.user)
    c = CauHinhNhom.cua(request.user)
    b = request.json
    if "noiDung" in b:
        c.noi_dung = str(b.get("noiDung") or "").strip()[:5000]
    if "nhom" in b:
        nhom = b.get("nhom") or []
        if not isinstance(nhom, list):
            raise ValueError("Dữ liệu nhóm không hợp lệ")
        c.nhom = [{"id": str(g.get("id")), "ten": str(g.get("ten") or "")[:255]}
                  for g in nhom if isinstance(g, dict) and str(g.get("id") or "").isdigit()][:100]
    c.save()
    return {"cauHinh": sv.cau_hinh_nhom_dict(c)}


@api("send", ["POST"])
def api_nhom_anh(request):
    """Tải ảnh đính kèm lên (multipart, trường "anh", có thể nhiều file)."""
    can_khong_dang_gui(request.user)
    c = CauHinhNhom.cua(request.user)
    files = request.FILES.getlist("anh")
    if not files:
        raise ValueError("Chưa chọn ảnh")
    for f in files:
        sv.luu_anh(request.user, c, f)
    return {"cauHinh": sv.cau_hinh_nhom_dict(c)}


@api("send", ["POST"])
def api_nhom_anh_xoa(request):
    can_khong_dang_gui(request.user)
    c = CauHinhNhom.cua(request.user)
    sv.xoa_anh(request.user, c, str(request.json.get("ten") or ""))
    return {"cauHinh": sv.cau_hinh_nhom_dict(c)}


@login_required
def xem_anh_nhom(request, ten):
    """Xem ảnh đính kèm của chính mình."""
    from django.http import FileResponse, Http404
    if ten not in CauHinhNhom.cua(request.user).anh:
        raise Http404
    f = sv.thu_muc_anh(request.user) / ten
    if not f.is_file():
        raise Http404
    return FileResponse(open(f, "rb"))


@api("send", ["POST"])
def api_nhom_gui(request):
    """Gửi nội dung + ảnh vào các nhóm đã chọn, bằng Zalo của chính người bấm."""
    u = request.user
    if worker.trang_thai(u.id).get("login") != "in":
        raise ValueError("Bạn chưa kết nối Zalo. Quét QR để kết nối Zalo của bạn trước khi gửi")
    c = CauHinhNhom.cua(u)
    if not c.nhom:
        raise ValueError("Chưa chọn nhóm nào để gửi")
    if not c.noi_dung and not c.anh:
        raise ValueError("Chưa có nội dung hay ảnh để gửi")
    worker.goi("POST", "/run-groups", {
        "uid": u.id, "groups": c.nhom, "noiDung": c.noi_dung,
        "anh": [f"{u.id}/{a}" for a in c.anh],
        "cfg": CaiDat.lay().to_dict(), "runBy": {"id": u.id, "name": str(u)},
    })
    return {"ok": True}


@api("send", ["GET"])
def api_nhom_lich_su(request):
    """100 lần gửi nhóm gần nhất (nhân viên chỉ thấy của mình)."""
    qs = LichSuGuiNhom.objects.select_related("nguoi_gui")
    if not request.user.co_quyen("all"):
        qs = qs.filter(nguoi_gui=request.user)
    return {"rows": [sv.lich_su_nhom_dict(h) for h in qs[:100]]}
