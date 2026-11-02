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
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import cham_soc as cs
from . import services as sv
from . import worker
from .auth import con_bi_khoa
from .models import (BoNhom, CaiDat, CauHinhNhom, DotGui, KetQua, KhachHang, KhongLienHe, LichHen, LichSuGui,
                     LichSuGuiNhom, MauTinNhan, NguoiDung, NguoiNhan, NhatKy, chuan_hoa_sdt, q_thanh_cong)


# ---------- trang ----------
class DangNhapForm(AuthenticationForm):
    username = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
    error_messages = {
        "invalid_login": "Sai email hoặc mật khẩu.",
        "inactive": "Tài khoản này đã bị khoá.",
    }

    def clean(self):
        email = self.cleaned_data.get("username") or ""
        phut = con_bi_khoa(self.request, email)
        if phut:
            raise forms.ValidationError(f"Sai quá nhiều lần, thử lại sau {phut} phút.")
        try:
            return super().clean()
        except PermissionDenied:
            raise forms.ValidationError(f"Sai quá nhiều lần, thử lại sau {con_bi_khoa(self.request, email) or 5} phút.") from None


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
          "perms": sorted(u.quyen), "doi": u.doi}
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


def han_muc_cho(user):
    """Hạn mức hôm nay của Zalo người này (hiện trên trang gửi tin)."""
    from django.utils.timezone import localtime
    cfg = CaiDat.lay()
    tam = user.tam_dung_den if user.tam_dung_den and user.tam_dung_den > timezone.now() else None
    return {"daGui": sv.da_gui_hom_nay(user), "toiDa": sv.han_muc_ngay(user, cfg),
            "tamDungDen": localtime(tam).strftime("%H:%M %d/%m") if tam else None}


def nhat_ky_cho(user):
    """Nhân viên chỉ thấy nhật ký của mình (thao tác của mình và các đợt gửi mình bấm)."""
    qs = NhatKy.objects.all()
    return qs if user.co_quyen("all") else qs.filter(nguoi_dung=user)


def dot_gan_nhat(user):
    """Đợt gửi từng người gần nhất của user (để hiện nút “Tiếp tục đợt bị gián đoạn”)."""
    d = DotGui.objects.filter(nguoi_gui=user, loai=DotGui.CA_NHAN).order_by("-id").first()
    return cs.dot_dict(d) if d else None


def thong_tin_them(user):
    """Dữ liệu phụ cho trang gửi tin: chiến dịch đang chạy, mẫu tin đã duyệt, lịch hẹn, trạng thái khách."""
    return {
        "chienDich": [{"id": c.id, "ten": c.ten, "duAn": c.du_an, "noiDung": c.noi_dung, "loiMoi": c.loi_moi}
                      for c in sv.chien_dich_dang_chay()],
        "mau": [{"id": m.id, "ten": m.ten, "loai": m.loai, "noiDung": m.noi_dung}
                for m in MauTinNhan.objects.filter(da_duyet=True)],
        "lichHen": [cs.lich_hen_dict(lh) for lh in user.lich_hen.filter(loai=LichHen.CA_NHAN).order_by("-dang_bat", "-tao_luc")[:30]],
        "trangThaiKhach": [{"id": k, "ten": v} for k, v in KhachHang.TRANG_THAI],
        "dot": dot_gan_nhat(user),
        "xuatExcel": user.co_quyen("export"),
    }


# ---------- API ----------
@api("view", ["GET"])
def api_state(request):
    cs.kiem_tra_dot_cua(request.user)
    logs = list(nhat_ky_cho(request.user).order_by("-id")[:300])
    return {
        **trang_thai_cho(request.user),
        "config": CaiDat.lay().to_dict(),
        "tinNhanCuaToi": request.user.tin_nhan_mac_dinh,
        "loiMoiCuaToi": request.user.loi_moi_ket_ban,
        "anhCuaToi": request.user.anh_tin_nhan,
        "contacts": sv.danh_sach(request.user),
        "staff": staff_cho(request.user),
        "logs": [sv.log_dict(n) for n in reversed(logs)],
        "hanMuc": han_muc_cho(request.user),
        **thong_tin_them(request.user),
        "version": sv.phien_ban(request.user),
    }


