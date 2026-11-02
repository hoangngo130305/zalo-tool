"""Báo cáo Excel định kỳ tự gửi email cho quản lý (cuối ngày / cuối tuần), gửi qua Gmail bằng mật khẩu ứng dụng."""
import io
from datetime import datetime, timedelta

from django.core.mail import EmailMessage, get_connection
from django.utils import timezone
from django.utils.timezone import localtime

from . import cham_soc as cs
from . import services as sv
from .models import CaiDat, LichSuGui


def khoang_bao_cao(cfg, luc=None):
    """Khoảng ngày của báo cáo: hôm nay (báo cáo ngày) hoặc 7 ngày tính đến hôm nay (báo cáo tuần)."""
    den = localtime(luc or timezone.now()).date()
    tu = den - timedelta(days=6) if cfg.bao_cao_tan_suat == CaiDat.HANG_TUAN else den
    return tu, den


def tao_excel(tu, den):
    """File Excel báo cáo: Tổng quan, Theo nhân viên, Theo dự án, Chiến dịch, Lịch sử gửi trong khoảng."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    bc = cs.bao_cao(tu, den)
    wb = Workbook()

    def bang(ws, dau, dong, rong):
        ws.append(dau)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F5FC4")
        for d in dong:
            sv.dong_excel(ws, d)
        for i, w in enumerate(rong):
            ws.column_dimensions[chr(65 + i)].width = w
        ws.freeze_panes = ws.cell(row=ws.max_row - len(dong) + 1, column=1) if dong else None

    t = bc["tong"]
    pt = (lambda a, b: f"{round(a / b * 100)}%" if b else "–")
    ws = wb.active
    ws.title = "Tổng quan"
    ws.append([f"Báo cáo Zalo Tool từ {tu:%d/%m/%Y} đến {den:%d/%m/%Y}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    bang(ws, ["Chỉ số", "Số lượng", "Tỷ lệ"], [
        ["Đã gửi (lượt)", t["gui"], ""], ["Gửi thành công", t["thanhCong"], pt(t["thanhCong"], t["gui"])],
        ["Khách đồng ý kết bạn", t["dongY"], ""], ["Khách trả lời", t["traLoi"], ""],
    ], [28, 14, 10])
    ws.append([])
    ws.append(["Ngày", "Đã gửi", "Thành công", "Đồng ý kết bạn", "Trả lời"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for d in bc["theoNgay"]:
        ws.append([datetime.fromisoformat(d["ngay"]).strftime("%d/%m/%Y"), d["gui"], d["thanhCong"], d["dongY"], d["traLoi"]])

    bang(wb.create_sheet("Theo nhân viên"), ["Nhân viên", "Đã gửi", "Thành công", "Tỷ lệ", "Đồng ý kết bạn", "Trả lời"],
         [[n["ten"], n["gui"], n["thanhCong"], pt(n["thanhCong"], n["gui"]), n["dongY"], n["traLoi"]] for n in bc["nhanVien"]],
         [26, 10, 12, 8, 16, 10])
    bang(wb.create_sheet("Theo dự án"), ["Dự án", "Đã gửi", "Thành công", "Tỷ lệ"],
         [[d["duAn"], d["gui"], d["thanhCong"], pt(d["thanhCong"], d["gui"])] for d in bc["duAn"]], [28, 10, 12, 8])
    bang(wb.create_sheet("Chiến dịch"), ["Chiến dịch", "Đang chạy", "Khách", "Đã gửi", "Thành công", "Đồng ý kết bạn", "Trả lời"],
         [[c["ten"], "Có" if c["dangChay"] else "Không", c["tong"], c["gui"], c["thanhCong"], c["dongY"], c["traLoi"]]
          for c in bc["chienDich"]], [30, 10, 8, 10, 12, 16, 10])
    bd = timezone.make_aware(datetime.combine(tu, datetime.min.time()))
    kt = timezone.make_aware(datetime.combine(den + timedelta(days=1), datetime.min.time()))
    ls = LichSuGui.objects.filter(thoi_gian__gte=bd, thoi_gian__lt=kt).select_related("nguoi_gui", "chien_dich").order_by("thoi_gian")
    bang(wb.create_sheet("Lịch sử gửi"), ["Thời gian", "SĐT", "Họ tên", "Loại", "Chiến dịch", "Kết bạn", "Tin nhắn", "Kết quả", "Nhân viên"],
         [[localtime(h.thoi_gian).strftime("%d/%m/%Y %H:%M"), h.sdt, h.ten, h.get_loai_display(), h.chien_dich.ten if h.chien_dich else "",
           h.ket_ban, h.tin_nhan, "Thành công" if h.thanh_cong else "Lỗi", str(h.nguoi_gui or "")] for h in ls[:20000]],
         [17, 12, 22, 14, 20, 10, 10, 11, 20])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), bc


def gui_bao_cao(cfg=None, luc=None, nguoi_nhan=None):
    """Gửi email báo cáo kèm file Excel. Trả về danh sách email đã gửi; lỗi thì ValueError (thông báo dễ hiểu)."""
    cfg = cfg or CaiDat.lay()
    nhan = nguoi_nhan or cfg.cac_email_nhan()
    if not nhan:
        raise ValueError("Chưa nhập email nhận báo cáo (Cài đặt gửi tin → Báo cáo qua email).")
    if not cfg.gmail_gui or not cfg.gmail_mat_khau:
        raise ValueError("Chưa nhập Gmail gửi báo cáo hoặc mật khẩu ứng dụng của Gmail.")
    tu, den = khoang_bao_cao(cfg, luc)
    data, bc = tao_excel(tu, den)
    t = bc["tong"]
    ky = f"ngày {den:%d/%m/%Y}" if tu == den else f"từ {tu:%d/%m} đến {den:%d/%m/%Y}"
    top = "\n".join(f"  {i + 1}. {n['ten']}: gửi {n['gui']}, thành công {n['thanhCong']}, đồng ý kết bạn {n['dongY']}, "
                    f"trả lời {n['traLoi']}" for i, n in enumerate(bc["nhanVien"][:10])) or "  (chưa có)"
    than = (f"Báo cáo Zalo Tool {ky}\n\n"
            f"- Đã gửi: {t['gui']} lượt, thành công {t['thanhCong']}\n"
            f"- Khách đồng ý kết bạn: {t['dongY']}\n- Khách trả lời: {t['traLoi']}\n\n"
            f"Theo nhân viên:\n{top}\n\nChi tiết trong file Excel đính kèm.\n-- Zalo Tool (email tự động)")
    ket_noi = get_connection(host="smtp.gmail.com", port=587, username=cfg.gmail_gui,
                             password=cfg.gmail_mat_khau.replace(" ", ""), use_tls=True, timeout=30)
    thu = EmailMessage(f"[Zalo Tool] Báo cáo {ky}", than, f"Zalo Tool <{cfg.gmail_gui}>", nhan, connection=ket_noi)
    thu.attach(f"bao-cao-zalo-tool-{den:%Y%m%d}.xlsx", data,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    try:
        thu.send()
    except Exception as e:  # noqa: BLE001 — lỗi SMTP: báo rõ cho quản trị
        loi = str(e)
        if "535" in loi or "Username and Password not accepted" in loi:
            raise ValueError("Gmail từ chối đăng nhập: kiểm tra lại Gmail gửi và mật khẩu ứng dụng (16 ký tự, "
                             "không phải mật khẩu đăng nhập Gmail).") from e
        raise ValueError(f"Không gửi được email: {loi}") from e
    return nhan


def den_gio_gui(cfg, now=None):
    """Đến giờ gửi báo cáo định kỳ mà chưa gửi lần nào cho kỳ này?"""
    if cfg.bao_cao_tan_suat == CaiDat.TAT:
        return False
    t = localtime(now or timezone.now())
    if cfg.bao_cao_tan_suat == CaiDat.HANG_TUAN and t.weekday() != cfg.bao_cao_thu:
        return False
    if t.time() < cfg.bao_cao_gio:
        return False
    return not cfg.bao_cao_gui_luc or localtime(cfg.bao_cao_gui_luc).date() < t.date()


def chay_dinh_ky(now=None):
    """Gọi từ chay_lich mỗi phút: đến giờ thì gửi báo cáo. Trả về mô tả việc đã làm (hoặc None)."""
    cfg = CaiDat.lay()
    if not den_gio_gui(cfg, now):
        return None
    # Đánh dấu trước để lỗi (vd sai mật khẩu) không làm gửi lại mỗi phút; quản trị bấm “Gửi thử” sau khi sửa
    CaiDat.objects.filter(pk=cfg.pk).update(bao_cao_gui_luc=now or timezone.now())
    try:
        nhan = gui_bao_cao(cfg, now)
        sv.ghi_log(f"Đã tự gửi email báo cáo {cfg.get_bao_cao_tan_suat_display().lower()} cho {', '.join(nhan)}.", "ok")
        return f"Đã gửi báo cáo cho {', '.join(nhan)}"
    except ValueError as e:
        sv.ghi_log(f"Không tự gửi được email báo cáo: {e}", "error")
        return f"Lỗi gửi báo cáo: {e}"
