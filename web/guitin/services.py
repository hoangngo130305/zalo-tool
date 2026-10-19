"""Nghiệp vụ dùng chung cho trang gửi tin và trang quản trị."""
import itertools

from django.core.cache import cache
from django.db import transaction
from django.db.models import Max
from django.utils.timezone import localtime

from .models import CaiDat, KetQua, LichSuGui, NguoiDung, NguoiNhan, NhatKy, chuan_hoa_sdt


# ---------- phiên bản danh sách: tăng mỗi khi danh sách / kết quả đổi để các trang đang mở tải lại ----------
def tang_phien_ban():
    try:
        cache.incr("ds_phien_ban")
    except ValueError:
        cache.set("ds_phien_ban", 1, None)


def phien_ban():
    return cache.get_or_set("ds_phien_ban", 1, None)


def ghi_log(noi_dung, muc="info", nguoi_dung=None):
    NhatKy.objects.create(noi_dung=noi_dung, muc=muc, nguoi_dung=nguoi_dung)


def log_dict(n):
    return {"id": n.id, "t": localtime(n.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), "text": n.noi_dung, "level": n.muc}


# ---------- phạm vi ----------
def pham_vi(user, chon="all"):
    """Trả về bộ lọc người phụ trách. Nhân viên luôn chỉ thấy số của mình.
    Quản trị / chỉ xem chọn được: all (tất cả), none (chưa giao), hoặc id nhân viên."""
    if not user.co_quyen("all"):
        return {"phu_trach": user}
    if chon in (None, "", "all"):
        return {}
    if chon == "none":
        return {"phu_trach__isnull": True}
    return {"phu_trach_id": int(chon)}


def nguoi_duoc_giao():
    """Những người có thể được giao số (nhân viên và quản trị đang hoạt động)."""
    return list(NguoiDung.objects.filter(is_active=True, vai_tro__in=[NguoiDung.NHAN_VIEN, NguoiDung.QUAN_TRI])
                .order_by("vai_tro", "ho_ten"))


def staff_dict(users):
    return [{"id": u.id, "name": u.ho_ten or u.email, "role": u.vai_tro} for u in users]


