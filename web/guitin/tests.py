"""Test tự động. Chạy trước mỗi lần cập nhật:  cd web && python manage.py test guitin
(và  npm test  ở thư mục gốc cho phần Node / trình duyệt)."""
import io
import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from . import services as sv
from .models import (CaiDat, KetQua, KhongLienHe, LichSuGui, NguoiDung, NguoiNhan, chuan_hoa_sdt, tach_sdt,
                     thanh_cong)

VD = json.loads((Path(settings.ROOT_DIR) / "tests" / "vi-du-chung.json").read_text(encoding="utf-8"))
DANG_NHAP = {"login": "in", "running": False, "account": "Zalo thử"}


class ViDuChung(TestCase):
    """Cùng bộ ví dụ với tests/chung.test.js: Python, Node và trình duyệt phải cho cùng kết quả."""

    def test_chuan_hoa_sdt(self):
        for vao, ra in VD["chuan_hoa_sdt"]:
            self.assertEqual(chuan_hoa_sdt(vao), ra, vao)

    def test_cac_mau(self):
        for vao, ra in VD["cac_mau"]:
            self.assertEqual(sv.cac_mau(vao), ra)

    def test_dien(self):
        for (tpl, ten, du_an, nv), ra in VD["dien"]:
            self.assertEqual(sv.dien(tpl, ten, du_an, nv), ra)

    def test_ten_goi_nho(self):
        for (tpl, ten, sdt, du_an), ra in VD["ten_goi_nho"]:
            self.assertEqual(sv.ten_goi_nho(tpl, ten, sdt, du_an), ra)

    def test_thanh_cong(self):
        for (kb, tn), ra in VD["thanh_cong"]:
            self.assertEqual(thanh_cong(kb, tn), ra)

    def test_doc_dong_dan(self):
        for vao, ra in VD["doc_dong_dan"]:
            out = sv.doc_van_ban(vao)
            self.assertEqual(out[0] if out else None, ra, vao)


