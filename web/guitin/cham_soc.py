"""Bắt đầu gửi (nút bấm, lịch hẹn, tiếp tục đợt bị gián đoạn), xử lý kết quả / sự kiện worker báo về
(khách đồng ý kết bạn, khách trả lời), nhắc lại tự động và báo cáo theo thời gian."""
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone
from django.utils.timezone import localtime

from . import services as sv
from . import worker
from .models import (BoNhom, CaiDat, CauHinhNhom, ChienDich, DotGui, KetQua, KhachHang, KhongLienHe, LichHen,
                     LichSuGui, LichSuGuiNhom, NguoiDung, NguoiNhan, chuan_hoa_sdt, q_thanh_cong, thanh_cong)

CHUA_KET_NOI = "Bạn chưa kết nối Zalo. Quét QR để kết nối Zalo của bạn trước khi gửi"


# ---------- bắt đầu gửi ----------
def dong_gui(r, cfg, kieu="lan_dau", noi_dung=None, zalo_uid=""):
    """Một dòng trong đợt gửi gửi cho worker (nội dung đã điền sẵn)."""
    return {"sdt": r.sdt, "ten": r.ten, "noiDung": noi_dung if noi_dung is not None else sv.noi_dung_gui(r, cfg),
            "duAn": sv.du_an_cua(r, cfg), "loiMoi": sv.loi_moi_gui(r, cfg), "nhanVien": sv.nhan_vien_cua(r),
            "chienDichId": r.chien_dich_id, "kieu": kieu, "zaloUid": zalo_uid}


def cfg_gui(u, cfg):
    # Ảnh gửi kèm: ảnh riêng của người bấm gửi, chưa có thì ảnh của quản trị.
    # conLaiHomNay: worker tự dừng khi đủ hạn mức hôm nay (lớp chặn thứ hai, cả khi đợt gửi kéo qua nửa đêm)
    return {**sv.cau_hinh_cua(u, cfg), "anh": sv.anh_gui_kem(u, cfg), "conLaiHomNay": sv.con_lai_hom_nay(u, cfg),
            "hanMucNgay": sv.han_muc_ngay(u, cfg)}


def bat_dau_gui(u, pham_vi=None, chi_sdt=None, ghi_chu=""):
    """Gửi danh sách khách bằng Zalo của u. Trả về DotGui; lỗi thì ValueError (thông báo dễ hiểu)."""
    if worker.trang_thai(u.id).get("login") != "in":
        raise ValueError(CHUA_KET_NOI)
    cfg = CaiDat.lay()
    if not cfg.gui_ket_ban and not cfg.gui_tin_nhan:
        raise ValueError("Đang tắt cả kết bạn lẫn tin nhắn, hãy bật ít nhất một mục")
    chan = sv.chua_gui_duoc(u, cfg)
    if chan:
        raise ValueError(chan)
    batch = sv.cho_gui(u, pham_vi, sv.so_gui_toi_da(u, cfg), chi_sdt=chi_sdt)
    if not batch:
        raise ValueError("Không còn ai cần gửi trong phạm vi này")
    dot = DotGui.objects.create(nguoi_gui=u, loai=DotGui.CA_NHAN, tong=len(batch),
                                danh_sach=[r.sdt for r in batch], ghi_chu=ghi_chu[:255])
    try:
        worker.goi("POST", "/run", {"uid": u.id, "dotId": dot.id, "batch": [dong_gui(r, cfg) for r in batch],
                                    "cfg": cfg_gui(u, cfg), "runBy": {"id": u.id, "name": str(u)}})
    except Exception:
        dot.delete()
        raise
    return dot