@api("view", ["GET"])
def api_poll(request):
    """Trang gọi mỗi 1,5 giây: trạng thái gửi, nhật ký mới, và phiên bản danh sách."""
    try:
        sau = int(request.GET.get("log") or 0)
    except ValueError:
        sau = 0
    st = trang_thai_cho(request.user)
    if not st.get("running") and st.get("worker"):
        cs.kiem_tra_dot_cua(request.user)
    logs = nhat_ky_cho(request.user).filter(id__gt=sau).order_by("id")[:200]
    return {**st, "logs": [sv.log_dict(n) for n in logs], "hanMuc": han_muc_cho(request.user),
            "dot": dot_gan_nhat(request.user), "version": sv.phien_ban(request.user)}


@api("all", ["GET"])
def api_thong_ke(request):
    """Số liệu cho bảng điều khiển trong trang quản trị."""
    return {**sv.thong_ke(), "version": sv.phien_ban(request.user)}


@api("view", ["GET", "POST"])
def api_contacts(request):
    ket_qua_luu = None
    if request.method == "POST":
        if not request.user.co_quyen("list"):
            return JsonResponse({"error": "Tài khoản của bạn không có quyền sửa danh sách"}, status=403)
        # Quản trị sửa danh sách trong trang quản trị; đường này chỉ để nhân viên lưu danh sách của mình
        if request.user.co_quyen("all"):
            return JsonResponse({"error": "Quản trị sửa danh sách trong trang quản trị → Người nhận"}, status=403)
        can_khong_dang_gui(request.user)
        rows = request.json.get("contacts") or []
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("Dữ liệu không hợp lệ")
        ket_qua_luu = sv.luu_danh_sach(request.user, rows)
    return {"contacts": sv.danh_sach(request.user), "luu": ket_qua_luu, "staff": staff_cho(request.user), "config": CaiDat.lay().to_dict(),
            "tinNhanCuaToi": request.user.tin_nhan_mac_dinh, "loiMoiCuaToi": request.user.loi_moi_ket_ban,
            "anhCuaToi": request.user.anh_tin_nhan, **thong_tin_them(request.user), "version": sv.phien_ban(request.user)}


@api("list", ["POST"])
def api_khach(request):
    """Nhân viên đổi trạng thái chăm sóc / ghi chú của khách (CRM): chỉ khách trong danh sách của mình."""
    u = request.user
    sdt = chuan_hoa_sdt(request.json.get("sdt"))
    if not sdt:
        raise ValueError("Số điện thoại không hợp lệ")
    if not u.co_quyen("all") and not u.nguoi_nhan.filter(sdt=sdt).exists():
        raise ValueError("Số này không thuộc danh sách của bạn")
    kh, _ = KhachHang.objects.get_or_create(sdt=sdt, defaults={"nhan_vien": u})
    if "trangThai" in request.json:
        tt = request.json.get("trangThai")
        if tt not in dict(KhachHang.TRANG_THAI):
            raise ValueError("Trạng thái không hợp lệ")
        kh.trang_thai = tt
    if "ghiChu" in request.json:
        kh.ghi_chu = str(request.json.get("ghiChu") or "").strip()[:2000]
    kh.save()
    sv.tang_phien_ban([u.id, kh.nhan_vien_id])
    return {"khach": sv.khach_dict(kh), "version": sv.phien_ban(u)}


@api("send", ["GET", "POST"])
def api_lich_hen(request):
    """Hẹn giờ gửi (một lần / hằng ngày / hằng tuần). POST {xoa: id} để xoá, {bat: id, dangBat} để bật / tắt."""
    u = request.user
    loai = request.GET.get("loai") or request.json.get("loai") or LichHen.CA_NHAN
    if request.method == "POST":
        b = request.json
        if b.get("xoa"):
            u.lich_hen.filter(id=int(b["xoa"])).delete()
            sv.ghi_log(f"{u} đã xoá một lịch hẹn gửi.", nguoi_dung=u)
        elif b.get("bat"):
            u.lich_hen.filter(id=int(b["bat"])).update(dang_bat=bool(b.get("dangBat")))
        else:
            cs.tao_lich_hen(u, b)
    return {"lichHen": [cs.lich_hen_dict(lh) for lh in u.lich_hen.filter(loai=loai).select_related("bo_nhom")
                        .order_by("-dang_bat", "-tao_luc")[:30]]}