def ket_qua_dict(k):
    return {
        "ten": k.ten, "tenZalo": k.ten_zalo, "ketBan": k.ket_ban, "tinNhan": k.tin_nhan, "ghiChu": k.ghi_chu,
        "thoiGian": localtime(k.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"),
        "nguoiGui": str(k.nguoi_gui) if k.nguoi_gui else "",
    }


def danh_sach(user, chon="all"):
    qs = NguoiNhan.objects.filter(**pham_vi(user, chon)).select_related("phu_trach")
    rows = list(qs)
    kq = {k.sdt: k for k in KetQua.objects.filter(sdt__in=[r.sdt for r in rows if r.sdt]).select_related("nguoi_gui")}
    return [{
        "sdt": r.sdt_goc, "ten": r.ten, "noiDung": r.noi_dung, "sdtChuan": r.sdt,
        "phuTrach": r.phu_trach_id, "phuTrachTen": str(r.phu_trach) if r.phu_trach else None,
        "ketQua": ket_qua_dict(kq[r.sdt]) if r.sdt in kq else None,
    } for r in rows]


def dien(tpl, ten):
    """Thay {ten} bằng tên người nhận (trống thì thành "bạn")."""
    import re
    return re.sub(r"\{ten\}", lambda m: ten or "bạn", tpl or "", flags=re.IGNORECASE).strip()


def ten_goi_nho(tpl, ten, sdt):
    """Tên gợi nhớ đặt cho người được mời kết bạn (giống worker): {ten} {sdt}, không có tên thì chỉ còn SĐT."""
    import re
    s = re.sub(r"\{ten\}", lambda m: ten or "", tpl or "{ten} {sdt}", flags=re.IGNORECASE)
    s = re.sub(r"\{sdt\}", lambda m: sdt or "", s, flags=re.IGNORECASE)
    # Bỏ dấu nối thừa ở hai đầu khi thiếu tên, vd "{ten} - {sdt}" -> "0901234567"
    return " ".join(s.split()).strip(" -–|,.:")[:60] or sdt


def noi_dung_gui(r, cfg):
    """Nội dung tin nhắn sẽ gửi cho 1 người, theo thứ tự ưu tiên:
    nội dung riêng của dòng -> tin nhắn mặc định của nhân viên phụ trách -> nội dung mặc định của quản trị."""
    cua_nv = r.phu_trach.tin_nhan_mac_dinh if r.phu_trach_id and r.phu_trach else ""
    return dien(r.noi_dung or cua_nv or cfg.noi_dung_tin_nhan, r.ten)


def cho_gui(user, chon, gioi_han=None):
    """Các số còn phải gửi trong phạm vi: SĐT hợp lệ, chưa có kết quả, không trùng, có nội dung (nếu gửi tin nhắn)."""
    cfg = CaiDat.lay()
    da_gui = set(KetQua.objects.values_list("sdt", flat=True))
    seen, out = set(), []
    for r in NguoiNhan.objects.filter(**pham_vi(user, chon)).exclude(sdt__isnull=True).select_related("phu_trach"):
        if r.sdt in da_gui or r.sdt in seen:
            continue
        if cfg.gui_tin_nhan and not noi_dung_gui(r, cfg):
            continue
        seen.add(r.sdt)
        out.append(r)
        if gioi_han and len(out) >= gioi_han:
            break
    return out


@transaction.atomic
def luu_danh_sach(user, rows):
    """Nhân viên chỉ thay phần của mình; quản trị thay cả danh sách kèm người phụ trách."""
    if user.co_quyen("all"):
        hop_le = {u.id for u in nguoi_duoc_giao()}
        NguoiNhan.objects.all().delete()
        base = 0

        def owner(r):
            try:
                pid = int(r.get("phuTrach") or 0)
            except (TypeError, ValueError):
                return None
            return pid if pid in hop_le else None
    else:
        NguoiNhan.objects.filter(phu_trach=user).delete()
        base = NguoiNhan.objects.aggregate(m=Max("thu_tu"))["m"] or 0

        def owner(r):
            return user.id

    NguoiNhan.objects.bulk_create([
        NguoiNhan(thu_tu=base + i + 1, sdt_goc=str(r.get("sdt") or "").strip()[:32], sdt=chuan_hoa_sdt(r.get("sdt")),
                  ten=str(r.get("ten") or "").strip()[:255], noi_dung=str(r.get("noiDung") or "").strip()[:5000],
                  phu_trach_id=owner(r))
        for i, r in enumerate(rows)
    ])
    tang_phien_ban()


@transaction.atomic
def chia_deu():
    """Chia lần lượt các số chưa giao cho các nhân viên. Trả về (số đã chia, số nhân viên)."""
    nv = [u for u in nguoi_duoc_giao() if u.vai_tro == NguoiDung.NHAN_VIEN]
    if not nv:
        raise ValueError("Chưa có tài khoản nhân viên nào. Tạo trong trang quản trị → Tài khoản.")
    vong = itertools.cycle(nv)
    chua_giao = list(NguoiNhan.objects.filter(phu_trach__isnull=True))
    for r in chua_giao:
        r.phu_trach = next(vong)
    NguoiNhan.objects.bulk_update(chua_giao, ["phu_trach"])
    tang_phien_ban()
    return len(chua_giao), len(nv)


# ---------- nhập danh sách ----------
def doc_file(data):
    """Đọc file Excel (.xlsx) hoặc CSV: cột SĐT / Số điện thoại / Phone, Tên / Họ tên / Name,
    và (không bắt buộc) Nội dung / Nội dung tin nhắn / Tin nhắn / Message."""
    import csv
    import io
    import unicodedata

    if data[:2] == b"PK":
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        rows = [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
    else:
        text = data.decode("utf-8-sig", errors="replace")
        delim = ";" if text.count(";") > text.count(",") else ","
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    if not rows:
        return []

    def key(s):
        s = unicodedata.normalize("NFD", str(s).lower()).replace("đ", "d")
        return "".join(ch for ch in s if unicodedata.category(ch) != "Mn" and not ch.isspace())

    head = [key(h) for h in rows[0]]
    sdt_cot = next((i for i, h in enumerate(head) if h in ("sdt", "sodienthoai", "dienthoai", "phone")), None)
    ten_cot = next((i for i, h in enumerate(head) if h in ("ten", "hoten", "hovaten", "name", "tenkhachhang")), None)
    nd_cot = next((i for i, h in enumerate(head)
                   if h in ("noidung", "noidungtinnhan", "noidunggui", "tinnhan", "loinhan", "message", "content")), None)
    if sdt_cot is None:
        raise ValueError("Không thấy cột SĐT. File cần có cột SĐT (hoặc Số điện thoại / Phone) và cột Tên.")
    out = []
    for r in rows[1:]:
        sdt = str(r[sdt_cot] if sdt_cot < len(r) else "").strip()
        if sdt.endswith(".0"):
            sdt = sdt[:-2]
        if sdt:
            out.append({"sdt": sdt, "ten": r[ten_cot] if ten_cot is not None and ten_cot < len(r) else "",
                        "noiDung": r[nd_cot] if nd_cot is not None and nd_cot < len(r) else ""})
    return out


def doc_van_ban(text):
    """Đọc danh sách dán vào: mỗi dòng 'SĐT, Tên, Nội dung' hoặc 2–3 cột copy từ Excel (cách nhau bằng Tab)."""
    import re
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2) if "\t" in line else re.split(r"\s*[,;|]\s*", line.strip(), maxsplit=2)
        sdt = re.sub(r"[\s.\-]", "", parts[0])
        if re.fullmatch(r"\+?\d{8,12}", sdt):
            out.append({"sdt": sdt, "ten": parts[1].strip() if len(parts) > 1 else "",
                        "noiDung": parts[2].strip() if len(parts) > 2 else ""})
    return out


@transaction.atomic
def nhap_them(rows, phu_trach=None):
    """Thêm số vào cuối danh sách, bỏ qua số đã có. Trả về (số đã thêm, số trùng)."""
    da_co = set(NguoiNhan.objects.exclude(sdt__isnull=True).values_list("sdt", flat=True))
    base = NguoiNhan.objects.aggregate(m=Max("thu_tu"))["m"] or 0
    moi, trung = [], 0
    for r in rows:
        sdt = chuan_hoa_sdt(r.get("sdt"))
        if sdt and sdt in da_co:
            trung += 1
            continue
        if sdt:
            da_co.add(sdt)
        moi.append(NguoiNhan(thu_tu=base + len(moi) + 1, sdt_goc=str(r.get("sdt") or "").strip()[:32], sdt=sdt,
                             ten=str(r.get("ten") or "").strip()[:255], noi_dung=str(r.get("noiDung") or "").strip()[:5000],
                             phu_trach=phu_trach))
    NguoiNhan.objects.bulk_create(moi)
    tang_phien_ban()
    return len(moi), trung


# ---------- thống kê cho bảng điều khiển ----------
def trang_thai_so(sdt, kq):
    if not sdt:
        return "loi"
    k = kq.get(sdt)
    if not k:
        return "cho"
    return "loi" if k.tin_nhan == "Lỗi" else "da_gui"


def thong_ke():
    kq = {k.sdt: k for k in KetQua.objects.all()}
    nhom = {}
    tong = {"tong": 0, "cho": 0, "da_gui": 0, "loi": 0}
    for r in NguoiNhan.objects.select_related("phu_trach"):
        key = r.phu_trach_id or 0
        g = nhom.setdefault(key, {"id": key, "ten": str(r.phu_trach) if r.phu_trach else "Chưa giao",
                                  "tong": 0, "cho": 0, "da_gui": 0, "loi": 0})
        tt = trang_thai_so(r.sdt, kq)
        for d in (g, tong):
            d["tong"] += 1
            d[tt] += 1
    # Nhân viên chưa được giao số nào vẫn hiện trong bảng
    for u in nguoi_duoc_giao():
        nhom.setdefault(u.id, {"id": u.id, "ten": str(u), "tong": 0, "cho": 0, "da_gui": 0, "loi": 0})
    rows = sorted(nhom.values(), key=lambda g: (g["id"] == 0, g["ten"]))
    return {"tong": tong, "nhanVien": rows}


# ---------- lịch sử gửi ----------
def loc_lich_su(user, q="", tt="", tu="", den="", nguoi=""):
    """Lọc lịch sử: nhân viên chỉ thấy lịch sử mình gửi. tu/den dạng YYYY-MM-DD."""
    from datetime import date, datetime, time, timedelta

    from django.db.models import Q
    from django.utils.timezone import make_aware

    qs = LichSuGui.objects.select_related("nguoi_gui")
    if not user.co_quyen("all"):
        qs = qs.filter(nguoi_gui=user)
    elif nguoi:
        qs = qs.filter(nguoi_gui_id=int(nguoi))
    if q:
        qs = qs.filter(Q(sdt__icontains=q) | Q(ten__icontains=q) | Q(ten_zalo__icontains=q) | Q(noi_dung__icontains=q))
    thanh_cong = Q(tin_nhan="Đã gửi") | Q(tin_nhan="-", ket_ban="Đã gửi")
    if tt == "ok":
        qs = qs.filter(thanh_cong)
    elif tt == "loi":
        qs = qs.exclude(thanh_cong)
    # Lọc theo khoảng thời gian tính sẵn (không cần bảng múi giờ trong MySQL)
    try:
        if tu:
            qs = qs.filter(thoi_gian__gte=make_aware(datetime.combine(date.fromisoformat(tu), time.min)))
        if den:
            qs = qs.filter(thoi_gian__lt=make_aware(datetime.combine(date.fromisoformat(den) + timedelta(days=1), time.min)))
    except ValueError:
        raise ValueError("Ngày không hợp lệ") from None
    return qs


def lich_su_dict(h):
    return {
        "id": h.id, "sdt": h.sdt, "ten": h.ten, "tenZalo": h.ten_zalo, "ketBan": h.ket_ban, "tinNhan": h.tin_nhan,
        "noiDung": h.noi_dung, "ghiChu": h.ghi_chu, "thanhCong": h.thanh_cong, "taiKhoanZalo": h.tai_khoan_zalo,
        "thoiGian": localtime(h.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), "nguoiGui": str(h.nguoi_gui or ""),
    }


def excel_lich_su(qs):
    """File Excel lịch sử gửi (bytes)."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "LichSuGui"
    ws.append(["Thời gian", "SĐT", "Họ tên", "Tên Zalo", "Kết bạn", "Tin nhắn", "Kết quả", "Nội dung đã gửi", "Ghi chú",
               "Nhân viên gửi", "Gửi từ Zalo"])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F5FC4")
    for h in qs.order_by("thoi_gian", "id"):
        ws.append([localtime(h.thoi_gian).strftime("%d/%m/%Y %H:%M:%S"), h.sdt, h.ten, h.ten_zalo, h.ket_ban, h.tin_nhan,
                   "Thành công" if h.thanh_cong else "Lỗi", h.noi_dung, h.ghi_chu, str(h.nguoi_gui or ""), h.tai_khoan_zalo])
    for col, w in zip("ABCDEFGHIJK", (20, 13, 22, 22, 11, 11, 12, 60, 40, 20, 22)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2, min_col=8, max_col=9):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- gửi vào nhóm Zalo ----------
ANH_TOI_DA = 10
ANH_TOI_DA_MB = 5


def thu_muc_anh(user):
    """Ảnh gửi nhóm của từng tài khoản: data/anh-gui-nhom/<id>/ (worker đọc cùng thư mục này)."""
    from django.conf import settings
    return settings.ROOT_DIR / "data" / "anh-gui-nhom" / str(int(user.id))


def loai_anh(data):
    """Đuôi file theo nội dung ảnh (jpg/png/webp), None nếu không phải ảnh hỗ trợ."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def luu_anh(user, cau_hinh, f):
    """Lưu 1 ảnh tải lên vào thư mục của người dùng và thêm vào cấu hình gửi nhóm."""
    import uuid
    if len(cau_hinh.anh) >= ANH_TOI_DA:
        raise ValueError(f"Tối đa {ANH_TOI_DA} ảnh mỗi lần gửi")
    if f.size > ANH_TOI_DA_MB * 1024 * 1024:
        raise ValueError(f"Ảnh “{f.name}” lớn hơn {ANH_TOI_DA_MB} MB")
    data = f.read()
    ext = loai_anh(data)
    if not ext:
        raise ValueError(f"“{f.name}” không phải ảnh JPG, PNG hoặc WEBP")
    thu_muc = thu_muc_anh(user)
    thu_muc.mkdir(parents=True, exist_ok=True)
    ten = f"{uuid.uuid4().hex}.{ext}"
    (thu_muc / ten).write_bytes(data)
    cau_hinh.anh = [*cau_hinh.anh, ten]
    cau_hinh.save(update_fields=["anh", "cap_nhat"])


def xoa_anh(user, cau_hinh, ten):
    if ten not in cau_hinh.anh:
        raise ValueError("Không tìm thấy ảnh")
    cau_hinh.anh = [a for a in cau_hinh.anh if a != ten]
    cau_hinh.save(update_fields=["anh", "cap_nhat"])
    (thu_muc_anh(user) / ten).unlink(missing_ok=True)


def cau_hinh_nhom_dict(c):
    return {"noiDung": c.noi_dung, "nhom": c.nhom, "anh": c.anh}


def lich_su_nhom_dict(h):
    return {
        "id": h.id, "nhomId": h.nhom_id, "tenNhom": h.ten_nhom, "ketQua": h.ket_qua, "thanhCong": h.thanh_cong,
        "noiDung": h.noi_dung, "soAnh": h.so_anh, "ghiChu": h.ghi_chu, "taiKhoanZalo": h.tai_khoan_zalo,
        "thoiGian": localtime(h.thoi_gian).strftime("%H:%M:%S %d/%m/%Y"), "nguoiGui": str(h.nguoi_gui or ""),
    }
