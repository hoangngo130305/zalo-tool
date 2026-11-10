"""Test các tính năng: chiến dịch, biến {xungho} / cột Excel, chăm sóc tự động (đồng ý kết bạn, trả lời, nhắc lại),
đợt gửi, lịch hẹn, bộ nhóm, trưởng nhóm, báo cáo, quyền xuất Excel, xoá dữ liệu khách, tình trạng hệ thống."""
import json
from datetime import datetime, time, timedelta
from unittest import mock

from django.conf import settings
from django.test import Client
from django.utils import timezone

from . import cham_soc as cs
from . import services as sv
from .models import (BoNhom, CauHinhNhom, ChienDich, DotGui, KetQua, KhachHang, KhongLienHe, LichHen, LichSuGui,
                     MauTinNhan, NguoiDung, NguoiNhan)
from .tests import DANG_NHAP, CoSo


class ChienDichVaBien(CoSo):
    def test_cung_so_hai_chien_dich(self):
        a = ChienDich.objects.create(ten="A", du_an="Palm City", noi_dung="Chào {xungho} {ten}, dự án {duan} căn {canho}")
        b = ChienDich.objects.create(ten="B")
        self.cfg.khong_nhan_lai_ngay = 0
        self.cfg.save()
        NguoiNhan.objects.create(sdt_goc="0901111111", ten="Nam", xung_ho="anh", them={"canho": "A12"}, phu_trach=self.nv, chien_dich=a)
        NguoiNhan.objects.create(sdt_goc="0901111111", ten="Nam", phu_trach=self.nv, chien_dich=b)
        ds = sv.cho_gui(self.nv, "all")
        self.assertEqual(len(ds), 1)                    # cùng số trong 1 đợt chỉ gửi 1 lần
        self.assertEqual(sv.noi_dung_gui(ds[0], self.cfg), "Chào anh Nam, dự án Palm City căn A12")
        # Gửi trong chiến dịch A rồi: chiến dịch B vẫn còn phải gửi
        LichSuGui.objects.create(sdt="0901111111", chien_dich=a, ket_ban="Đã gửi", tin_nhan="Đã gửi", thoi_gian=timezone.now())
        self.assertEqual([r.chien_dich_id for r in sv.cho_gui(self.nv, "all")], [b.id])

    def test_khong_nhan_lai_trong_n_ngay(self):
        a = ChienDich.objects.create(ten="A")
        b = ChienDich.objects.create(ten="B")
        self.cfg.khong_nhan_lai_ngay = 7
        self.cfg.save()
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv, chien_dich=b)
        LichSuGui.objects.create(sdt="0901111111", chien_dich=a, ket_ban="Đã gửi", tin_nhan="Đã gửi",
                                 thoi_gian=timezone.now() - timedelta(days=3))
        self.assertEqual(sv.cho_gui(self.nv, "all"), [])
        LichSuGui.objects.update(thoi_gian=timezone.now() - timedelta(days=8))
        self.assertEqual(len(sv.cho_gui(self.nv, "all")), 1)

    def test_chien_dich_het_han_khong_gui(self):
        a = ChienDich.objects.create(ten="A", ket_thuc=timezone.localdate() - timedelta(days=1))
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv, chien_dich=a)
        self.assertEqual(sv.cho_gui(self.nv, "all"), [])

    def test_gui_lai_trong_chien_dich(self):
        a = ChienDich.objects.create(ten="A")
        r = NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv, chien_dich=a)
        LichSuGui.objects.create(sdt="0901111111", chien_dich=a, ket_ban="Lỗi", tin_nhan="Lỗi", thoi_gian=timezone.now())
        self.assertEqual(sv.cho_gui(self.nv, "all"), [])
        with mock.patch("guitin.views.worker.trang_thai", return_value=DANG_NHAP):
            self.post(self.client_cua(self.nv), "/api/reset", {"sdt": "0901111111", "id": r.id})
        self.assertEqual(len(sv.cho_gui(self.nv, "all")), 1)
        self.assertEqual(LichSuGui.objects.count(), 1)      # lịch sử cũ vẫn giữ

    def test_nhan_vien_them_so_vao_chien_dich(self):
        a = ChienDich.objects.create(ten="A")
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv2)              # số Chung của người khác
        kq = sv.luu_danh_sach(self.nv, [{"sdt": "0901111111", "chienDich": a.id}, {"sdt": "0902222222", "chienDich": 999}])
        self.assertEqual(kq["trungNguoiKhac"], [])                                     # khác chiến dịch: được thêm
        self.assertEqual(dict(self.nv.nguoi_nhan.values_list("sdt", "chien_dich_id")),
                         {"0901111111": a.id, "0902222222": None})                     # chiến dịch không hợp lệ -> Chung

    def test_danh_sach_tra_ket_qua_theo_tung_dong(self):
        a = ChienDich.objects.create(ten="A")
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv, chien_dich=a)
        self.ket_qua("0901111111")              # số Chung đã gửi; trong chiến dịch A thì chưa
        ds = {r["chienDich"]: r for r in sv.danh_sach(self.nv)}
        self.assertIsNotNone(ds[None]["ketQua"])
        self.assertIsNone(ds[a.id]["ketQua"])