@api("send", ["POST"])
def api_dot_tiep_tuc(request):
    """Tiếp tục đợt gửi bị gián đoạn / đã dừng: gửi các số còn lại của đợt đó (số đã gửi tự bỏ qua)."""
    u = request.user
    d = DotGui.objects.filter(nguoi_gui=u, id=int(request.json.get("dot") or 0), loai=DotGui.CA_NHAN).first()
    if not d:
        raise ValueError("Không tìm thấy đợt gửi")
    dot = cs.bat_dau_gui(u, chi_sdt=d.danh_sach, ghi_chu=f"Tiếp tục đợt #{d.id}")
    sv.ghi_log(f"{u} tiếp tục đợt gửi #{d.id}: còn {dot.tong} số.", "ok", u)
    return {"ok": True, "dot": cs.dot_dict(dot)}


@api("send", ["POST"])
def api_tin_nhan_cua_toi(request):
    """Nhân viên tự sửa tin nhắn mặc định gửi hàng loạt của mình (để trống = dùng nội dung của quản trị)."""
    u = request.user
    u.tin_nhan_mac_dinh = str(request.json.get("noiDung") or "").strip()[:5000]
    u.save(update_fields=["tin_nhan_mac_dinh"])
    sv.tang_phien_ban()
    return {"tinNhanCuaToi": u.tin_nhan_mac_dinh, "version": sv.phien_ban(request.user)}


@api("send", ["POST"])
def api_loi_moi_cua_toi(request):
    """Nhân viên tự sửa lời mời kết bạn của mình (để trống = dùng lời mời của quản trị)."""
    u = request.user
    u.loi_moi_ket_ban = str(request.json.get("loiMoi") or "").strip()[:3000]
    u.save(update_fields=["loi_moi_ket_ban"])
    sv.tang_phien_ban()
    return {"loiMoiCuaToi": u.loi_moi_ket_ban, "version": sv.phien_ban(request.user)}


@api("send", ["POST"])
def api_anh_cua_toi(request):
    """Nhân viên tải ảnh gửi kèm tin nhắn của mình (multipart, trường "anh", có thể nhiều file)."""
    can_khong_dang_gui(request.user)
    files = request.FILES.getlist("anh")
    if not files:
        raise ValueError("Chưa chọn ảnh")
    for f in files:
        sv.luu_anh_nv(request.user, f)
    sv.tang_phien_ban()
    return {"anhCuaToi": request.user.anh_tin_nhan, "version": sv.phien_ban(request.user)}


@api("send", ["POST"])
def api_anh_cua_toi_xoa(request):
    can_khong_dang_gui(request.user)
    sv.xoa_anh_nv(request.user, str(request.json.get("ten") or ""))
    sv.tang_phien_ban()
    return {"anhCuaToi": request.user.anh_tin_nhan, "version": sv.phien_ban(request.user)}


@login_required
def xem_anh_cua_toi(request, ten):
    from django.http import FileResponse, Http404
    if ten not in request.user.anh_tin_nhan:
        raise Http404
    f = sv.thu_muc_anh_nv(request.user) / ten
    if not f.is_file():
        raise Http404
    return FileResponse(open(f, "rb"))


@api("list", ["POST"])
def api_tu_choi(request):
    """Khách từ chối liên hệ: thêm SĐT vào danh sách không liên hệ (không gửi nữa, nhập lại cũng bị loại).
    Nhân viên chỉ thêm được số trong danh sách của mình. bo=true để bỏ khỏi danh sách (chỉ quản trị)."""
    u = request.user
    sdt = chuan_hoa_sdt(request.json.get("sdt"))
    if not sdt:
        raise ValueError("Số điện thoại không hợp lệ")
    if request.json.get("bo"):
        if not u.co_quyen("settings"):
            raise ValueError("Chỉ quản trị được bỏ số khỏi danh sách từ chối")
        KhongLienHe.objects.filter(sdt=sdt).delete()
        sv.ghi_log(f"{u} đã bỏ {sdt} khỏi danh sách khách từ chối liên hệ.", "warn", u)
    else:
        if not u.co_quyen("all") and not u.nguoi_nhan.filter(sdt=sdt).exists():
            raise ValueError("Số này không thuộc danh sách của bạn")
        ly_do = str(request.json.get("lyDo") or "").strip()[:255] or "Khách từ chối liên hệ"
        KhongLienHe.objects.get_or_create(sdt=sdt, defaults={"ly_do": ly_do, "nguoi_them": u})
        sv.ghi_log(f"{u} đánh dấu {sdt} là khách từ chối liên hệ ({ly_do}). Tool sẽ không gửi cho số này nữa.", "warn", u)
    sv.tang_phien_ban()
    return {"contacts": sv.danh_sach(u), "version": sv.phien_ban(u)}