class DocDuLieu(TestCase):
    def test_tach_sdt(self):
        self.assertEqual(tach_sdt("0901234567 - 0912345678"), ["0901234567", "0912345678"])
        self.assertEqual(tach_sdt("09012345670912345678"), ["0901234567", "0912345678"])
        self.assertEqual(tach_sdt("02838123456"), [])          # số bàn 11 chữ số: không cắt nhầm

    def test_xao(self):
        for _ in range(20):
            self.assertIn(sv.xao("{Chào|Xin chào} anh\n---\nHello"), ("Chào anh", "Xin chào anh", "Hello"))

    def test_doc_csv(self):
        rows = sv.doc_file("Danh sách\r\nSĐT;Họ tên;Dự án\r\n901234567;Anh Nam;Palm City\r\n".encode("utf-8"))
        self.assertEqual(rows, [{"sdt": "0901234567", "ten": "Anh Nam", "noiDung": "", "duAn": "Palm City"}])

    def test_doc_xlsx(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(["Số điện thoại", "Tên khách hàng", "Nội dung"])
        wb.active.append([901234567, "Chị Lan", "Chào {ten}"])
        buf = io.BytesIO()
        wb.save(buf)
        self.assertEqual(sv.doc_file(buf.getvalue())[0]["sdt"], "0901234567")

    def test_file_loi(self):
        with self.assertRaises(ValueError):
            sv.doc_file(b"Ten,Tuoi\nA,1\n")

    def test_excel_chong_cong_thuc(self):
        self.assertEqual(sv.o_excel("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(sv.o_excel("+84"), "'+84")
        self.assertEqual(sv.o_excel("Anh Nam"), "Anh Nam")
        self.assertEqual(sv.o_excel(5), 5)


class CoSo(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = NguoiDung.objects.create_superuser("admin@x.vn", "matkhau1", ho_ten="Quản trị")
        self.nv = NguoiDung.objects.create_user("nv@x.vn", "matkhau1", ho_ten="Lan")
        self.nv2 = NguoiDung.objects.create_user("nv2@x.vn", "matkhau1", ho_ten="Hùng")
        self.xem = NguoiDung.objects.create_user("xem@x.vn", "matkhau1", ho_ten="Xem", vai_tro=NguoiDung.CHI_XEM)
        self.cfg = CaiDat.lay()

    def client_cua(self, u):
        c = Client()
        c.force_login(u)
        return c

    def post(self, c, url, data):
        return c.post(url, json.dumps(data), content_type="application/json")

    def ket_qua(self, sdt, ket_ban="Đã gửi", tin_nhan="Đã gửi", nguoi=None, luc=None):
        d = {"ket_ban": ket_ban, "tin_nhan": tin_nhan, "thoi_gian": luc or timezone.now(), "nguoi_gui": nguoi}
        KetQua.objects.update_or_create(sdt=sdt, defaults=d)
        LichSuGui.objects.create(sdt=sdt, **d)


class ChoGuiVaHanMuc(CoSo):
    def test_cho_gui_loai_da_gui_va_tu_choi(self):
        for s in ("0901111111", "0902222222", "0903333333"):
            NguoiNhan.objects.create(sdt_goc=s, phu_trach=self.nv)
        self.ket_qua("0901111111")
        KhongLienHe.objects.create(sdt="0902222222")
        self.assertEqual([r.sdt for r in sv.cho_gui(self.nv, "all")], ["0903333333"])

    def test_han_muc_ngay(self):
        self.cfg.gioi_han_moi_ngay = 3
        self.cfg.save()
        for i in range(2):
            self.ket_qua(f"090000000{i}", nguoi=self.nv)
        self.ket_qua("0900000009", nguoi=self.nv, luc=timezone.now() - timedelta(days=1))   # hôm qua: không tính
        self.assertEqual(sv.con_lai_hom_nay(self.nv, self.cfg), 1)
        self.assertEqual(sv.so_gui_toi_da(self.nv, self.cfg), 1)
        self.nv.gioi_han_ngay = 2      # hạn mức riêng của nhân viên
        self.assertEqual(sv.con_lai_hom_nay(self.nv, self.cfg), 0)
        self.assertIn("đủ 2 người", sv.chua_gui_duoc(self.nv, self.cfg))

    def test_khong_gioi_han(self):
        self.cfg.gioi_han_moi_ngay = 0
        self.cfg.gioi_han_moi_lan = 0
        self.assertIsNone(sv.so_gui_toi_da(self.nv, self.cfg))

    @mock.patch("guitin.views.worker.goi")
    @mock.patch("guitin.views.worker.trang_thai", return_value=DANG_NHAP)
    def test_api_start_gui_dung_so_luong(self, _tt, goi):
        self.cfg.gioi_han_moi_ngay = 2
        self.cfg.save()
        for s in ("0901111111", "0902222222", "0903333333"):
            NguoiNhan.objects.create(sdt_goc=s, phu_trach=self.nv, du_an="Palm City")
        self.nv.loi_moi_ket_ban = "Em {nhanvien} dự án {duan} chào {ten}"
        self.nv.save()
        r = self.post(self.client_cua(self.nv), "/api/start", {})
        self.assertEqual(r.status_code, 200, r.content)
        body = goi.call_args[0][2]
        self.assertEqual(len(body["batch"]), 2)
        self.assertEqual(body["cfg"]["conLaiHomNay"], 2)
        self.assertEqual(body["batch"][0]["loiMoi"], "Em Lan dự án Palm City chào {ten}")
        self.assertEqual(body["cfg"]["gioBatDau"], 8 * 60)

    @mock.patch("guitin.views.worker.goi")
    @mock.patch("guitin.views.worker.trang_thai", return_value=DANG_NHAP)
    def test_api_start_bi_tam_dung(self, _tt, goi):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        self.nv.tam_dung_den = timezone.now() + timedelta(hours=3)
        self.nv.save()
        r = self.post(self.client_cua(self.nv), "/api/start", {})
        self.assertEqual(r.status_code, 400)
        self.assertIn("tạm dừng", r.json()["error"])
        goi.assert_not_called()


class LuuDanhSach(CoSo):
    def test_nhan_vien_khong_xoa_duoc_so_quan_tri_giao(self):
        sv.nhap_them([{"sdt": "0901111111", "ten": "A"}], self.nv)
        tu_them = NguoiNhan.objects.create(sdt_goc="0902222222", phu_trach=self.nv)
        giao = NguoiNhan.objects.get(sdt="0901111111")
        tao_luc = giao.tao_luc
        self.assertTrue(giao.quan_tri_giao)
        # Nhân viên bấm "Xoá hết" (gửi danh sách rỗng) rồi thử đổi SĐT số được giao
        kq = sv.luu_danh_sach(self.nv, [])
        self.assertEqual(kq["giuLai"], 1)
        self.assertFalse(NguoiNhan.objects.filter(id=tu_them.id).exists())
        sv.luu_danh_sach(self.nv, [{"id": giao.id, "sdt": "0909999999", "ten": "A sửa"}])
        giao.refresh_from_db()
        self.assertEqual((giao.sdt, giao.ten, giao.tao_luc), ("0901111111", "A sửa", tao_luc))

    def test_khong_them_so_cua_nguoi_khac_hoac_tu_choi(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv2)
        KhongLienHe.objects.create(sdt="0902222222")
        kq = sv.luu_danh_sach(self.nv, [{"sdt": "0901111111"}, {"sdt": "0902222222"}, {"sdt": "0903333333"}])
        self.assertEqual(kq["trungNguoiKhac"], ["0901111111"])
        self.assertEqual(kq["tuChoi"], ["0902222222"])
        self.assertEqual(list(self.nv.nguoi_nhan.values_list("sdt", flat=True)), ["0903333333"])

    def test_quan_tri_khong_dung_api_luu_danh_sach(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        r = self.post(self.client_cua(self.admin), "/api/contacts", {"contacts": []})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(NguoiNhan.objects.count(), 1)

    def test_nhap_them_bao_cao(self):
        KhongLienHe.objects.create(sdt="0902222222")
        self.ket_qua("0903333333")
        kq = sv.nhap_them([{"sdt": "0901111111"}, {"sdt": "0901111111"}, {"sdt": "0902222222"}, {"sdt": "0903333333"}])
        self.assertEqual(kq, {"them": 2, "trung": 1, "tuChoi": 1, "daGui": 1})


class TuChoiLienHe(CoSo):
    def test_nhan_vien_chi_danh_dau_so_cua_minh(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        NguoiNhan.objects.create(sdt_goc="0902222222", phu_trach=self.nv2)
        c = self.client_cua(self.nv)
        self.assertEqual(self.post(c, "/api/tu-choi", {"sdt": "0901111111"}).status_code, 200)
        self.assertEqual(self.post(c, "/api/tu-choi", {"sdt": "0902222222"}).status_code, 400)
        self.assertEqual(self.post(c, "/api/tu-choi", {"sdt": "0901111111", "bo": True}).status_code, 400)
        self.assertTrue(KhongLienHe.objects.filter(sdt="0901111111").exists())
        self.assertEqual(self.post(self.client_cua(self.xem), "/api/tu-choi", {"sdt": "0901111111"}).status_code, 403)


class WorkerBaoVe(CoSo):
    def bao(self, data):
        return Client().post("/api/worker/event", json.dumps(data), content_type="application/json",
                             headers={"X-Worker-Token": settings.WORKER_TOKEN})

    def test_sai_ma_worker(self):
        r = Client().post("/api/worker/event", "{}", content_type="application/json", headers={"X-Worker-Token": "x"})
        self.assertEqual(r.status_code, 403)

    def test_ket_qua_gui_lai_khong_ghi_trung(self):
        ev = {"type": "result", "sdt": "0901111111", "ma": "abc-123", "nguoiGuiId": self.nv.id,
              "ketQua": {"ketBan": "Đã gửi", "tinNhan": "Đã gửi", "thoiGian": "2026-11-01T03:00:00.000Z"}}
        self.assertEqual(self.bao(ev).status_code, 200)
        self.assertEqual(self.bao(ev).status_code, 200)
        self.assertEqual(LichSuGui.objects.filter(sdt="0901111111").count(), 1)
        self.assertTrue(KetQua.objects.filter(sdt="0901111111").exists())

    def test_tu_dung_thi_tam_dung_zalo(self):
        self.bao({"type": "auto_stop", "nguoiDungId": self.nv.id, "soLoi": 5})
        self.nv.refresh_from_db()
        self.assertGreater(self.nv.tam_dung_den, timezone.now() + timedelta(hours=23))


class ThongKe(CoSo):
    def test_thong_ke_khop_dinh_nghia_thanh_cong(self):
        for s in ("0901111111", "0902222222", "0903333333", "0904444444", "0905555555"):
            NguoiNhan.objects.create(sdt_goc=s, phu_trach=self.nv)
        NguoiNhan.objects.create(sdt_goc="12345", phu_trach=self.nv)            # số sai
        self.ket_qua("0901111111")
        self.ket_qua("0902222222", ket_ban="Bỏ qua", tin_nhan="-")             # trước đây bị tính là đã gửi
        self.ket_qua("0903333333", ket_ban="Lỗi", tin_nhan="Lỗi")
        KhongLienHe.objects.create(sdt="0904444444")
        t = sv.thong_ke()["tong"]
        self.assertEqual(t, {"tong": 6, "cho": 1, "da_gui": 1, "loi": 4})
        ls = sv.loc_lich_su(self.admin, tt="ok")
        self.assertEqual(ls.count(), 1)


class PhanQuyen(CoSo):
    def test_nhan_vien_khong_xoa_toan_bo_lich_su(self):
        r = self.post(self.client_cua(self.nv), "/api/reset", {})
        self.assertEqual(r.status_code, 400)

    def test_chi_xem_khong_gui(self):
        self.assertEqual(self.post(self.client_cua(self.xem), "/api/start", {}).status_code, 403)

    def test_chua_dang_nhap(self):
        self.assertEqual(Client().get("/api/state").status_code, 401)

    @mock.patch("guitin.views.worker.goi")
    def test_dung_ho_tai_khoan_khong_ton_tai(self, _goi):
        r = self.post(self.client_cua(self.admin), "/api/stop", {"uid": 99999})
        self.assertEqual(r.status_code, 400)

    def test_nhan_vien_thay_moi_so_cua_minh(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        NguoiNhan.objects.create(sdt_goc="0902222222", phu_trach=self.nv2)
        with mock.patch("guitin.views.worker.trang_thai", return_value=DANG_NHAP):
            r = self.client_cua(self.nv).get("/api/state")
        self.assertEqual([c["sdtChuan"] for c in r.json()["contacts"]], ["0901111111"])

    def test_xuat_excel_ghi_nhat_ky(self):
        from .models import NhatKy
        self.client_cua(self.nv).get("/api/lich-su/excel")
        self.assertTrue(NhatKy.objects.filter(noi_dung__contains="xuất Excel lịch sử").exists())


class TrangQuanTri(CoSo):
    def test_cac_trang_mo_duoc(self):
        NguoiNhan.objects.create(sdt_goc="0901111111", phu_trach=self.nv)
        KhongLienHe.objects.create(sdt="0902222222")
        c = self.client_cua(self.admin)
        with mock.patch("guitin.worker.tat_ca", return_value={}), \
                mock.patch("guitin.worker.trang_thai", return_value=DANG_NHAP):
            for url in ("/admin/", "/admin/guitin/caidat/1/change/", "/admin/guitin/khonglienhe/",
                        "/admin/guitin/khonglienhe/add/", "/admin/guitin/nguoinhan/?trang_thai=tu_choi",
                        "/admin/guitin/nguoinhan/?trang_thai=loi", "/admin/guitin/nguoinhan/nhap/",
                        f"/admin/guitin/nguoidung/{self.nv.id}/change/"):
                self.assertEqual(c.get(url).status_code, 200, url)

    def test_nhap_danh_sach_qua_admin(self):
        c = self.client_cua(self.admin)
        r = c.post("/admin/guitin/nguoinhan/nhap/", {"dan": "0901111111\tAnh Nam\t\tPalm City", "phu_trach": self.nv.id,
                                                     "du_an": ""}, follow=True)
        self.assertEqual(r.status_code, 200)
        n = NguoiNhan.objects.get(sdt="0901111111")
        self.assertEqual((n.du_an, n.quan_tri_giao), ("Palm City", True))


class KhoaDangNhap(CoSo):
    def test_khoa_theo_ip_va_email(self):
        c = Client()
        for _ in range(5):
            c.post("/dang-nhap/", {"username": "nv@x.vn", "password": "sai"})
        r = c.post("/dang-nhap/", {"username": "nv@x.vn", "password": "matkhau1"})
        self.assertContains(r, "Sai quá nhiều lần")
        # Người khác cùng máy vẫn đăng nhập được
        r = c.post("/dang-nhap/", {"username": "nv2@x.vn", "password": "matkhau1"})
        self.assertEqual(r.status_code, 302)