class ChamSocTuDong(CoSo):
    def bao(self, data):
        return Client().post("/api/worker/event", json.dumps(data), content_type="application/json",
                             headers={"X-Worker-Token": settings.WORKER_TOKEN}).json()

    def gui_dau(self, sdt="0901111111", tin="Đã gửi", luc=None, cd=None):
        r = NguoiNhan.objects.create(sdt_goc=sdt, ten="Nam", phu_trach=self.nv, chien_dich=cd)
        dot = DotGui.objects.create(nguoi_gui=self.nv, tong=1, danh_sach=[sdt])
        self.bao({"type": "result", "sdt": sdt, "nguoiGuiId": self.nv.id, "dotId": dot.id, "kieu": "lan_dau",
                  "chienDichId": cd.id if cd else None,
                  "ketQua": {"ketBan": "Đã gửi", "tinNhan": tin, "zaloUid": f"u{sdt}", "tenZalo": "Nam Zalo",
                             "thoiGian": (luc or timezone.now()).isoformat()}})
        return r, dot

    def test_ket_qua_cap_nhat_khach_va_dot(self):
        _, dot = self.gui_dau()
        kh = KhachHang.objects.get(sdt="0901111111")
        self.assertEqual((kh.zalo_uid, kh.trang_thai, kh.nhan_vien_id), ("u0901111111", KhachHang.DA_GUI, self.nv.id))
        dot.refresh_from_db()
        self.assertEqual(dot.da_gui, 1)
        self.bao({"type": "dot_ket_thuc", "dotId": dot.id, "trangThai": "xong", "daGui": 1})
        dot.refresh_from_db()
        self.assertEqual(dot.trang_thai, DotGui.XONG)

    def test_dong_y_ket_ban_gui_lai_tin_khi_tin_dau_khong_toi(self):
        self.gui_dau(tin="Lỗi")
        out = self.bao({"type": "ket_ban", "zaloUid": "u0901111111", "nguoiDungId": self.nv.id})
        self.assertEqual(out["guiTin"]["noiDung"], "Chào Nam!")
        self.assertEqual(KhachHang.objects.get(sdt="0901111111").trang_thai, KhachHang.DA_KET_BAN)
        # Worker báo kết quả tin tự gửi: không gửi lần nữa khi khách đồng ý lại
        self.bao({"type": "result", "sdt": "0901111111", "nguoiGuiId": self.nv.id, "kieu": "khi_dong_y",
                  "ketQua": {"ketBan": "Đã kết bạn", "tinNhan": "Đã gửi"}})
        self.assertEqual(KetQua.objects.get(sdt="0901111111").tin_nhan, "Đã gửi")
        self.assertNotIn("guiTin", self.bao({"type": "ket_ban", "zaloUid": "u0901111111", "nguoiDungId": self.nv.id}))

    def test_dong_y_khi_tin_dau_da_toi_thi_khong_gui_lai(self):
        self.gui_dau(tin="Đã gửi")
        self.assertNotIn("guiTin", self.bao({"type": "ket_ban", "zaloUid": "u0901111111", "nguoiDungId": self.nv.id}))
        # Có soạn "tin khi đồng ý" (của chiến dịch) thì luôn gửi tin đó
        cd = ChienDich.objects.create(ten="A", tin_khi_dong_y="Cảm ơn {ten} đã kết bạn")
        self.gui_dau("0902222222", cd=cd)
        out = self.bao({"type": "ket_ban", "zaloUid": "u0902222222", "nguoiDungId": self.nv.id})
        self.assertEqual(out["guiTin"]["noiDung"], "Cảm ơn Nam đã kết bạn")
        self.assertEqual(out["guiTin"]["chienDichId"], cd.id)

    def test_ban_be_khac_bo_qua(self):
        self.assertEqual(self.bao({"type": "ket_ban", "zaloUid": "u-la", "nguoiDungId": self.nv.id}), {"ok": True})

    def test_tra_loi_va_nhac_lai(self):
        self.cfg.nhac_lai_sau_ngay = 3
        self.cfg.noi_dung_nhac_lai = "{ten} ơi, anh xem thông tin chưa ạ?"
        self.cfg.save()
        self.gui_dau("0901111111", luc=timezone.now() - timedelta(days=4))
        self.gui_dau("0902222222", luc=timezone.now() - timedelta(days=4))
        self.gui_dau("0903333333", luc=timezone.now() - timedelta(days=1))          # chưa đủ 3 ngày
        self.bao({"type": "tra_loi", "zaloUid": "u0902222222", "nguoiDungId": self.nv.id})     # đã trả lời
        kh = KhachHang.objects.get(sdt="0902222222")
        self.assertEqual((kh.trang_thai, kh.so_lan_tra_loi), (KhachHang.DA_TRA_LOI, 1))
        cac = cs.can_nhac_lai(self.nv, self.cfg)
        self.assertEqual([(r.sdt, nd, uid) for r, nd, uid in cac],
                         [("0901111111", "Nam ơi, anh xem thông tin chưa ạ?", "u0901111111")])
        with mock.patch("guitin.cham_soc.worker.goi") as goi:
            dot = cs.bat_dau_nhac_lai(self.nv, self.cfg)
        body = goi.call_args[0][2]
        self.assertEqual((dot.loai, body["batch"][0]["kieu"], body["batch"][0]["zaloUid"], body["cfg"]["anh"]),
                         (DotGui.NHAC_LAI, "nhac", "u0901111111", []))
        self.bao({"type": "result", "sdt": "0901111111", "nguoiGuiId": self.nv.id, "kieu": "nhac",
                  "ketQua": {"ketBan": "-", "tinNhan": "Đã gửi"}})
        self.assertTrue(KhachHang.objects.get(sdt="0901111111").da_nhac_lai)
        self.assertEqual(cs.can_nhac_lai(self.nv, self.cfg), [])

    def test_nhan_vien_doi_trang_thai_khach(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        r = self.post(self.client_cua(self.nv), "/api/khach", {"sdt": "0901111111", "trangThai": "hen_gap"})
        self.assertEqual(r.json()["khach"]["trangThai"], "hen_gap")
        self.assertEqual(self.post(self.client_cua(self.nv2), "/api/khach", {"sdt": "0901111111", "trangThai": "moi"}).status_code, 400)
        # Trạng thái nhân viên chọn không bị tool ghi đè khi khách trả lời
        kh = KhachHang.objects.get(sdt="0901111111")
        kh.dat_trang_thai_tu_dong(KhachHang.DA_TRA_LOI)
        self.assertEqual(kh.trang_thai, "hen_gap")


class DotGuiVaLichHen(CoSo):
    @mock.patch("guitin.cham_soc.worker.goi")
    @mock.patch("guitin.cham_soc.worker.trang_thai", return_value=DANG_NHAP)
    def test_tiep_tuc_dot_bi_gian_doan(self, _tt, goi):
        for s in ("0901111111", "0902222222", "0903333333"):
            NguoiNhan.objects.create(sdt_goc=s, phu_trach=self.nv)
        dot = cs.bat_dau_gui(self.nv)
        self.assertEqual((dot.tong, goi.call_args[0][2]["dotId"]), (3, dot.id))
        KetQua.objects.create(sdt="0901111111", ket_ban="Đã gửi", tin_nhan="Đã gửi", thoi_gian=timezone.now())
        DotGui.objects.filter(id=dot.id).update(bat_dau=timezone.now() - timedelta(minutes=5))
        cs.danh_dau_gian_doan({self.nv.id: {"running": False}})        # worker khởi động lại giữa đợt
        dot.refresh_from_db()
        self.assertEqual(dot.trang_thai, DotGui.GIAN_DOAN)
        self.assertEqual(cs.dot_dict(dot)["conLai"], 2)
        r = self.post(self.client_cua(self.nv), "/api/dot/tiep-tuc", {"dot": dot.id})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([x["sdt"] for x in goi.call_args[0][2]["batch"]], ["0902222222", "0903333333"])

    def test_lan_toi_cua_lich(self):
        tz = timezone.get_current_timezone()
        thu_hai = timezone.make_aware(datetime(2026, 11, 2, 9, 0), tz)              # thứ Hai 9:00
        lh = LichHen(nguoi=self.nv, lap=LichHen.HANG_TUAN, gio=time(8, 0), thu=[0, 3])
        self.assertEqual(timezone.localtime(lh.lan_toi(thu_hai)), timezone.make_aware(datetime(2026, 11, 5, 8, 0), tz))
        lh.lap = LichHen.HANG_NGAY
        self.assertEqual(timezone.localtime(lh.lan_toi(thu_hai)), timezone.make_aware(datetime(2026, 11, 3, 8, 0), tz))

    def test_tao_va_chay_lich_hen(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        c = self.client_cua(self.nv)
        luc = (timezone.localtime() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
        r = self.post(c, "/api/lich-hen", {"loai": "ca_nhan", "lap": "mot_lan", "luc": luc})
        self.assertEqual(len(r.json()["lichHen"]), 1)
        self.assertEqual(self.post(c, "/api/lich-hen", {"lap": "mot_lan", "luc": "2020-01-01T08:00"}).status_code, 400)
        st = {self.nv.id: {**DANG_NHAP}}
        with mock.patch("guitin.cham_soc.worker.tat_ca", return_value=st), \
                mock.patch("guitin.cham_soc.worker.trang_thai", return_value=DANG_NHAP), \
                mock.patch("guitin.cham_soc.worker.goi") as goi:
            self.assertEqual(cs.chay_lich(), [])                              # chưa đến giờ
            viec = cs.chay_lich(timezone.now() + timedelta(minutes=31))
        self.assertIn("Đã bắt đầu", viec[0])
        self.assertEqual(goi.call_args[0][1], "/run")
        self.assertFalse(LichHen.objects.get().dang_bat)                     # lịch một lần: chạy xong thì tắt

    def test_lich_gui_nhom_theo_bo_nhom(self):
        bo = BoNhom.objects.create(nguoi=self.nv, ten="BĐS Q7", nhom=[{"id": "111", "ten": "Nhóm 1", "noiDung": "Riêng nhóm 1"}])
        CauHinhNhom.objects.create(nguoi_dung=self.nv, noi_dung="Nội dung chung")
        LichHen.objects.create(nguoi=self.nv, loai=LichHen.NHOM, lap=LichHen.HANG_NGAY, gio=time(0, 0), bo_nhom=bo)
        LichHen.objects.update(tao_luc=timezone.now() - timedelta(days=2))
        with mock.patch("guitin.cham_soc.worker.tat_ca", return_value={self.nv.id: dict(DANG_NHAP)}), \
                mock.patch("guitin.cham_soc.worker.trang_thai", return_value=DANG_NHAP), \
                mock.patch("guitin.cham_soc.worker.goi") as goi:
            cs.chay_lich()
        body = goi.call_args[0][2]
        self.assertEqual((goi.call_args[0][1], body["groups"][0]["noiDung"]), ("/run-groups", "Riêng nhóm 1"))

    def test_bo_nhom_api(self):
        c = self.client_cua(self.nv)
        r = self.post(c, "/api/nhom/bo-nhom", {"ten": "Q7", "nhom": [{"id": "111", "ten": "A", "noiDung": "x"}, {"id": "abc"}]})
        self.assertEqual(r.json()["boNhom"][0]["nhom"], [{"id": "111", "ten": "A", "noiDung": "x"}])
        r = self.post(c, "/api/nhom/bo-nhom", {"xoa": r.json()["boNhom"][0]["id"]})
        self.assertEqual(r.json()["boNhom"], [])

    def test_gui_nhom_dung_thoi_gian_rieng(self):
        self.cfg.nhom_cho_toi_thieu_giay = 200
        self.cfg.nhom_cho_toi_da_giay = 300
        self.cfg.save()
        CauHinhNhom.objects.create(nguoi_dung=self.nv, noi_dung="Chào", nhom=[{"id": "111", "ten": "A"}])
        with mock.patch("guitin.cham_soc.worker.trang_thai", return_value=DANG_NHAP), \
                mock.patch("guitin.cham_soc.worker.goi") as goi:
            r = self.post(self.client_cua(self.nv), "/api/nhom/gui", {})
        self.assertEqual(r.status_code, 200, r.content)
        cfg = goi.call_args[0][2]["cfg"]
        self.assertEqual((cfg["choToiThieuGiay"], cfg["choToiDaGiay"]), (200, 300))


class DoiVaBaoCao(CoSo):
    def setUp(self):
        super().setUp()
        self.tn = NguoiDung.objects.create_user("tn@x.vn", "matkhau1", ho_ten="Trưởng", vai_tro=NguoiDung.TRUONG_NHOM, doi="A")
        NguoiDung.objects.filter(id=self.nv.id).update(doi="A")           # nv cùng đội, nv2 khác đội

    def test_truong_nhom_giao_va_chia_deu(self):
        for s in ("0901111111", "0902222222"):
            NguoiNhan.objects.create(sdt_goc=s, phu_trach=self.tn)
        ngoai = NguoiNhan.objects.create(sdt_goc="0903333333", phu_trach=self.nv2)
        c = self.client_cua(self.tn)
        r = c.get("/api/doi").json()
        self.assertEqual({x["id"] for x in r["thanhVien"]}, {self.tn.id, self.nv.id})
        self.assertNotIn(ngoai.id, [x["id"] for x in r["rows"]])
        self.assertNotIn(self.nv2.id, [x["id"] for x in r["thongKe"]])     # không lộ tên người đội khác
        self.assertEqual(self.post(c, "/api/doi", {"ids": [ngoai.id], "nguoi": self.nv2.id}).status_code, 400)
        self.post(c, "/api/doi", {"chiaDeu": True})
        self.assertEqual(self.nv.nguoi_nhan.count(), 2)
        self.assertTrue(all(self.nv.nguoi_nhan.values_list("quan_tri_giao", flat=True)))
        self.assertEqual(self.client_cua(self.nv).get("/api/doi").status_code, 403)
        self.assertEqual(self.client_cua(self.tn).get("/doi/").status_code, 200)

    def test_truong_nhom_xem_lich_su_ca_doi(self):
        self.ket_qua("0901111111", nguoi=self.nv)
        self.ket_qua("0902222222", nguoi=self.nv2)
        self.assertEqual(sv.loc_lich_su(self.tn).count(), 1)
        self.assertEqual(sv.loc_lich_su(self.nv2).count(), 1)

    def test_bao_cao(self):
        LichSuGui.objects.create(sdt="0901111111", ket_ban="Đã gửi", tin_nhan="Đã gửi", thoi_gian=timezone.now(), nguoi_gui=self.nv)
        LichSuGui.objects.create(sdt="0902222222", ket_ban="Lỗi", tin_nhan="Lỗi", thoi_gian=timezone.now(), nguoi_gui=self.nv2)
        KhachHang.objects.create(sdt="0901111111", nhan_vien=self.nv, gui_dau_luc=timezone.now(), dong_y_luc=timezone.now())
        r = self.client_cua(self.admin).get("/api/bao-cao").json()
        self.assertEqual((r["tong"]["gui"], r["tong"]["thanhCong"], r["tong"]["dongY"]), (2, 1, 1))
        self.assertEqual(len(r["theoNgay"]), 30)
        self.assertEqual(r["nhanVien"][0]["ten"], "Lan")
        r = self.client_cua(self.nv).get("/api/bao-cao").json()                 # nhân viên chỉ thấy của mình
        self.assertEqual(r["tong"]["gui"], 1)

    def test_tat_quyen_xuat_excel(self):
        self.cfg.nhan_vien_xuat_excel = False
        self.cfg.save()
        self.assertEqual(self.client_cua(self.nv).get("/api/lich-su/excel").status_code, 403)
        self.assertEqual(self.client_cua(self.admin).get("/api/lich-su/excel").status_code, 200)

    def test_xoa_du_lieu_khach_theo_yeu_cau(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", ten="Nam", phu_trach=self.nv)
        self.ket_qua("0901111111", nguoi=self.nv)
        LichSuGui.objects.update(ten="Nam", noi_dung="Chào Nam")
        KhachHang.objects.create(sdt="0901111111")
        sv.xoa_du_lieu_khach({"0901111111"}, self.admin)
        self.assertFalse(NguoiNhan.objects.exists() or KetQua.objects.exists() or KhachHang.objects.exists())
        h = LichSuGui.objects.get()
        self.assertEqual((h.ten, h.noi_dung), ("", ""))
        self.assertTrue(KhongLienHe.objects.filter(sdt="0901111111").exists())

    def test_mau_tin_chi_hien_mau_da_duyet(self):
        MauTinNhan.objects.create(ten="Duyệt", noi_dung="A", da_duyet=True)
        MauTinNhan.objects.create(ten="Chưa", noi_dung="B")
        with mock.patch("guitin.views.worker.trang_thai", return_value=DANG_NHAP):
            r = self.client_cua(self.nv).get("/api/state").json()
        self.assertEqual([m["ten"] for m in r["mau"]], ["Duyệt"])

    def test_tinh_trang_he_thong(self):
        from . import tinh_trang
        with mock.patch("guitin.tinh_trang.worker.tat_ca", return_value=None):
            muc = {m["ten"]: m for m in tinh_trang.kiem_tra()}
        self.assertFalse(muc["Zalo worker"]["ok"])
        self.assertTrue(muc["Database"]["ok"])


class BaoCaoEmail(CoSo):
    def setUp(self):
        super().setUp()
        self.cfg.bao_cao_tan_suat = "hang_ngay"
        self.cfg.bao_cao_email_nhan = "quanly@x.vn, giamdoc@x.vn"
        self.cfg.gmail_gui = "nguyenkluanbds@gmail.com"
        self.cfg.gmail_mat_khau = "abcdabcdabcdabcd"
        self.cfg.save()
        LichSuGui.objects.create(sdt="0901111111", ten="Nam", ket_ban="Đã gửi", tin_nhan="Đã gửi",
                                 thoi_gian=timezone.now(), nguoi_gui=self.nv)

    def test_gui_bao_cao_kem_excel(self):
        import io

        from django.core import mail
        from openpyxl import load_workbook

        from .bao_cao_email import gui_bao_cao
        self.assertEqual(gui_bao_cao(), ["quanly@x.vn", "giamdoc@x.vn"])
        thu = mail.outbox[0]
        self.assertIn("Báo cáo", thu.subject)
        self.assertIn("Đã gửi: 1 lượt, thành công 1", thu.body)
        ten, data, _ = thu.attachments[0]
        wb = load_workbook(io.BytesIO(data))
        self.assertEqual(wb.sheetnames, ["Tổng quan", "Theo nhân viên", "Theo dự án", "Chiến dịch", "Lịch sử gửi"])
        self.assertEqual(wb["Lịch sử gửi"]["B2"].value, "0901111111")

    def test_thieu_cau_hinh(self):
        from .bao_cao_email import gui_bao_cao
        self.cfg.gmail_mat_khau = ""
        with self.assertRaises(ValueError):
            gui_bao_cao(self.cfg)

    def test_gui_dinh_ky_moi_ngay_mot_lan(self):
        from django.core import mail

        from .bao_cao_email import chay_dinh_ky
        tz = timezone.get_current_timezone()
        som = timezone.make_aware(datetime.combine(timezone.localdate(), time(17, 0)), tz)
        muon = timezone.make_aware(datetime.combine(timezone.localdate(), time(18, 5)), tz)
        self.assertIsNone(chay_dinh_ky(som))                       # chưa đến 18:00
        self.assertIn("Đã gửi báo cáo", chay_dinh_ky(muon))
        self.assertIsNone(chay_dinh_ky(muon + timedelta(minutes=1)))   # đã gửi hôm nay
        self.assertEqual(len(mail.outbox), 1)
        self.cfg.refresh_from_db()
        self.cfg.bao_cao_tan_suat = "hang_tuan"
        self.cfg.bao_cao_thu = (muon + timedelta(days=1)).weekday()
        self.cfg.save()
        self.assertIsNone(chay_dinh_ky(muon + timedelta(hours=1)))     # không phải ngày gửi báo cáo tuần

    def test_admin_gui_thu_va_giu_mat_khau(self):
        from django.core import mail
        c = self.client_cua(self.admin)
        r = c.post("/admin/guitin/caidat/gui-thu-bao-cao/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(c.get("/admin/guitin/caidat/1/change/").status_code, 200)
        from .admin import CaiDatForm
        f = CaiDatForm(instance=self.cfg)
        f.cleaned_data = {"gmail_mat_khau": ""}
        self.assertEqual(f.clean_gmail_mat_khau(), "abcdabcdabcdabcd")