def bat_dau_gui_nhom(u, bo_nhom=None, ghi_chu=""):
    """Gửi nội dung + ảnh vào các nhóm (đang chọn, hoặc theo bộ nhóm lưu sẵn) bằng Zalo của u."""
    if worker.trang_thai(u.id).get("login") != "in":
        raise ValueError(CHUA_KET_NOI)
    c = CauHinhNhom.cua(u)
    nhom = bo_nhom.nhom if bo_nhom else c.nhom
    if not nhom:
        raise ValueError("Chưa chọn nhóm nào để gửi")
    if not c.noi_dung and not c.anh and not any(g.get("noiDung") for g in nhom):
        raise ValueError("Chưa có nội dung hay ảnh để gửi")
    dot = DotGui.objects.create(nguoi_gui=u, loai=DotGui.NHOM, tong=len(nhom),
                                danh_sach=[str(g.get("id")) for g in nhom], ghi_chu=ghi_chu[:255])
    try:
        worker.goi("POST", "/run-groups", {
            "uid": u.id, "dotId": dot.id, "groups": nhom, "noiDung": c.noi_dung,
            "anh": [f"anh-gui-nhom/{u.id}/{a}" for a in c.anh],
            "cfg": sv.cau_hinh_cua(u, nhom=True), "runBy": {"id": u.id, "name": str(u)},
        })
    except Exception:
        dot.delete()
        raise
    return dot


# ---------- đợt gửi ----------
def dot_dict(d):
    con_lai = None
    if d.loai == DotGui.CA_NHAN and d.trang_thai in (DotGui.GIAN_DOAN, DotGui.DA_DUNG, DotGui.TU_DUNG):
        con_lai = len(sv.cho_gui(d.nguoi_gui, None, chi_sdt=d.danh_sach)) if d.nguoi_gui else 0
    return {"id": d.id, "loai": d.loai, "loaiTen": d.get_loai_display(), "trangThai": d.trang_thai,
            "trangThaiTen": d.get_trang_thai_display(), "tong": d.tong, "daGui": d.da_gui, "conLai": con_lai,
            "batDau": localtime(d.bat_dau).strftime("%H:%M %d/%m/%Y"),
            "ketThuc": localtime(d.ket_thuc).strftime("%H:%M %d/%m/%Y") if d.ket_thuc else None}


def danh_dau_gian_doan(trang_thai_worker=None):
    """Đợt đang chạy mà worker không còn chạy (worker khởi động lại, mất kết nối...) thì ghi là bị gián đoạn.
    trang_thai_worker: {uid: trạng thái} (worker.tat_ca()); None nếu worker không chạy."""
    qs = DotGui.objects.filter(trang_thai=DotGui.DANG_CHAY, bat_dau__lt=timezone.now() - timedelta(seconds=20))
    n = 0
    for d in qs:
        st = (trang_thai_worker or {}).get(d.nguoi_gui_id) or {}
        if not st.get("running") or (st.get("dotId") and st.get("dotId") != d.id):
            d.trang_thai, d.ket_thuc = DotGui.GIAN_DOAN, timezone.now()
            d.save(update_fields=["trang_thai", "ket_thuc"])
            sv.ghi_log(f"Đợt gửi #{d.id} của {d.nguoi_gui} bị gián đoạn (đã xử lý {d.da_gui}/{d.tong}). "
                       f"Bấm “Tiếp tục đợt bị gián đoạn” để gửi tiếp các số còn lại.", "warn", d.nguoi_gui)
            n += 1
    return n


def kiem_tra_dot_cua(u):
    """Gọi khi trang gửi tin tải trạng thái: phát hiện đợt của u bị gián đoạn."""
    if DotGui.objects.filter(nguoi_gui=u, trang_thai=DotGui.DANG_CHAY).exists():
        st = worker.trang_thai(u.id)
        if st.get("worker"):
            danh_dau_gian_doan({u.id: st})


def ket_thuc_dot(dot_id, trang_thai, da_gui=None):
    d = DotGui.objects.filter(id=dot_id).first()
    if not d:
        return
    d.trang_thai = trang_thai if trang_thai in dict(DotGui.TRANG_THAI) else DotGui.XONG
    d.ket_thuc = timezone.now()
    if da_gui is not None:
        d.da_gui = max(d.da_gui, int(da_gui))
    d.save(update_fields=["trang_thai", "ket_thuc", "da_gui"])
    sv.tang_phien_ban([d.nguoi_gui_id])