@api("list", ["POST"])
def api_import(request):
    return {"rows": sv.doc_file(request.body)}


@api("view", ["POST"])
def api_preview(request):
    cfg = CaiDat.lay()
    chan = sv.chua_gui_duoc(request.user, cfg)
    if chan:
        raise ValueError(chan)
    items = sv.cho_gui(request.user, request.json.get("phamVi"), sv.so_gui_toi_da(request.user, cfg))
    so_anh = len(sv.anh_gui_kem(request.user, cfg)) if cfg.gui_tin_nhan else 0
    # Có nhiều mẫu thì xem trước một mẫu ngẫu nhiên (lúc gửi thật mỗi người cũng nhận ngẫu nhiên 1 mẫu)
    out = []
    for r in items:
        loi_moi, tin = sv.dien(sv.loi_moi_gui(r, cfg), r.ten), sv.noi_dung_gui(r, cfg)
        out.append({
            "sdt": r.sdt, "ten": r.ten, "chienDich": r.chien_dich.ten if r.chien_dich_id else "",
            "canhBao": (sv.canh_bao_noi_dung(tin, "tin_nhan", cfg.cac_tu_spam()) if cfg.gui_tin_nhan else [])
            + (sv.canh_bao_noi_dung(loi_moi, "loi_moi", cfg.cac_tu_spam()) if cfg.gui_ket_ban else []),
            "loiMoi": sv.xao(loi_moi) if cfg.gui_ket_ban else None, "soMauLoiMoi": len(sv.cac_mau(loi_moi)),
            "tenGoiNho": sv.ten_goi_nho(cfg.mau_ten_goi_nho, r.ten, r.sdt, sv.du_an_cua(r, cfg)) if cfg.doi_ten_goi_nho else None,
            "tinNhan": sv.xao(tin) if cfg.gui_tin_nhan else None, "soMauTinNhan": len(sv.cac_mau(tin)),
            "soAnh": so_anh,
        })
    return {"items": out, "hanMuc": han_muc_cho(request.user)}


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
        dong = NguoiNhan.objects.filter(id=int(request.json.get("id") or 0), sdt=phone).first()
        if dong and dong.chien_dich_id:
            # Số trong chiến dịch: chỉ tính kết quả gửi sau lúc này (lịch sử cũ vẫn giữ)
            dong.gui_lai_tu = timezone.now()
            dong.save(update_fields=["gui_lai_tu"])
        else:
            KetQua.objects.filter(sdt=phone).delete()
        sv.ghi_log(f"{u} đánh dấu {phone} để gửi lại.", "warn", u)
    sv.tang_phien_ban()
    return {"contacts": sv.danh_sach(u), "version": sv.phien_ban(request.user)}


# ---------- Zalo của từng người ----------
def _chu_tai_khoan(request):
    """Tài khoản Zalo được thao tác: mặc định của chính mình; quản trị thao tác hộ được người khác."""
    uid = request.json.get("uid")
    if uid and int(uid) != request.user.id:
        if not request.user.co_quyen("settings"):
            raise ValueError("Bạn chỉ thao tác được với Zalo của chính mình")
        chu = NguoiDung.objects.filter(id=int(uid)).first()
        if not chu:
            raise ValueError("Không tìm thấy tài khoản này")
        return chu
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
    dot = cs.bat_dau_gui(request.user, request.json.get("phamVi"))
    return {"ok": True, "dot": cs.dot_dict(dot)}


@api("send", ["POST"])
def api_stop(request):
    """Dừng đợt gửi của mình; quản trị dừng hộ được đợt gửi của nhân viên."""
    chu = _chu_tai_khoan(request)
    worker.goi("POST", "/stop", {"uid": chu.id})
    ai = "" if chu == request.user else f" đợt gửi của {chu}"
    sv.ghi_log(f"{request.user} bấm dừng{ai}.", "warn", request.user)
    return {"ok": True}