# ---------- kết quả worker báo về ----------
def _thoi_gian(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return timezone.now()


def ghi_ket_qua(b):
    """Một kết quả gửi 1 người (lần đầu / khi đồng ý kết bạn / nhắc lại). Ghi lịch sử (không ghi 2 lần cùng mã),
    trạng thái mới nhất, hồ sơ khách hàng và tiến độ đợt gửi."""
    sdt = chuan_hoa_sdt(b.get("sdt"))
    r = b.get("ketQua") or {}
    if not sdt:
        return
    loai = {"khi_dong_y": LichSuGui.KHI_DONG_Y, "nhac": LichSuGui.NHAC_LAI}.get(b.get("kieu"), LichSuGui.LAN_DAU)
    nguoi = NguoiDung.objects.filter(id=b.get("nguoiGuiId")).first()
    cd = ChienDich.objects.filter(id=b.get("chienDichId")).first() if b.get("chienDichId") else None
    zalo_uid = str(r.get("zaloUid") or b.get("zaloUid") or "")[:40]
    du_lieu = {
        "ten": str(r.get("ten") or "")[:255], "ten_zalo": str(r.get("tenZalo") or "")[:255],
        "ket_ban": str(r.get("ketBan") or "-")[:20], "tin_nhan": str(r.get("tinNhan") or "-")[:20],
        "noi_dung": str(r.get("noiDung") or ""), "ghi_chu": str(r.get("ghiChu") or ""),
        "thoi_gian": _thoi_gian(r.get("thoiGian")), "tai_khoan_zalo": str(r.get("taiKhoanZalo") or "")[:255],
        "nguoi_gui": nguoi, "zalo_uid": zalo_uid,
    }
    ma = str(b.get("ma") or "")[:40] or None
    if ma and LichSuGui.objects.filter(ma=ma).exists():
        return      # worker gửi lại kết quả đã lưu: bỏ qua
    dot = DotGui.objects.filter(id=b.get("dotId")).first() if b.get("dotId") else None
    with transaction.atomic():
        LichSuGui.objects.create(sdt=sdt, ma=ma, loai=loai, chien_dich=cd, dot=dot, **du_lieu)
        if loai == LichSuGui.LAN_DAU and not cd:
            # Trạng thái mới nhất của số “Chung”
            KetQua.objects.update_or_create(sdt=sdt, defaults=du_lieu)
        elif loai == LichSuGui.KHI_DONG_Y and du_lieu["tin_nhan"] == "Đã gửi":
            KetQua.objects.filter(sdt=sdt).exclude(tin_nhan="Đã gửi").update(tin_nhan="Đã gửi")
        if dot:
            DotGui.objects.filter(id=dot.id).update(da_gui=F("da_gui") + 1)
        kh, _ = KhachHang.objects.get_or_create(sdt=sdt)
        if zalo_uid:
            kh.zalo_uid = zalo_uid
        if du_lieu["ten_zalo"]:
            kh.ten_zalo = du_lieu["ten_zalo"]
        if nguoi:
            kh.nhan_vien = nguoi
        if loai == LichSuGui.LAN_DAU:
            kh.gui_dau_luc = kh.gui_dau_luc or du_lieu["thoi_gian"]
            if thanh_cong(du_lieu["ket_ban"], du_lieu["tin_nhan"]):
                kh.dat_trang_thai_tu_dong(KhachHang.DA_GUI)
        elif loai == LichSuGui.NHAC_LAI:
            kh.da_nhac_lai = True
        kh.save()
    phu_trach = NguoiNhan.objects.filter(sdt=sdt).values_list("phu_trach_id", flat=True)
    sv.tang_phien_ban([b.get("nguoiGuiId"), *phu_trach])


def ghi_ket_qua_nhom(b):
    r = b.get("ketQua") or {}
    LichSuGuiNhom.objects.create(
        nhom_id=str(b.get("nhomId") or "")[:64], ten_nhom=str(r.get("tenNhom") or "")[:255],
        ket_qua=str(r.get("ketQua") or "-")[:20], noi_dung=str(r.get("noiDung") or ""),
        so_anh=max(0, int(r.get("soAnh") or 0)), ghi_chu=str(r.get("ghiChu") or ""), thoi_gian=_thoi_gian(r.get("thoiGian")),
        tai_khoan_zalo=str(r.get("taiKhoanZalo") or "")[:255],
        nguoi_gui=NguoiDung.objects.filter(id=b.get("nguoiGuiId")).first(),
    )
    if b.get("dotId"):
        DotGui.objects.filter(id=b.get("dotId")).update(da_gui=F("da_gui") + 1)
    sv.tang_phien_ban([b.get("nguoiGuiId")])


# ---------- khách đồng ý kết bạn / trả lời ----------
def _khach_cua(zalo_uid, nguoi):
    """Khách mà chính Zalo của `nguoi` đã mời / nhắn. Khách của nhân viên khác kết bạn hoặc nhắn với Zalo này
    thì không tính (không ghi nhận, không tự gửi tin từ Zalo này)."""
    if not nguoi:
        return None
    for kh in KhachHang.objects.filter(zalo_uid=zalo_uid):
        if kh.nhan_vien_id == nguoi.id or LichSuGui.objects.filter(sdt=kh.sdt, nguoi_gui=nguoi).exists():
            return kh
    return None


def khach_dong_y(b):
    """Khách đồng ý kết bạn với Zalo của nhân viên. Trả về {"guiTin": {...}} nếu cần tự gửi tin cho khách."""
    cfg = CaiDat.lay()
    if not cfg.theo_doi_phan_hoi:
        return {}
    nguoi = NguoiDung.objects.filter(id=b.get("nguoiDungId")).first()
    uid = str(b.get("zaloUid") or "")[:40]
    kh = _khach_cua(uid, nguoi) if uid else None
    if not kh:
        return {}           # bạn bè khác, không phải khách tool đã mời
    if not kh.dong_y_luc:
        kh.dong_y_luc = timezone.now()
    kh.dat_trang_thai_tu_dong(KhachHang.DA_KET_BAN)
    kh.save()
    sv.ghi_log(f"Khách {kh.sdt} ({kh.ten_zalo or 'không tên'}) đã đồng ý kết bạn với Zalo của {nguoi}.", "ok", nguoi)
    sv.tang_phien_ban([nguoi.id if nguoi else None, kh.nhan_vien_id])
    gui = tin_khi_dong_y(kh, nguoi, cfg)
    return {"guiTin": gui} if gui else {}


def tin_khi_dong_y(kh, nguoi, cfg):
    """Tin tự gửi khi khách đồng ý kết bạn, None nếu không cần gửi:
    - có “Tin gửi khi đồng ý” (của chiến dịch, không có thì của cài đặt chung): gửi tin đó (một lần),
    - không có: chỉ gửi lại tin chính khi tin đầu chưa tới khách."""
    if not (cfg.gui_tin_khi_dong_y and cfg.gui_tin_nhan and nguoi):
        return None
    if KhongLienHe.objects.filter(sdt=kh.sdt).exists() or sv.chua_gui_duoc(nguoi, cfg):
        return None
    dau = LichSuGui.objects.filter(sdt=kh.sdt, loai=LichSuGui.LAN_DAU).order_by("-thoi_gian").first()
    if not dau:
        return None
    if LichSuGui.objects.filter(sdt=kh.sdt, loai=LichSuGui.KHI_DONG_Y, chien_dich=dau.chien_dich).exists():
        return None         # đã gửi tin khi đồng ý rồi
    r = (NguoiNhan.objects.filter(sdt=kh.sdt, chien_dich=dau.chien_dich).select_related("phu_trach", "chien_dich")
         .first() or NguoiNhan(sdt_goc=kh.sdt, sdt=kh.sdt, ten=dau.ten, phu_trach=nguoi))
    rieng = (dau.chien_dich.tin_khi_dong_y if dau.chien_dich else "") or cfg.tin_khi_dong_y
    if rieng.strip():
        noi_dung = sv.dien_cho(r, cfg, rieng)
    elif dau.tin_nhan != "Đã gửi":
        noi_dung = sv.noi_dung_gui(r, cfg)
    else:
        return None
    if not noi_dung:
        return None
    return {"sdt": kh.sdt, "zaloUid": kh.zalo_uid, "ten": r.ten, "noiDung": noi_dung, "chienDichId": dau.chien_dich_id,
            "anh": sv.anh_gui_kem(nguoi, cfg) if not rieng.strip() else []}


def khach_tra_loi(b):
    """Khách nhắn tin cho Zalo của nhân viên (không lưu nội dung)."""
    cfg = CaiDat.lay()
    if not cfg.theo_doi_phan_hoi:
        return
    nguoi = NguoiDung.objects.filter(id=b.get("nguoiDungId")).first()
    uid = str(b.get("zaloUid") or "")[:40]
    kh = _khach_cua(uid, nguoi) if uid else None
    if not kh:
        return
    lan_dau = not kh.tra_loi_luc
    kh.tra_loi_luc = timezone.now()
    kh.so_lan_tra_loi += 1
    kh.dat_trang_thai_tu_dong(KhachHang.DA_TRA_LOI)
    kh.save()
    if lan_dau:
        sv.ghi_log(f"Khách {kh.sdt} ({kh.ten_zalo or 'không tên'}) đã nhắn lại cho Zalo của {nguoi}. Ưu tiên chăm sóc!",
                   "ok", nguoi)
    sv.tang_phien_ban([nguoi.id if nguoi else None, kh.nhan_vien_id])


# ---------- nhắc lại tự động ----------
def can_nhac_lai(u, cfg, gioi_han=None):
    """Khách của u cần nhắc lại: đã gửi lần đầu đủ N ngày (N của chiến dịch, không có thì cài đặt chung), chưa trả lời,
    chưa nhắc, tin đã tới (tin đầu gửi được hoặc đã đồng ý kết bạn), không từ chối. Trả về [(NguoiNhan, nội dung, zalo_uid)]."""
    out = []
    now = timezone.now()
    nho_nhat = min([n for n in [cfg.nhac_lai_sau_ngay, *ChienDich.objects.filter(nhac_sau_ngay__gt=0)
                                .values_list("nhac_sau_ngay", flat=True)] if n] or [0])
    if not nho_nhat:
        return out
    tu_choi = set(KhongLienHe.objects.values_list("sdt", flat=True))
    qs = (KhachHang.objects.filter(nhan_vien=u, tra_loi_luc__isnull=True, da_nhac_lai=False,
                                   gui_dau_luc__lte=now - timedelta(days=nho_nhat))
          .exclude(zalo_uid="").exclude(trang_thai__in=[KhachHang.KHONG_QUAN_TAM, KhachHang.DA_MUA]))
    for kh in qs[:1000]:
        if kh.sdt in tu_choi:
            continue
        dau = LichSuGui.objects.filter(sdt=kh.sdt, nguoi_gui=u, loai=LichSuGui.LAN_DAU).order_by("-thoi_gian").first()
        if not dau or not (dau.tin_nhan == "Đã gửi" or kh.dong_y_luc):
            continue
        cd = dau.chien_dich
        so_ngay = (cd.nhac_sau_ngay if cd and cd.nhac_sau_ngay else 0) or cfg.nhac_lai_sau_ngay
        mau = (cd.noi_dung_nhac if cd else "") or cfg.noi_dung_nhac_lai
        if not so_ngay or not mau.strip() or dau.thoi_gian > now - timedelta(days=so_ngay):
            continue
        if cd and not cd.con_hieu_luc():
            continue
        r = (NguoiNhan.objects.filter(sdt=kh.sdt, chien_dich=cd).select_related("phu_trach", "chien_dich").first()
             or NguoiNhan(sdt_goc=kh.sdt, sdt=kh.sdt, ten=dau.ten, phu_trach=u, chien_dich=cd))
        out.append((r, sv.dien_cho(r, cfg, mau), kh.zalo_uid))
        if gioi_han and len(out) >= gioi_han:
            break
    return out


def bat_dau_nhac_lai(u, cfg):
    """Chạy một đợt nhắc lại cho khách của u (nếu có). Trả về DotGui hoặc None."""
    if sv.chua_gui_duoc(u, cfg) or not cfg.gui_tin_nhan:
        return None
    cac = can_nhac_lai(u, cfg, sv.so_gui_toi_da(u, cfg) or 200)
    if not cac:
        return None
    dot = DotGui.objects.create(nguoi_gui=u, loai=DotGui.NHAC_LAI, tong=len(cac), danh_sach=[r.sdt for r, _, _ in cac],
                                ghi_chu="Nhắc lại khách chưa trả lời")
    try:
        worker.goi("POST", "/run", {"uid": u.id, "dotId": dot.id,
                                    "batch": [dong_gui(r, cfg, "nhac", nd, uid) for r, nd, uid in cac],
                                    "cfg": cfg_gui(u, cfg), "runBy": {"id": u.id, "name": f"{u} (nhắc lại tự động)"}})
    except Exception:
        dot.delete()
        raise
    return dot


def trong_khung_gio(cfg, luc=None):
    t = localtime(luc or timezone.now())
    if cfg.nghi_chu_nhat and t.weekday() == 6:
        return False
    return cfg.gio_bat_dau <= t.time() < cfg.gio_ket_thuc


# ---------- chạy định kỳ (mỗi phút, manage.py chay_lich) ----------
def chay_lich(now=None):
    """Đến giờ thì chạy lịch hẹn gửi; nhắc lại khách chưa trả lời; phát hiện đợt gửi bị gián đoạn.
    Trả về danh sách việc đã làm (để ghi ra màn hình)."""
    now = now or timezone.now()
    viec = []
    from .bao_cao_email import chay_dinh_ky
    bc = chay_dinh_ky(now)          # báo cáo Excel qua email (không cần Zalo worker)
    if bc:
        viec.append(bc)
    st = worker.tat_ca()
    danh_dau_gian_doan(st)
    if st is None:
        return viec + ["Zalo worker không chạy"]
    cfg = CaiDat.lay()
    ranh = {uid for uid, s in st.items() if s.get("login") == "in" and not s.get("running")}

    for lh in LichHen.objects.filter(dang_bat=True).select_related("nguoi", "bo_nhom"):
        toi = lh.lan_toi(lh.lan_chay_cuoi or lh.tao_luc - timedelta(seconds=1))
        if not toi or toi > now:
            continue
        u = lh.nguoi
        try:
            if not u.is_active or not u.co_quyen("send"):
                raise ValueError("Tài khoản không còn quyền gửi")
            if u.id not in ranh:
                raise ValueError(CHUA_KET_NOI if (st.get(u.id) or {}).get("login") != "in" else "Zalo đang gửi đợt khác")
            dot = (bat_dau_gui_nhom(u, lh.bo_nhom, f"Lịch hẹn #{lh.id}") if lh.loai == LichHen.NHOM
                   else bat_dau_gui(u, ghi_chu=f"Lịch hẹn #{lh.id}"))
            ranh.discard(u.id)
            kq = f"Đã bắt đầu đợt #{dot.id} ({dot.tong} {'nhóm' if lh.loai == LichHen.NHOM else 'người'})"
            sv.ghi_log(f"Lịch hẹn: bắt đầu gửi bằng Zalo của {u} ({lh.mo_ta()}).", "ok", u)
        except (ValueError, worker.WorkerLoi) as e:
            kq = f"Không chạy được: {e}"
            sv.ghi_log(f"Lịch hẹn gửi ({lh.mo_ta()}) không chạy được: {e}", "warn", u)
        lh.lan_chay_cuoi, lh.ket_qua_cuoi = now, kq[:255]
        if lh.lap == LichHen.MOT_LAN:
            lh.dang_bat = False
        lh.save(update_fields=["lan_chay_cuoi", "ket_qua_cuoi", "dang_bat"])
        viec.append(f"Lịch #{lh.id}: {kq}")

    # Nhắc lại: chỉ trong khung giờ gửi, khi Zalo của nhân viên đang rảnh
    if trong_khung_gio(cfg, now):
        for u in NguoiDung.objects.filter(id__in=ranh, is_active=True):
            try:
                dot = bat_dau_nhac_lai(u, cfg)
            except worker.WorkerLoi as e:
                viec.append(f"Nhắc lại cho {u}: {e}")
                continue
            if dot:
                sv.ghi_log(f"Tự gửi tin nhắc lại cho {dot.tong} khách chưa trả lời bằng Zalo của {u}.", "ok", u)
                viec.append(f"Nhắc lại {dot.tong} khách của {u}")
    return viec


# ---------- lịch hẹn, bộ nhóm ----------
def lich_hen_dict(lh):
    toi = lh.lan_toi(lh.lan_chay_cuoi or lh.tao_luc - timedelta(seconds=1))
    return {"id": lh.id, "loai": lh.loai, "lap": lh.lap, "moTa": lh.mo_ta(), "dangBat": lh.dang_bat,
            "boNhom": lh.bo_nhom.ten if lh.bo_nhom else None,
            "lanToi": localtime(toi).strftime("%H:%M %d/%m/%Y") if toi else None,
            "lanCuoi": localtime(lh.lan_chay_cuoi).strftime("%H:%M %d/%m/%Y") if lh.lan_chay_cuoi else None,
            "ketQuaCuoi": lh.ket_qua_cuoi}


def tao_lich_hen(u, b):
    """Tạo lịch hẹn từ dữ liệu trang gửi tin: {loai, lap, luc ("YYYY-MM-DDTHH:MM"), gio ("HH:MM"), thu: [0..6], boNhom}."""
    from datetime import time as dtime
    loai = b.get("loai") if b.get("loai") in dict(LichHen.LOAI) else LichHen.CA_NHAN
    lap = b.get("lap") if b.get("lap") in dict(LichHen.LAP) else LichHen.MOT_LAN
    lh = LichHen(nguoi=u, loai=loai, lap=lap)
    try:
        if lap == LichHen.MOT_LAN:
            lh.luc = timezone.make_aware(datetime.fromisoformat(str(b.get("luc"))))
            if lh.luc <= timezone.now():
                raise ValueError("Chọn thời điểm trong tương lai")
        else:
            h, m = (int(x) for x in str(b.get("gio")).split(":")[:2])
            lh.gio = dtime(h, m)
            if lap == LichHen.HANG_TUAN:
                lh.thu = sorted({int(x) for x in (b.get("thu") or []) if 0 <= int(x) <= 6})
                if not lh.thu:
                    raise ValueError("Chọn ít nhất một ngày trong tuần")
    except (TypeError, ValueError) as e:
        raise ValueError(str(e) if "Chọn" in str(e) else "Thời gian không hợp lệ") from None
    if loai == LichHen.NHOM and b.get("boNhom"):
        lh.bo_nhom = BoNhom.objects.filter(nguoi=u, id=int(b["boNhom"])).first()
        if not lh.bo_nhom:
            raise ValueError("Không tìm thấy bộ nhóm")
    if LichHen.objects.filter(nguoi=u, dang_bat=True).count() >= 20:
        raise ValueError("Tối đa 20 lịch hẹn đang bật")
    lh.save()
    sv.ghi_log(f"{u} đã hẹn giờ gửi: {lh}.", nguoi_dung=u)
    return lh


def bo_nhom_dict(b):
    return {"id": b.id, "ten": b.ten, "nhom": b.nhom}


# ---------- báo cáo theo thời gian ----------
def bao_cao(tu, den, qs_nguoi=None):
    """Số liệu cho bảng điều khiển: theo ngày, theo nhân viên, theo dự án, theo chiến dịch (khoảng tu..den, date)."""
    bd = timezone.make_aware(datetime.combine(tu, datetime.min.time()))
    kt = timezone.make_aware(datetime.combine(den + timedelta(days=1), datetime.min.time()))
    ls = LichSuGui.objects.filter(thoi_gian__gte=bd, thoi_gian__lt=kt)
    if qs_nguoi is not None:
        ls = ls.filter(nguoi_gui__in=qs_nguoi)
    ok = q_thanh_cong()
    theo_ngay = {}
    for h in ls.values("thoi_gian", "ket_ban", "tin_nhan"):
        ngay = localtime(h["thoi_gian"]).date().isoformat()
        d = theo_ngay.setdefault(ngay, {"ngay": ngay, "gui": 0, "thanhCong": 0, "dongY": 0, "traLoi": 0})
        d["gui"] += 1
        d["thanhCong"] += thanh_cong(h["ket_ban"], h["tin_nhan"])
    kh = KhachHang.objects.all()
    if qs_nguoi is not None:
        kh = kh.filter(nhan_vien__in=qs_nguoi)
    for f, k in (("dong_y_luc", "dongY"), ("tra_loi_luc", "traLoi")):
        for t in kh.filter(**{f"{f}__gte": bd, f"{f}__lt": kt}).values_list(f, flat=True):
            ngay = localtime(t).date().isoformat()
            theo_ngay.setdefault(ngay, {"ngay": ngay, "gui": 0, "thanhCong": 0, "dongY": 0, "traLoi": 0})[k] += 1
    ngay_list = []
    d = tu
    while d <= den:
        ngay_list.append(theo_ngay.get(d.isoformat(), {"ngay": d.isoformat(), "gui": 0, "thanhCong": 0, "dongY": 0, "traLoi": 0}))
        d += timedelta(days=1)

    def nhom_theo(truong, ten_mac_dinh):
        rows = (ls.filter(loai=LichSuGui.LAN_DAU).values(truong)
                .annotate(gui=Count("id"), thanhCong=Count("id", filter=ok), khach=Count("sdt", distinct=True)))
        return {r[truong]: {"gui": r["gui"], "thanhCong": r["thanhCong"], "khach": r["khach"]} for r in rows}

    # Theo nhân viên: gửi, thành công, đồng ý, trả lời (khách gửi lần đầu trong khoảng)
    nv = nhom_theo("nguoi_gui", "")
    khach_nv = (kh.filter(gui_dau_luc__gte=bd, gui_dau_luc__lt=kt).values("nhan_vien")
                .annotate(dongY=Count("sdt", filter=Q(dong_y_luc__isnull=False)),
                          traLoi=Count("sdt", filter=Q(tra_loi_luc__isnull=False))))
    for r in khach_nv:
        nv.setdefault(r["nhan_vien"], {"gui": 0, "thanhCong": 0, "khach": 0}).update(dongY=r["dongY"], traLoi=r["traLoi"])
    ten = {u.id: str(u) for u in NguoiDung.objects.filter(id__in=[k for k in nv if k])}
    nhan_vien = sorted(({"id": k, "ten": ten.get(k, "(đã xoá)"), "dongY": 0, "traLoi": 0, **v} for k, v in nv.items() if k),
                       key=lambda x: (-x["thanhCong"], x["ten"]))

    # Theo dự án / chiến dịch: lấy dự án của dòng người nhận tương ứng
    du_an = {}
    ls_dau = list(ls.filter(loai=LichSuGui.LAN_DAU).values("sdt", "ket_ban", "tin_nhan", "chien_dich__du_an"))
    da_cua = dict(NguoiNhan.objects.filter(sdt__in={h["sdt"] for h in ls_dau}).exclude(du_an="")
                  .values_list("sdt", "du_an"))
    mac_dinh = CaiDat.lay().ten_du_an or "(không có dự án)"
    for h in ls_dau:
        da = h["chien_dich__du_an"] or da_cua.get(h["sdt"]) or mac_dinh
        g = du_an.setdefault(da, {"duAn": da, "gui": 0, "thanhCong": 0})
        g["gui"] += 1
        g["thanhCong"] += thanh_cong(h["ket_ban"], h["tin_nhan"])
    chien_dich = []
    for c in ChienDich.objects.all()[:50]:
        lsc = LichSuGui.objects.filter(chien_dich=c, loai=LichSuGui.LAN_DAU)
        sdts = lsc.values("sdt").distinct()
        chien_dich.append({"id": c.id, "ten": c.ten, "dangChay": c.con_hieu_luc(),
                           "tong": NguoiNhan.objects.filter(chien_dich=c).count(),
                           "gui": lsc.values("sdt").distinct().count(),
                           "thanhCong": lsc.filter(ok).values("sdt").distinct().count(),
                           "dongY": KhachHang.objects.filter(sdt__in=sdts, dong_y_luc__isnull=False).count(),
                           "traLoi": KhachHang.objects.filter(sdt__in=sdts, tra_loi_luc__isnull=False).count()})
    return {"theoNgay": ngay_list, "nhanVien": nhan_vien,
            "duAn": sorted(du_an.values(), key=lambda x: -x["gui"]), "chienDich": chien_dich,
            "tong": {k: sum(x[k] for x in ngay_list) for k in ("gui", "thanhCong", "dongY", "traLoi")}}