@api("export", ["GET"])
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
    n = 0
    for k in qs:
        sv.dong_excel(ws, [k.sdt, k.ten, k.ten_zalo, k.ket_ban, k.tin_nhan, k.ghi_chu,
                           timezone.localtime(k.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), str(k.nguoi_gui or "")])
        n += 1
    for col, w in zip("ABCDEFGH", (13, 24, 24, 12, 12, 45, 20, 20)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    sv.ghi_log(f"{request.user} đã xuất Excel kết quả gửi ({n} dòng).", nguoi_dung=request.user)
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
    ws.append(["SĐT", "Họ tên", "Nội dung", "Dự án"])
    ws.append(["0901234567", "Anh Nam", "Chào {ten}, em gửi anh thông tin chương trình tháng này ạ.", "Palm City"])
    ws.append(["0912345678", "Chị Lan", "", "Global City"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F5FC4")
    for r in (2, 3):
        ws.cell(r, 1).number_format = "@"   # giữ số 0 đầu
        ws.cell(r, 3).alignment = Alignment(wrap_text=True, vertical="top")
    ws["F1"] = "Ghi chú"
    ws["F2"] = "{ten} sẽ được thay bằng họ tên. Để trống Nội dung thì gửi nội dung mặc định."
    ws["F3"] = "Dự án được ghi vào tên gợi nhớ khi kết bạn. Để trống thì dùng dự án mặc định của quản trị."
    ws["F1"].font = Font(bold=True)
    for col, w in zip("ABCDEF", (14, 22, 60, 18, 2, 60)):
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
    ok = qs.filter(q_thanh_cong()).count()
    rows = qs[(trang - 1) * co: trang * co]
    return {"rows": [sv.lich_su_dict(h) for h in rows], "tong": tong, "thanhCong": ok, "loi": tong - ok,
            "trang": trang, "soTrang": max(1, (tong + co - 1) // co)}


@api("export", ["GET"])
def api_lich_su_excel(request):
    """Xuất Excel đúng theo bộ lọc đang chọn."""
    loc = _bo_loc(request)
    qs = sv.loc_lich_su(request.user, **loc)
    data = sv.excel_lich_su(qs)
    mo_ta = ", ".join(f"{k}={v}" for k, v in loc.items() if v) or "không lọc"
    sv.ghi_log(f"{request.user} đã xuất Excel lịch sử gửi ({qs.count()} dòng, {mo_ta}).", nguoi_dung=request.user)
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
    if b.get("type") == "auto_stop":
        # Worker tự dừng vì lỗi liên tiếp: tạm dừng Zalo này một thời gian để tài khoản không bị phạt nặng hơn
        cfg = CaiDat.lay()
        u = NguoiDung.objects.filter(id=b.get("nguoiDungId")).first()
        if u and cfg.tam_dung_gio:
            from datetime import timedelta
            u.tam_dung_den = timezone.now() + timedelta(hours=cfg.tam_dung_gio)
            u.save(update_fields=["tam_dung_den"])
            sv.ghi_log(f"Zalo của {u} bị tạm dừng gửi {cfg.tam_dung_gio} giờ vì gặp {b.get('soLoi')} lỗi liên tiếp. "
                       f"Kiểm tra Zalo trên điện thoại; muốn gửi lại sớm thì quản trị vào Quản trị → Tài khoản, tick tài khoản → thao tác “Bỏ tạm dừng gửi”.", "error", u)
            sv.tang_phien_ban([u.id])
    elif b.get("type") == "log":
        NhatKy.objects.create(noi_dung=str(b.get("text", ""))[:5000], muc=b.get("level") or "info",
                              nguoi_dung=NguoiDung.objects.filter(id=b.get("nguoiDungId")).first())
    elif b.get("type") == "result":
        # Worker gửi lại kết quả chưa được xác nhận (hàng đợi trên đĩa): mã đã có thì bỏ qua, không ghi 2 lần
        cs.ghi_ket_qua(b)
    elif b.get("type") == "result_nhom":
        cs.ghi_ket_qua_nhom(b)
    elif b.get("type") == "dot_ket_thuc":
        cs.ket_thuc_dot(b.get("dotId"), b.get("trangThai"), b.get("daGui"))
    elif b.get("type") == "ket_ban":
        # Khách đồng ý kết bạn: trả lời worker có cần tự gửi tin cho khách không
        out = cs.khach_dong_y(b)
        if out.get("guiTin"):
            out["guiTin"]["cfg"] = CaiDat.lay().to_dict()
        return JsonResponse({"ok": True, **out})
    elif b.get("type") == "tra_loi":
        cs.khach_tra_loi(b)
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
    u = request.user
    c = CauHinhNhom.cua(u)
    logs = list(nhat_ky_cho(u).filter(nguoi_dung=u).order_by("-id")[:200])
    return {**trang_thai_cho(u), "config": CaiDat.lay().to_dict(), "cfgNhom": CaiDat.lay().cfg_nhom(),
            "cauHinh": sv.cau_hinh_nhom_dict(c), "anhToiDa": sv.ANH_TOI_DA, "anhToiDaMb": sv.ANH_TOI_DA_MB,
            "boNhom": [cs.bo_nhom_dict(x) for x in u.bo_nhom.all()],
            "lichHen": [cs.lich_hen_dict(lh) for lh in u.lich_hen.filter(loai=LichHen.NHOM).select_related("bo_nhom")
                        .order_by("-dang_bat", "-tao_luc")[:30]],
            "mau": [{"id": m.id, "ten": m.ten, "noiDung": m.noi_dung}
                    for m in MauTinNhan.objects.filter(da_duyet=True, loai=MauTinNhan.NHOM)],
            "logs": [sv.log_dict(n) for n in reversed(logs)], "version": sv.phien_ban(u)}


def _nhom_hop_le(nhom):
    """Danh sách nhóm từ trang web: [{id, ten, noiDung (nội dung riêng của nhóm, không bắt buộc)}]."""
    if not isinstance(nhom, list):
        raise ValueError("Dữ liệu nhóm không hợp lệ")
    return [{"id": str(g.get("id")), "ten": str(g.get("ten") or "")[:255],
             **({"noiDung": str(g["noiDung"]).strip()[:5000]} if str(g.get("noiDung") or "").strip() else {})}
            for g in nhom if isinstance(g, dict) and str(g.get("id") or "").isdigit()][:100]


@api("send", ["POST"])
def api_bo_nhom(request):
    """Lưu / xoá bộ nhóm: {ten, nhom} để lưu (trùng tên thì ghi đè), {xoa: id} để xoá."""
    u = request.user
    b = request.json
    if b.get("xoa"):
        u.bo_nhom.filter(id=int(b["xoa"])).delete()
    else:
        ten = str(b.get("ten") or "").strip()[:100]
        if not ten:
            raise ValueError("Đặt tên cho bộ nhóm")
        nhom = _nhom_hop_le(b.get("nhom") or [])
        if not nhom:
            raise ValueError("Chưa chọn nhóm nào")
        BoNhom.objects.update_or_create(nguoi=u, ten=ten, defaults={"nhom": nhom})
    return {"boNhom": [cs.bo_nhom_dict(x) for x in u.bo_nhom.all()]}


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
        c.nhom = _nhom_hop_le(b.get("nhom") or [])
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
def xem_anh_tin_nhan(request, pk):
    """Xem ảnh gửi kèm tin nhắn khách (quản trị cài; ai đăng nhập cũng xem được để biết sẽ gửi gì)."""
    from django.http import FileResponse, Http404
    from .models import AnhTinNhan
    a = AnhTinNhan.objects.filter(pk=pk).first()
    if not a or not a.tep or not a.tep.storage.exists(a.tep.name):
        raise Http404
    return FileResponse(a.tep.open("rb"))


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
    dot = cs.bat_dau_gui_nhom(request.user)
    return {"ok": True, "dot": cs.dot_dict(dot)}


@api("send", ["GET"])
def api_nhom_lich_su(request):
    """100 lần gửi nhóm gần nhất (nhân viên chỉ thấy của mình)."""
    qs = LichSuGuiNhom.objects.select_related("nguoi_gui")
    if not request.user.co_quyen("all"):
        qs = qs.filter(nguoi_gui=request.user)
    return {"rows": [sv.lich_su_nhom_dict(h) for h in qs[:100]]}


# ---------- báo cáo theo thời gian ----------
def _khoang_ngay(request, mac_dinh=30):
    from datetime import date, timedelta
    try:
        den = date.fromisoformat(request.GET.get("den")) if request.GET.get("den") else timezone.localdate()
        tu = date.fromisoformat(request.GET.get("tu")) if request.GET.get("tu") else den - timedelta(days=mac_dinh - 1)
    except ValueError:
        raise ValueError("Ngày không hợp lệ") from None
    if tu > den:
        tu, den = den, tu
    if (den - tu).days > 366:
        raise ValueError("Chọn khoảng tối đa 1 năm")
    return tu, den


@api("view", ["GET"])
def api_bao_cao(request):
    """Biểu đồ theo ngày, xếp hạng nhân viên, so sánh dự án / chiến dịch. Nhân viên / trưởng nhóm chỉ thấy của mình / đội."""
    u = request.user
    tu, den = _khoang_ngay(request)
    nguoi = None if u.co_quyen("all") else u.thanh_vien_doi()
    return {**cs.bao_cao(tu, den, nguoi), "tu": tu.isoformat(), "den": den.isoformat()}


# ---------- trưởng nhóm: đội của tôi ----------
@login_required
def trang_doi(request):
    u = request.user
    if not u.co_quyen("team"):
        raise PermissionDenied
    me = {"id": u.id, "name": str(u), "role": u.vai_tro, "roleLabel": u.get_vai_tro_display(), "doi": u.doi}
    return render(request, "guitin/doi.html", {"me": me})


@api("team", ["GET", "POST"])
def api_doi(request):
    """Trưởng nhóm xem số của cả đội và giao lại cho thành viên.
    POST {ids: [...], nguoi: id} giao các số đã chọn; {chiaDeu: true, ids?} chia đều các số chưa gửi cho thành viên."""
    u = request.user
    tv = list(u.thanh_vien_doi())
    tv_ids = {x.id for x in tv}
    qs = NguoiNhan.objects.filter(phu_trach_id__in=tv_ids)
    if request.method == "POST":
        b = request.json
        ids = [int(i) for i in (b.get("ids") or []) if str(i).isdigit()]
        if b.get("chiaDeu"):
            nv = [x for x in tv if x.vai_tro == NguoiDung.NHAN_VIEN]
            if not nv:
                raise ValueError("Đội chưa có nhân viên nào")
            cho = sv.loc_cho_gui(qs.filter(id__in=ids) if ids else qs.filter(phu_trach=u), CaiDat.lay())
            cho = list(cho.order_by("thu_tu", "id"))
            for i, r in enumerate(cho):
                r.phu_trach, r.quan_tri_giao = nv[i % len(nv)], True
            NguoiNhan.objects.bulk_update(cho, ["phu_trach", "quan_tri_giao"], batch_size=500)
            sv.ghi_log(f"{u} (trưởng nhóm) đã chia {len(cho)} số cho {len(nv)} nhân viên trong đội.", "ok", u)
        else:
            nguoi = int(b.get("nguoi") or 0)
            if nguoi not in tv_ids:
                raise ValueError("Chỉ giao được cho thành viên trong đội")
            n = qs.filter(id__in=ids).update(phu_trach_id=nguoi, quan_tri_giao=True)
            ten = next(str(x) for x in tv if x.id == nguoi)
            sv.ghi_log(f"{u} (trưởng nhóm) đã giao {n} số cho {ten}.", "ok", u)
        sv.tang_phien_ban(tv_ids)
    rows = list(qs.select_related("phu_trach", "chien_dich")[:3000])
    kq = sv.ket_qua_cac_dong(rows)
    tu_choi = set(KhongLienHe.objects.filter(sdt__in=[r.sdt for r in rows if r.sdt]).values_list("sdt", flat=True))
    return {
        "thanhVien": [{"id": x.id, "ten": str(x), "vaiTro": x.get_vai_tro_display()} for x in tv],
        "thongKe": sv.thong_ke(qs)["nhanVien"],
        "rows": [{"id": r.id, "sdt": r.sdt_goc, "ten": r.ten, "duAn": r.du_an, "phuTrach": r.phu_trach_id,
                  "chienDich": r.chien_dich.ten if r.chien_dich_id else "",
                  "trangThai": "tu_choi" if r.sdt in tu_choi else ("so_sai" if not r.sdt else (
                      ("ok" if kq[r.id].thanh_cong else "loi") if r.id in kq else "cho"))} for r in rows],
    }
