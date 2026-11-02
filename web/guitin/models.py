import re
from datetime import time

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# Xáo nội dung: nhiều mẫu cách nhau bằng dòng "---", hoặc {a|b|c} để đổi vài từ (xem services.xao)
HD_XAO = ("Muốn mỗi người nhận một nội dung khác nhau: viết nhiều mẫu, mỗi mẫu cách nhau bằng một dòng chỉ có ---; "
          "hoặc viết {Chào|Xin chào|Hello} để chọn ngẫu nhiên một từ.")
THU_MUC_ANH_TIN_NHAN = "anh-tin-nhan"


def kho_anh_tin_nhan():
    """Ảnh gửi kèm tin nhắn cho khách lưu ở data/anh-tin-nhan/ (worker đọc cùng thư mục)."""
    from django.conf import settings
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage(location=settings.ROOT_DIR / "data" / THU_MUC_ANH_TIN_NHAN)


def ten_file_anh(instance, filename):
    import uuid
    from pathlib import Path
    return f"{uuid.uuid4().hex}{Path(filename).suffix.lower()[:6]}"


# Đầu số di động 11 số cũ -> 10 số mới (chuyển đổi năm 2018)
DAU_SO_CU = {
    "0120": "070", "0121": "079", "0122": "077", "0126": "076", "0128": "078",
    "0123": "083", "0124": "084", "0125": "085", "0127": "081", "0129": "082",
    "0162": "032", "0163": "033", "0164": "034", "0165": "035", "0166": "036", "0167": "037", "0168": "038", "0169": "039",
    "0186": "056", "0188": "058", "0199": "059",
}


def chuan_hoa_sdt(raw):
    """Chuẩn hoá SĐT: Excel hay làm mất số 0 đầu, hoặc ghi +84 / 84; số 11 chữ số đầu cũ (0162...) đổi sang đầu mới.
    Trả về None nếu không hợp lệ."""
    p = re.sub(r"[^\d+]", "", str(raw or ""))
    if p.startswith("+84"):
        p = "0" + p[3:]
    elif p.startswith("84") and len(p) in (11, 12):
        p = "0" + p[2:]
    elif len(p) in (9, 10) and not p.startswith("0"):
        p = "0" + p
    if len(p) == 11 and p[:4] in DAU_SO_CU:
        p = DAU_SO_CU[p[:4]] + p[4:]
    return p if re.fullmatch(r"0\d{9}", p) else None


def tach_sdt(raw):
    """Lấy các SĐT hợp lệ trong 1 ô (ô có thể ghi nhiều số: "090.. - 091..", "090../091..", xuống dòng,
    hoặc 2 số dính liền nhau). Trả về danh sách SĐT đã chuẩn hoá, giữ thứ tự, không trùng."""
    s = str(raw or "")
    out = []
    for part in re.split(r"[/;,|\n\r]+|\s[-–]\s|\s{2,}|hoặc|hoac|or", s, flags=re.IGNORECASE):
        p = chuan_hoa_sdt(part)
        found = [p] if p else _tach_so_dinh_lien(re.sub(r"\D", "", part))
        for q in found:
            if q not in out:
                out.append(q)
    return out


def _tach_so_dinh_lien(d):
    """Chuỗi chữ số gồm nhiều SĐT viết liền nhau (vd 20 chữ số = 2 số). Chỉ nhận khi tách được
    trọn vẹn thành các số hợp lệ, không thừa chữ số nào (để không cắt nhầm số bàn / số lạ)."""
    out, i = [], 0
    while i < len(d):
        n = 11 if d[i:i + 4] in DAU_SO_CU else 10
        q = chuan_hoa_sdt(d[i:i + n])
        if not q:
            return []
        out.append(q)
        i += n
    return out if len(out) > 1 else []


# ---------- trạng thái gửi: một định nghĩa duy nhất, dùng ở models, services, admin và JS (chung.js) ----------
def thanh_cong(ket_ban, tin_nhan):
    """Gửi thành công = tin nhắn đã gửi, hoặc tắt gửi tin (“-”) mà lời mời kết bạn đã gửi."""
    return tin_nhan == "Đã gửi" or (tin_nhan == "-" and ket_ban == "Đã gửi")


def q_thanh_cong(tien_to=""):
    """Bộ lọc Q giống hệt thanh_cong(). tien_to: vd "kq_" khi lọc trên cột annotate."""
    from django.db.models import Q
    return Q(**{f"{tien_to}tin_nhan": "Đã gửi"}) | Q(**{f"{tien_to}tin_nhan": "-", f"{tien_to}ket_ban": "Đã gửi"})


def nhom_chi_xem():
    """Nhóm quyền cho vai trò Chỉ xem: chỉ có quyền xem các bảng của tool trong /admin/."""
    from django.contrib.auth.models import Group, Permission
    nhom, _ = Group.objects.get_or_create(name="Chỉ xem")
    quyen = Permission.objects.filter(content_type__app_label="guitin", codename__startswith="view_")
    if nhom.permissions.count() != quyen.count():
        nhom.permissions.set(quyen)
    return nhom


# ---------- tài khoản ----------
class NguoiDungManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("Cần nhập email")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("vai_tro", NguoiDung.NHAN_VIEN)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["vai_tro"] = NguoiDung.QUAN_TRI
        return self._create(email, password, **extra)


class NguoiDung(AbstractUser):
    QUAN_TRI, TRUONG_NHOM, NHAN_VIEN, CHI_XEM = "admin", "truongnhom", "nhanvien", "xem"
    VAI_TRO = [(QUAN_TRI, "Quản trị"), (TRUONG_NHOM, "Trưởng nhóm"), (NHAN_VIEN, "Nhân viên"), (CHI_XEM, "Chỉ xem")]
    # "all" = thấy toàn bộ danh sách; nhân viên thiếu quyền này nên chỉ thấy số được giao cho mình.
    # "team" = trưởng nhóm: xem và giao số trong đội của mình (trang Đội của tôi)
    QUYEN = {
        QUAN_TRI: {"view", "all", "list", "send", "settings", "zalo", "users", "export"},
        TRUONG_NHOM: {"view", "list", "send", "team", "export"},
        NHAN_VIEN: {"view", "list", "send", "export"},
        CHI_XEM: {"view", "all", "export"},
    }

    username = None
    first_name = None
    last_name = None
    email = models.EmailField("Email đăng nhập", unique=True)
    ho_ten = models.CharField("Họ tên", max_length=255)
    vai_tro = models.CharField("Vai trò", max_length=16, choices=VAI_TRO, default=NHAN_VIEN,
                               help_text="Quản trị: toàn quyền trong trang quản trị. Nhân viên: dùng trang gửi tin, chỉ thấy và gửi các số được giao. Chỉ xem: xem trang quản trị, không sửa được gì.")

    tin_nhan_mac_dinh = models.TextField(
        "Tin nhắn mặc định của nhân viên", blank=True,
        help_text="Nhân viên tự soạn trên trang gửi tin. Dùng cho các số không có nội dung riêng; "
                  "để trống thì dùng nội dung mặc định trong Cài đặt gửi tin. " + HD_XAO)
    loi_moi_ket_ban = models.TextField(
        "Lời mời kết bạn của nhân viên", blank=True,
        help_text="Nhân viên tự soạn trên trang gửi tin. Để trống thì dùng lời mời kết bạn trong Cài đặt gửi tin. " + HD_XAO)
    anh_tin_nhan = models.JSONField(
        "Ảnh gửi kèm tin nhắn của nhân viên", default=list, blank=True,
        help_text="Nhân viên tự chọn trên trang gửi tin (data/anh-tin-nhan/nv-<id>/). Để trống thì dùng ảnh trong Cài đặt gửi tin.")
    gioi_han_ngay = models.PositiveSmallIntegerField(
        "Tối đa người/ngày của Zalo này", default=0, validators=[MaxValueValidator(500)],
        help_text="0 = theo “Tối đa người mỗi ngày” trong Cài đặt gửi tin. Zalo mới tạo nên đặt thấp (vd 10/ngày tuần đầu) rồi tăng dần.")
    tam_dung_den = models.DateTimeField(
        "Tạm dừng gửi đến", null=True, blank=True,
        help_text="Tool tự đặt khi gặp nhiều lỗi liên tiếp (có thể Zalo đang hạn chế tài khoản). Xoá ô này để cho gửi lại ngay.")
    so_dien_thoai = models.CharField("SĐT của nhân viên", max_length=20, blank=True,
                                     help_text="Dùng cho {sdt_nhanvien} trong tin nhắn / lời mời.")
    doi = models.CharField("Đội", max_length=100, blank=True, db_index=True,
                           help_text="Nhân viên cùng tên đội với một Trưởng nhóm thì trưởng nhóm đó xem và giao số được cho họ.")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["ho_ten"]
    objects = NguoiDungManager()

    class Meta:
        verbose_name = "tài khoản"
        verbose_name_plural = "Tài khoản"
        ordering = ["vai_tro", "ho_ten"]

    def save(self, *args, **kwargs):
        # Quản trị: toàn quyền trang /admin/. Chỉ xem: vào /admin/ nhưng chỉ xem được.
        # Nhân viên: không vào /admin/, dùng trang gửi tin riêng.
        self.email = (self.email or "").lower()
        self.is_staff = self.vai_tro in (self.QUAN_TRI, self.CHI_XEM)
        self.is_superuser = self.vai_tro == self.QUAN_TRI
        super().save(*args, **kwargs)
        nhom = nhom_chi_xem()
        if self.vai_tro == self.CHI_XEM:
            self.groups.add(nhom)
        else:
            self.groups.remove(nhom)

    @property
    def quyen(self):
        return self.QUYEN.get(self.vai_tro, set())

    def co_quyen(self, q):
        if not (self.is_active and q in self.quyen):
            return False
        # Quản trị tắt "Nhân viên được xuất Excel" thì chỉ quản trị / chỉ xem xuất được
        if q == "export" and self.vai_tro in (self.NHAN_VIEN, self.TRUONG_NHOM):
            return CaiDat.lay().nhan_vien_xuat_excel
        return True

    def thanh_vien_doi(self):
        """Trưởng nhóm: các tài khoản cùng đội (kể cả chính mình); người khác: chỉ mình."""
        if self.vai_tro != self.TRUONG_NHOM or not self.doi:
            return NguoiDung.objects.filter(pk=self.pk)
        return NguoiDung.objects.filter(is_active=True, doi=self.doi,
                                        vai_tro__in=[self.NHAN_VIEN, self.TRUONG_NHOM])

    def __str__(self):
        return self.ho_ten or self.email


# ---------- chiến dịch ----------
class ChienDich(models.Model):
    """Một đợt chăm sóc riêng: tên, dự án, nội dung, thời gian. Một khách có thể nằm trong nhiều chiến dịch;
    số không thuộc chiến dịch nào là “Chung” (như trước đây)."""
    ten = models.CharField("Tên chiến dịch", max_length=150, unique=True)
    du_an = models.CharField("Dự án", max_length=100, blank=True, help_text="Dùng cho {duan} khi số chưa có dự án riêng.")
    loi_moi = models.TextField("Lời mời kết bạn", blank=True, help_text="Để trống: lời mời của nhân viên / quản trị. " + HD_XAO)
    noi_dung = models.TextField("Tin nhắn", blank=True, help_text="Để trống: tin nhắn của nhân viên / quản trị. " + HD_XAO)
    tin_khi_dong_y = models.TextField(
        "Tin gửi khi khách đồng ý kết bạn", blank=True,
        help_text="Để trống: dùng cài đặt chung (Cài đặt gửi tin → Chăm sóc tự động).")
    nhac_sau_ngay = models.PositiveSmallIntegerField(
        "Nhắc lại sau (ngày)", default=0, validators=[MaxValueValidator(90)],
        help_text="Khách chưa trả lời sau bấy nhiêu ngày thì tự gửi tin nhắc lại một lần. 0 = theo cài đặt chung.")
    noi_dung_nhac = models.TextField("Tin nhắc lại", blank=True, help_text="Để trống: theo cài đặt chung.")
    bat_dau = models.DateField("Bắt đầu", null=True, blank=True)
    ket_thuc = models.DateField("Kết thúc", null=True, blank=True, help_text="Sau ngày này không gửi số của chiến dịch nữa.")
    dang_chay = models.BooleanField("Đang chạy", default=True)
    tao_luc = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        verbose_name = "chiến dịch"
        verbose_name_plural = "Chiến dịch"
        ordering = ["-tao_luc"]

    def con_hieu_luc(self, ngay=None):
        from django.utils import timezone
        ngay = ngay or timezone.localdate()
        return self.dang_chay and (not self.bat_dau or self.bat_dau <= ngay) and (not self.ket_thuc or ngay <= self.ket_thuc)

    def __str__(self):
        return self.ten


# ---------- người nhận ----------
class NguoiNhan(models.Model):
    thu_tu = models.PositiveIntegerField("Thứ tự", default=0, db_index=True)
    sdt_goc = models.CharField("Số điện thoại", max_length=32, blank=True)
    sdt = models.CharField("SĐT chuẩn hoá", max_length=10, null=True, blank=True, db_index=True, editable=False)
    ten = models.CharField("Tên", max_length=255, blank=True)
    noi_dung = models.TextField("Nội dung tin nhắn riêng", blank=True,
                                help_text="Để trống thì dùng nội dung mặc định trong Cài đặt gửi tin. Có thể dùng {ten}, {duan}, {nhanvien}. " + HD_XAO)
    du_an = models.CharField("Dự án", max_length=100, blank=True, db_index=True,
                             help_text="Ghi vào tên gợi nhớ ({duan}). Để trống thì dùng tên dự án mặc định trong Cài đặt gửi tin.")
    phu_trach = models.ForeignKey(
        NguoiDung, verbose_name="Nhân viên phụ trách", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="nguoi_nhan",
        limit_choices_to={"vai_tro__in": [NguoiDung.QUAN_TRI, NguoiDung.TRUONG_NHOM, NguoiDung.NHAN_VIEN]},
    )
    quan_tri_giao = models.BooleanField(
        "Quản trị giao", default=False,
        help_text="Số do quản trị nhập / giao: nhân viên không xoá hay đổi SĐT được, chỉ sửa tên, nội dung, dự án.")
    chien_dich = models.ForeignKey(ChienDich, verbose_name="Chiến dịch", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="nguoi_nhan", help_text="Để trống = Chung (không thuộc chiến dịch nào).")
    xung_ho = models.CharField("Xưng hô", max_length=20, blank=True, help_text="Anh / Chị... dùng cho {xungho}.")
    them = models.JSONField("Cột thêm từ file", default=dict, blank=True,
                            help_text="Các cột khác trong file Excel, dùng được trong nội dung bằng {tên cột viết liền không dấu}.")
    nguon = models.CharField("Nguồn danh sách", max_length=255, blank=True, db_index=True,
                             help_text="Danh sách lấy từ đâu (vd: khách đăng ký sự kiện 10/2026). Nên ghi để biết nguồn gốc dữ liệu.")
    gui_lai_tu = models.DateTimeField("Gửi lại từ", null=True, blank=True, editable=False,
                                      help_text="Số trong chiến dịch được bấm Gửi lại: chỉ tính kết quả sau thời điểm này.")
    tao_luc = models.DateTimeField("Thêm lúc", auto_now_add=True)

    class Meta:
        verbose_name = "người nhận"
        verbose_name_plural = "Người nhận"
        ordering = ["thu_tu", "id"]

    def save(self, *args, **kwargs):
        self.sdt = chuan_hoa_sdt(self.sdt_goc)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sdt_goc} {self.ten}".strip()


# ---------- kết quả gửi ----------
class KetQua(models.Model):
    sdt = models.CharField("SĐT", max_length=10, primary_key=True)
    ten = models.CharField("Tên trong danh sách", max_length=255, blank=True)
    ten_zalo = models.CharField("Tên Zalo", max_length=255, blank=True)
    ket_ban = models.CharField("Kết bạn", max_length=20)
    tin_nhan = models.CharField("Tin nhắn", max_length=20)
    noi_dung = models.TextField("Nội dung đã gửi", blank=True)
    tai_khoan_zalo = models.CharField("Gửi từ Zalo", max_length=255, blank=True)
    ghi_chu = models.TextField("Ghi chú", blank=True)
    thoi_gian = models.DateTimeField("Thời gian gửi")
    nguoi_gui = models.ForeignKey(NguoiDung, verbose_name="Người bấm gửi", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="da_gui")
    zalo_uid = models.CharField("Zalo ID", max_length=40, blank=True, db_index=True, editable=False)

    class Meta:
        verbose_name = "kết quả gửi (mới nhất)"
        verbose_name_plural = "Kết quả gửi"
        ordering = ["-thoi_gian"]

    @property
    def thanh_cong(self):
        return thanh_cong(self.ket_ban, self.tin_nhan)

    def __str__(self):
        return f"{self.sdt}: kết bạn {self.ket_ban}, tin nhắn {self.tin_nhan}"


# ---------- lịch sử gửi: mỗi lần gửi một dòng, kể cả gửi lại ----------
class LichSuGui(models.Model):
    # Mã do worker đặt cho từng kết quả: worker gửi lại kết quả chưa được xác nhận nên cần chống ghi 2 lần
    ma = models.CharField("Mã kết quả", max_length=40, unique=True, null=True, blank=True, editable=False)
    sdt = models.CharField("SĐT", max_length=10, db_index=True)
    ten = models.CharField("Tên", max_length=255, blank=True)
    ten_zalo = models.CharField("Tên Zalo", max_length=255, blank=True)
    ket_ban = models.CharField("Kết bạn", max_length=20)
    tin_nhan = models.CharField("Tin nhắn", max_length=20)
    noi_dung = models.TextField("Nội dung đã gửi", blank=True)
    tai_khoan_zalo = models.CharField("Gửi từ Zalo", max_length=255, blank=True)
    ghi_chu = models.TextField("Ghi chú", blank=True)
    thoi_gian = models.DateTimeField("Thời gian gửi", db_index=True)
    nguoi_gui = models.ForeignKey(NguoiDung, verbose_name="Nhân viên gửi", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="lich_su_gui")
    LAN_DAU, KHI_DONG_Y, NHAC_LAI = "lan_dau", "khi_dong_y", "nhac_lai"
    LOAI = [(LAN_DAU, "Lần đầu"), (KHI_DONG_Y, "Khi khách đồng ý kết bạn"), (NHAC_LAI, "Nhắc lại")]
    loai = models.CharField("Loại", max_length=12, choices=LOAI, default=LAN_DAU, db_index=True)
    chien_dich = models.ForeignKey(ChienDich, verbose_name="Chiến dịch", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="lich_su")
    zalo_uid = models.CharField("Zalo ID", max_length=40, blank=True, db_index=True, editable=False)
    dot = models.ForeignKey("DotGui", verbose_name="Đợt gửi", null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="lich_su")

    class Meta:
        verbose_name = "lịch sử gửi"
        verbose_name_plural = "Lịch sử gửi"
        ordering = ["-thoi_gian", "-id"]

    @property
    def thanh_cong(self):
        return thanh_cong(self.ket_ban, self.tin_nhan)

    def __str__(self):
        return f"{self.sdt} lúc {self.thoi_gian:%d/%m/%Y %H:%M}"


# ---------- cài đặt (chỉ 1 dòng) ----------
class CaiDat(models.Model):
    loi_moi_ket_ban = models.TextField("Lời mời kết bạn", default="Chào {ten}, mình xin phép kết bạn để tiện trao đổi ạ.",
                                       help_text="{ten} = tên từng người, {duan} = dự án, {nhanvien} = nhân viên phụ trách. Nên dưới 150 ký tự (mỗi mẫu). "
                                                 + HD_XAO)
    noi_dung_tin_nhan = models.TextField("Tin nhắn", default="Chào {ten}!",
                                         help_text="{ten} = tên từng người, {duan} = dự án, {nhanvien} = nhân viên phụ trách. " + HD_XAO)
    gui_ket_ban = models.BooleanField("Gửi lời mời kết bạn", default=True)
    gui_tin_nhan = models.BooleanField("Gửi tin nhắn", default=True)
    gioi_han_moi_lan = models.PositiveIntegerField(
        "Số người tối đa mỗi lần", default=0,
        help_text="0 = không giới hạn: bấm gửi một lần là tool gửi hết danh sách chờ. "
                  "Nên bật “Nghỉ giữa các lượt” để tool tự nghỉ sau mỗi X người.")
    gioi_han_moi_ngay = models.PositiveSmallIntegerField(
        "Tối đa người mỗi ngày (mỗi Zalo)", default=0, validators=[MaxValueValidator(500)],
        help_text="0 = không giới hạn (mặc định). Đặt số thì mỗi tài khoản Zalo gửi tối đa bấy nhiêu người mỗi ngày, "
                  "đủ thì tool tự dừng, mai gửi tiếp (nên 20–30 để tránh bị Zalo khoá). Đặt riêng cho từng nhân viên trong Tài khoản.")
    dung_khi_loi_lien_tiep = models.PositiveSmallIntegerField(
        "Tự dừng sau số lỗi liên tiếp", default=5, validators=[MaxValueValidator(50)],
        help_text="Gặp bấy nhiêu lỗi liên tiếp (thường do Zalo đang hạn chế tìm số / kết bạn) thì tự dừng đợt gửi "
                  "và tạm dừng Zalo đó. 0 = không tự dừng.")
    tam_dung_gio = models.PositiveSmallIntegerField(
        "Tạm dừng sau khi tự dừng (giờ)", default=24, validators=[MaxValueValidator(168)],
        help_text="Sau khi tự dừng vì lỗi, Zalo đó không được gửi tiếp trong bấy nhiêu giờ (quản trị bỏ được trong Tài khoản). 0 = không tạm dừng.")
    gio_bat_dau = models.TimeField("Gửi từ", default=time(8, 0),
                                   help_text="Ngoài khung giờ này tool tự tạm nghỉ, đến giờ thì gửi tiếp.")
    gio_ket_thuc = models.TimeField("Gửi đến", default=time(20, 0))
    nghi_chu_nhat = models.BooleanField("Không gửi Chủ nhật", default=False)
    cho_toi_thieu_giay = models.PositiveIntegerField("Chờ tối thiểu (giây)", default=60, validators=[MinValueValidator(30)],
                                                      help_text="Không cho dưới 30 giây để tránh bị Zalo khoá tài khoản.")
    cho_toi_da_giay = models.PositiveIntegerField("Chờ tối đa (giây)", default=150, validators=[MinValueValidator(30)])
    nghi_sau_so_lan = models.PositiveSmallIntegerField(
        "Nghỉ sau mỗi (lần gửi)", default=0, validators=[MaxValueValidator(1000)],
        help_text="Cứ gửi được số lần này thì nghỉ một lúc lâu hơn. Để 0 là không nghỉ.")
    nghi_phut = models.PositiveSmallIntegerField("Thời gian nghỉ (phút)", default=10,
                                                 validators=[MinValueValidator(1), MaxValueValidator(240)])
    doi_ten_goi_nho = models.BooleanField("Đổi tên gợi nhớ khi kết bạn", default=True,
                                          help_text="Đặt tên gợi nhớ (biệt danh) cho người được mời kết bạn, để dễ tìm lại trong Zalo.")
    ten_du_an = models.CharField("Tên dự án mặc định", max_length=100, blank=True,
                                 help_text="Dùng cho {duan} khi số điện thoại chưa có dự án riêng (cột Dự án trong danh sách).")
    mau_ten_goi_nho = models.CharField("Mẫu tên gợi nhớ", max_length=100, default="{ten} {sdt} {duan}",
                                       help_text="{ten} = họ tên, {sdt} = số điện thoại, {duan} = tên dự án. Ví dụ: “{ten} {sdt} {duan}” → “Anh Nam 0901234567 Palm City”.")

    # Chăm sóc tự động (theo dõi khách đồng ý kết bạn / trả lời)
    theo_doi_phan_hoi = models.BooleanField(
        "Theo dõi khách đồng ý kết bạn và trả lời", default=True,
        help_text="Tool ghi lại khách nào đã đồng ý kết bạn, đã nhắn lại (không lưu nội dung khách nhắn).")
    gui_tin_khi_dong_y = models.BooleanField(
        "Tự gửi tin khi khách đồng ý kết bạn", default=True,
        help_text="Nhiều khách chặn tin từ người lạ nên tin đầu không tới. Khi khách đồng ý kết bạn, tool tự gửi tin "
                  "(chỉ khi tin đầu chưa tới, hoặc khi có soạn “Tin gửi khi đồng ý” bên dưới).")
    tin_khi_dong_y = models.TextField(
        "Tin gửi khi khách đồng ý kết bạn", blank=True,
        help_text="Để trống: gửi lại tin nhắn chính (kèm ảnh) nếu tin đầu chưa tới. Có nội dung: luôn gửi tin này khi khách "
                  "đồng ý. Dùng được {ten}, {duan}, {nhanvien}, {xungho}. " + HD_XAO)
    nhac_lai_sau_ngay = models.PositiveSmallIntegerField(
        "Nhắc lại sau (ngày)", default=0, validators=[MaxValueValidator(90)],
        help_text="Khách chưa trả lời sau bấy nhiêu ngày kể từ lần gửi đầu thì tool tự gửi tin nhắc lại một lần "
                  "(khi Zalo của nhân viên đang kết nối và rảnh, trong khung giờ gửi). 0 = không nhắc lại.")
    noi_dung_nhac_lai = models.TextField("Tin nhắc lại", blank=True,
                                         help_text="Bắt buộc khi bật nhắc lại. " + HD_XAO)
    khong_nhan_lai_ngay = models.PositiveSmallIntegerField(
        "Không nhắn cùng một khách quá 1 lần trong (ngày)", default=7, validators=[MaxValueValidator(365)],
        help_text="Một khách nằm trong nhiều chiến dịch thì chiến dịch sau chỉ gửi khi lần gửi trước đã cách bấy nhiêu ngày. "
                  "0 = không giới hạn.")
    tu_de_spam = models.TextField(
        "Từ dễ bị coi là spam", blank=True,
        default="khuyến mãi khủng\ngiảm giá sốc\ncam kết lợi nhuận\nlãi suất 0%\nbấm vào link\nnhận quà miễn phí\nchỉ hôm nay\ncơ hội cuối",
        help_text="Mỗi dòng một cụm từ. Trang gửi tin cảnh báo khi nội dung có các cụm này (không chặn gửi).")
    # Gửi vào nhóm có thời gian chờ riêng (0 = dùng chung với gửi từng người)
    nhom_cho_toi_thieu_giay = models.PositiveIntegerField("Gửi nhóm: chờ tối thiểu (giây)", default=0,
                                                          help_text="0 = dùng chung thời gian chờ ở trên. Nếu đặt thì tối thiểu 30 giây.")
    nhom_cho_toi_da_giay = models.PositiveIntegerField("Gửi nhóm: chờ tối đa (giây)", default=0)
    nhom_nghi_sau_so_lan = models.PositiveSmallIntegerField("Gửi nhóm: nghỉ sau mỗi (nhóm)", default=0,
                                                            validators=[MaxValueValidator(1000)], help_text="0 = dùng chung.")
    nhom_nghi_phut = models.PositiveSmallIntegerField("Gửi nhóm: thời gian nghỉ (phút)", default=0,
                                                      validators=[MaxValueValidator(240)], help_text="0 = dùng chung.")
    nhan_vien_xuat_excel = models.BooleanField(
        "Nhân viên được xuất Excel", default=True,
        help_text="Bỏ chọn thì chỉ quản trị và tài khoản Chỉ xem tải được file Excel kết quả / lịch sử.")
    # Báo cáo Excel tự gửi email cho quản lý
    TAT, HANG_NGAY, HANG_TUAN = "tat", "hang_ngay", "hang_tuan"
    bao_cao_tan_suat = models.CharField(
        "Gửi báo cáo", max_length=10, default=TAT,
        choices=[(TAT, "Không gửi"), (HANG_NGAY, "Cuối mỗi ngày"), (HANG_TUAN, "Cuối mỗi tuần")])
    bao_cao_gio = models.TimeField("Gửi lúc", default=time(18, 0), help_text="Giờ Việt Nam.")
    bao_cao_thu = models.PositiveSmallIntegerField(
        "Gửi vào (báo cáo tuần)", default=5,
        choices=[(0, "Thứ Hai"), (1, "Thứ Ba"), (2, "Thứ Tư"), (3, "Thứ Năm"), (4, "Thứ Sáu"), (5, "Thứ Bảy"), (6, "Chủ nhật")])
    bao_cao_email_nhan = models.TextField(
        "Email nhận báo cáo", blank=True,
        help_text="Mỗi dòng (hoặc cách nhau bằng dấu phẩy) một email quản lý nhận file Excel báo cáo.")
    gmail_gui = models.EmailField("Gmail gửi báo cáo", blank=True, default="nguyenkluanbds@gmail.com",
                                  help_text="Tài khoản Gmail dùng để gửi email báo cáo.")
    gmail_mat_khau = models.CharField(
        "Mật khẩu ứng dụng của Gmail", max_length=64, blank=True,
        help_text="Không phải mật khẩu đăng nhập Gmail. Tạo tại myaccount.google.com → Bảo mật → Xác minh 2 bước (bật) → "
                  "Mật khẩu ứng dụng → đặt tên “Zalo Tool” → chép 16 ký tự vào đây. Để trống khi lưu thì giữ mật khẩu cũ.")
    bao_cao_gui_luc = models.DateTimeField("Gửi báo cáo lần cuối", null=True, blank=True, editable=False)

    def cac_email_nhan(self):
        import re
        return [e for e in re.split(r"[\s,;]+", self.bao_cao_email_nhan or "") if "@" in e]

    class Meta:
        verbose_name = "cài đặt gửi tin"
        verbose_name_plural = "Cài đặt gửi tin"

    def clean(self):
        if self.cho_toi_da_giay < self.cho_toi_thieu_giay:
            raise ValidationError({"cho_toi_da_giay": "Phải lớn hơn hoặc bằng thời gian chờ tối thiểu."})
        if self.gio_bat_dau and self.gio_ket_thuc and self.gio_ket_thuc <= self.gio_bat_dau:
            raise ValidationError({"gio_ket_thuc": "Giờ kết thúc phải sau giờ bắt đầu."})
        if self.nhac_lai_sau_ngay and not self.noi_dung_nhac_lai.strip():
            raise ValidationError({"noi_dung_nhac_lai": "Soạn tin nhắc lại, hoặc đặt “Nhắc lại sau” = 0 để tắt."})

    def save(self, *args, **kwargs):
        # Luôn chỉ có 1 dòng, và luôn giữ giới hạn an toàn
        self.pk = 1
        self.gioi_han_moi_lan = max(0, int(self.gioi_han_moi_lan or 0))     # 0 = không giới hạn
        self.cho_toi_thieu_giay = max(30, int(self.cho_toi_thieu_giay or 60))
        self.cho_toi_da_giay = max(self.cho_toi_thieu_giay, int(self.cho_toi_da_giay or 150))
        self.nghi_sau_so_lan = min(1000, max(0, int(self.nghi_sau_so_lan or 0)))
        self.nghi_phut = min(240, max(1, int(self.nghi_phut or 10)))
        if self.nhom_cho_toi_thieu_giay:
            self.nhom_cho_toi_thieu_giay = max(30, int(self.nhom_cho_toi_thieu_giay))
            self.nhom_cho_toi_da_giay = max(self.nhom_cho_toi_thieu_giay, int(self.nhom_cho_toi_da_giay or 0))
        super().save(*args, **kwargs)

    def cfg_nhom(self):
        """Cấu hình cho đợt gửi vào nhóm: thời gian chờ / nghỉ riêng của gửi nhóm (nếu có đặt)."""
        d = self.to_dict()
        if self.nhom_cho_toi_thieu_giay:
            d["choToiThieuGiay"], d["choToiDaGiay"] = self.nhom_cho_toi_thieu_giay, self.nhom_cho_toi_da_giay
        if self.nhom_nghi_sau_so_lan:
            d["nghiSauSoLan"], d["nghiPhut"] = self.nhom_nghi_sau_so_lan, self.nhom_nghi_phut or self.nghi_phut
        return d

    def cac_tu_spam(self):
        return [x.strip().lower() for x in (self.tu_de_spam or "").splitlines() if x.strip()]

    @classmethod
    def lay(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def to_dict(self):
        return {
            "loiMoiKetBan": self.loi_moi_ket_ban, "noiDungTinNhan": self.noi_dung_tin_nhan,
            "guiKetBan": self.gui_ket_ban, "guiTinNhan": self.gui_tin_nhan,
            "gioiHanMoiLan": self.gioi_han_moi_lan,
            "choToiThieuGiay": self.cho_toi_thieu_giay, "choToiDaGiay": self.cho_toi_da_giay,
            "nghiSauSoLan": self.nghi_sau_so_lan, "nghiPhut": self.nghi_phut,
            "gioiHanMoiNgay": self.gioi_han_moi_ngay, "dungKhiLoiLienTiep": self.dung_khi_loi_lien_tiep,
            # Khung giờ gửi theo giờ Việt Nam, dạng phút trong ngày (worker tự nghỉ ngoài khung giờ)
            "gioBatDau": self.gio_bat_dau.hour * 60 + self.gio_bat_dau.minute,
            "gioKetThuc": self.gio_ket_thuc.hour * 60 + self.gio_ket_thuc.minute,
            "nghiChuNhat": self.nghi_chu_nhat,
            "doiTenGoiNho": self.doi_ten_goi_nho, "mauTenGoiNho": self.mau_ten_goi_nho, "tenDuAn": self.ten_du_an,
            "tuSpam": self.cac_tu_spam(), "nhanVienXuatExcel": self.nhan_vien_xuat_excel,
            # Ảnh gửi kèm tin nhắn cho từng khách (đường dẫn trong data/, worker tự đọc)
            "anh": [f"{THU_MUC_ANH_TIN_NHAN}/{a.tep.name}" for a in self.anh.all()],
        }

    def __str__(self):
        return "Cài đặt gửi tin"


class AnhTinNhan(models.Model):
    """Ảnh gửi kèm tin nhắn cho từng khách (quản trị cài trong Cài đặt gửi tin)."""
    TOI_DA, TOI_DA_MB = 10, 5
    cai_dat = models.ForeignKey(CaiDat, on_delete=models.CASCADE, related_name="anh")
    tep = models.FileField("Ảnh", upload_to=ten_file_anh, storage=kho_anh_tin_nhan,
                           help_text=f"Mỗi khách nhận tin nhắn kèm các ảnh này (tối đa {TOI_DA} ảnh; để trống thì chỉ gửi chữ). "
                                     f"JPG, PNG hoặc WEBP, tối đa {TOI_DA_MB} MB.")
    thu_tu = models.PositiveSmallIntegerField("Thứ tự", default=0)

    class Meta:
        verbose_name = "ảnh đính kèm tin nhắn"
        verbose_name_plural = "Hình ảnh đính kèm tin nhắn"
        ordering = ["thu_tu", "id"]

    def clean(self):
        from .services import loai_anh
        f = self.tep
        if not f or getattr(f, "_committed", True):
            return
        if f.size > self.TOI_DA_MB * 1024 * 1024:
            raise ValidationError({"tep": f"Ảnh lớn hơn {self.TOI_DA_MB} MB."})
        head = f.read(16)
        f.seek(0)
        if not loai_anh(head):
            raise ValidationError({"tep": "File không phải ảnh JPG, PNG hoặc WEBP."})

    def delete(self, *args, **kwargs):
        tep = self.tep
        super().delete(*args, **kwargs)
        if tep:
            tep.delete(save=False)

    def __str__(self):
        return f"Ảnh {self.thu_tu or self.id}"


# ---------- gửi vào nhóm Zalo ----------
class CauHinhNhom(models.Model):
    """Cấu hình gửi nhóm của từng người: các nhóm đích, nội dung và ảnh đính kèm."""
    nguoi_dung = models.OneToOneField(NguoiDung, verbose_name="Tài khoản", on_delete=models.CASCADE, related_name="cau_hinh_nhom")
    noi_dung = models.TextField("Nội dung", blank=True)
    nhom = models.JSONField("Nhóm đích", default=list, blank=True, help_text="[{id, ten}]")
    anh = models.JSONField("Ảnh đính kèm", default=list, blank=True, help_text="Tên file trong data/anh-gui-nhom/<id tài khoản>/")
    cap_nhat = models.DateTimeField("Cập nhật lúc", auto_now=True)

    class Meta:
        verbose_name = "cấu hình gửi nhóm"
        verbose_name_plural = "Cấu hình gửi nhóm"

    @classmethod
    def cua(cls, user):
        return cls.objects.get_or_create(nguoi_dung=user)[0]

    def __str__(self):
        return f"Gửi nhóm của {self.nguoi_dung}"


class LichSuGuiNhom(models.Model):
    nhom_id = models.CharField("ID nhóm", max_length=64, db_index=True)
    ten_nhom = models.CharField("Tên nhóm", max_length=255, blank=True)
    ket_qua = models.CharField("Kết quả", max_length=20)
    noi_dung = models.TextField("Nội dung đã gửi", blank=True)
    so_anh = models.PositiveSmallIntegerField("Số ảnh", default=0)
    tai_khoan_zalo = models.CharField("Gửi từ Zalo", max_length=255, blank=True)
    ghi_chu = models.TextField("Ghi chú", blank=True)
    thoi_gian = models.DateTimeField("Thời gian gửi", db_index=True)
    nguoi_gui = models.ForeignKey(NguoiDung, verbose_name="Người gửi", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="lich_su_gui_nhom")

    class Meta:
        verbose_name = "lịch sử gửi nhóm"
        verbose_name_plural = "Lịch sử gửi nhóm"
        ordering = ["-thoi_gian", "-id"]

    @property
    def thanh_cong(self):
        return self.ket_qua == "Đã gửi"

    def __str__(self):
        return f"{self.ten_nhom or self.nhom_id} lúc {self.thoi_gian:%d/%m/%Y %H:%M}"


# ---------- khách từ chối liên hệ (danh sách đen) ----------
class KhongLienHe(models.Model):
    sdt = models.CharField("SĐT", max_length=10, primary_key=True)
    ly_do = models.CharField("Lý do", max_length=255, blank=True)
    nguoi_them = models.ForeignKey(NguoiDung, verbose_name="Người thêm", null=True, blank=True, on_delete=models.SET_NULL)
    thoi_gian = models.DateTimeField("Thêm lúc", auto_now_add=True)

    class Meta:
        verbose_name = "khách từ chối liên hệ"
        verbose_name_plural = "Khách từ chối liên hệ"
        ordering = ["-thoi_gian"]

    def save(self, *args, **kwargs):
        sdt = chuan_hoa_sdt(self.sdt)
        if not sdt:
            raise ValidationError({"sdt": "Số điện thoại không hợp lệ."})
        self.sdt = sdt
        super().save(*args, **kwargs)

    def __str__(self):
        return self.sdt


# ---------- khách hàng (CRM nhẹ): mỗi SĐT một dòng ----------
class KhachHang(models.Model):
    MOI, DA_GUI, DA_KET_BAN, DA_TRA_LOI, QUAN_TAM, HEN_GAP, KHONG_QUAN_TAM, DA_MUA = (
        "moi", "da_gui", "da_ket_ban", "da_tra_loi", "quan_tam", "hen_gap", "khong_quan_tam", "da_mua")
    TRANG_THAI = [(MOI, "Mới"), (DA_GUI, "Đã gửi"), (DA_KET_BAN, "Đã kết bạn"), (DA_TRA_LOI, "Đã trả lời"),
                  (QUAN_TAM, "Quan tâm"), (HEN_GAP, "Hẹn xem nhà / gặp"), (KHONG_QUAN_TAM, "Không quan tâm"),
                  (DA_MUA, "Đã mua")]
    # Trạng thái tool tự đặt; các trạng thái sau do nhân viên chọn thì tool không ghi đè
    TU_DONG = (MOI, DA_GUI, DA_KET_BAN, DA_TRA_LOI)

    sdt = models.CharField("SĐT", max_length=10, primary_key=True)
    zalo_uid = models.CharField("Zalo ID", max_length=40, blank=True, db_index=True)
    ten_zalo = models.CharField("Tên Zalo", max_length=255, blank=True)
    trang_thai = models.CharField("Trạng thái", max_length=16, choices=TRANG_THAI, default=MOI, db_index=True)
    nhan_vien = models.ForeignKey(NguoiDung, verbose_name="Nhân viên chăm sóc", null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="khach_hang")
    gui_dau_luc = models.DateTimeField("Gửi lần đầu", null=True, blank=True)
    dong_y_luc = models.DateTimeField("Đồng ý kết bạn lúc", null=True, blank=True, db_index=True)
    tra_loi_luc = models.DateTimeField("Trả lời gần nhất", null=True, blank=True, db_index=True)
    so_lan_tra_loi = models.PositiveIntegerField("Số lần trả lời", default=0)
    da_nhac_lai = models.BooleanField("Đã nhắc lại", default=False)
    ghi_chu = models.TextField("Ghi chú", blank=True)
    cap_nhat = models.DateTimeField("Cập nhật", auto_now=True)

    class Meta:
        verbose_name = "khách hàng"
        verbose_name_plural = "Khách hàng"
        ordering = ["-cap_nhat"]

    def dat_trang_thai_tu_dong(self, tt):
        """Tool chỉ nâng trạng thái tự động (Mới → Đã gửi → Đã kết bạn → Đã trả lời), không đè trạng thái nhân viên chọn."""
        if self.trang_thai in self.TU_DONG and self.TU_DONG.index(tt) > self.TU_DONG.index(self.trang_thai):
            self.trang_thai = tt

    def __str__(self):
        return self.sdt


# ---------- đợt gửi ----------
class DotGui(models.Model):
    DANG_CHAY, XONG, DA_DUNG, TU_DUNG, GIAN_DOAN = "dang_chay", "xong", "da_dung", "tu_dung", "gian_doan"
    TRANG_THAI = [(DANG_CHAY, "Đang chạy"), (XONG, "Hoàn tất"), (DA_DUNG, "Đã dừng"), (TU_DUNG, "Tự dừng vì lỗi"),
                  (GIAN_DOAN, "Bị gián đoạn")]
    CA_NHAN, NHOM, NHAC_LAI = "ca_nhan", "nhom", "nhac_lai"
    LOAI = [(CA_NHAN, "Gửi từng người"), (NHOM, "Gửi nhóm"), (NHAC_LAI, "Nhắc lại tự động")]

    nguoi_gui = models.ForeignKey(NguoiDung, verbose_name="Người gửi", null=True, on_delete=models.SET_NULL,
                                  related_name="dot_gui")
    loai = models.CharField("Loại", max_length=10, choices=LOAI, default=CA_NHAN)
    bat_dau = models.DateTimeField("Bắt đầu", auto_now_add=True, db_index=True)
    ket_thuc = models.DateTimeField("Kết thúc", null=True, blank=True)
    tong = models.PositiveIntegerField("Tổng", default=0)
    da_gui = models.PositiveIntegerField("Đã xử lý", default=0)
    trang_thai = models.CharField("Trạng thái", max_length=10, choices=TRANG_THAI, default=DANG_CHAY, db_index=True)
    danh_sach = models.JSONField("Danh sách", default=list, blank=True, help_text="SĐT (hoặc id nhóm) của đợt.")
    ghi_chu = models.CharField("Ghi chú", max_length=255, blank=True)

    class Meta:
        verbose_name = "đợt gửi"
        verbose_name_plural = "Đợt gửi"
        ordering = ["-bat_dau"]

    def __str__(self):
        return f"Đợt {self.id} ({self.get_trang_thai_display()})"


# ---------- hẹn giờ gửi (một lần hoặc lặp lại) ----------
class LichHen(models.Model):
    CA_NHAN, NHOM = "ca_nhan", "nhom"
    LOAI = [(CA_NHAN, "Gửi danh sách khách"), (NHOM, "Gửi vào nhóm")]
    MOT_LAN, HANG_NGAY, HANG_TUAN = "mot_lan", "hang_ngay", "hang_tuan"
    LAP = [(MOT_LAN, "Một lần"), (HANG_NGAY, "Hằng ngày"), (HANG_TUAN, "Hằng tuần")]

    nguoi = models.ForeignKey(NguoiDung, verbose_name="Người hẹn", on_delete=models.CASCADE, related_name="lich_hen")
    loai = models.CharField("Gửi gì", max_length=10, choices=LOAI, default=CA_NHAN)
    lap = models.CharField("Lặp lại", max_length=10, choices=LAP, default=MOT_LAN)
    luc = models.DateTimeField("Lúc (một lần)", null=True, blank=True)
    gio = models.TimeField("Giờ (lặp lại)", null=True, blank=True)
    thu = models.JSONField("Thứ (hằng tuần)", default=list, blank=True, help_text="0 = thứ Hai … 6 = Chủ nhật")
    bo_nhom = models.ForeignKey("BoNhom", verbose_name="Bộ nhóm", null=True, blank=True, on_delete=models.SET_NULL,
                                help_text="Gửi nhóm: để trống thì gửi theo các nhóm đang chọn ở trang Gửi nhóm.")
    dang_bat = models.BooleanField("Đang bật", default=True)
    lan_chay_cuoi = models.DateTimeField("Chạy lần cuối", null=True, blank=True)
    ket_qua_cuoi = models.CharField("Kết quả lần cuối", max_length=255, blank=True)
    tao_luc = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        verbose_name = "lịch hẹn gửi"
        verbose_name_plural = "Lịch hẹn gửi"
        ordering = ["-tao_luc"]

    def lan_toi(self, sau):
        """Lần chạy kế tiếp sau thời điểm `sau` (aware), None nếu không còn."""
        from datetime import datetime, timedelta

        from django.utils import timezone
        if not self.dang_bat:
            return None
        if self.lap == self.MOT_LAN:
            return self.luc if self.luc and (not self.lan_chay_cuoi) else None
        if not self.gio:
            return None
        ngay = timezone.localtime(sau).date()
        for k in range(0, 8):
            d = ngay + timedelta(days=k)
            if self.lap == self.HANG_TUAN and d.weekday() not in (self.thu or []):
                continue
            t = timezone.make_aware(datetime.combine(d, self.gio))
            if t > sau:
                return t
        return None

    def mo_ta(self):
        if self.lap == self.MOT_LAN:
            from django.utils import timezone
            return f"Một lần lúc {timezone.localtime(self.luc):%H:%M %d/%m/%Y}" if self.luc else "Một lần"
        g = f"{self.gio:%H:%M}" if self.gio else "?"
        if self.lap == self.HANG_NGAY:
            return f"Hằng ngày lúc {g}"
        ten = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
        return f"Hằng tuần ({', '.join(ten[i] for i in sorted(self.thu or []) if 0 <= i <= 6)}) lúc {g}"

    def __str__(self):
        return f"{self.get_loai_display()}: {self.mo_ta()}"


# ---------- bộ nhóm Zalo lưu sẵn ----------
class BoNhom(models.Model):
    nguoi = models.ForeignKey(NguoiDung, verbose_name="Tài khoản", on_delete=models.CASCADE, related_name="bo_nhom")
    ten = models.CharField("Tên bộ nhóm", max_length=100)
    nhom = models.JSONField("Các nhóm", default=list, blank=True, help_text="[{id, ten, noiDung}]")

    class Meta:
        verbose_name = "bộ nhóm"
        verbose_name_plural = "Bộ nhóm"
        ordering = ["ten"]
        unique_together = [("nguoi", "ten")]

    def __str__(self):
        return self.ten


# ---------- thư viện mẫu tin nhắn ----------
class MauTinNhan(models.Model):
    TIN_NHAN, LOI_MOI, NHOM = "tin_nhan", "loi_moi", "nhom"
    LOAI = [(TIN_NHAN, "Tin nhắn cho khách"), (LOI_MOI, "Lời mời kết bạn"), (NHOM, "Tin gửi nhóm")]
    ten = models.CharField("Tên mẫu", max_length=150)
    loai = models.CharField("Dùng cho", max_length=10, choices=LOAI, default=TIN_NHAN)
    noi_dung = models.TextField("Nội dung", help_text="Dùng được {ten}, {duan}, {nhanvien}, {xungho}. " + HD_XAO)
    da_duyet = models.BooleanField("Đã duyệt", default=False, help_text="Chỉ mẫu đã duyệt mới hiện cho nhân viên chọn.")
    nguoi_tao = models.ForeignKey(NguoiDung, verbose_name="Người tạo", null=True, blank=True, on_delete=models.SET_NULL)
    tao_luc = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        verbose_name = "mẫu tin nhắn"
        verbose_name_plural = "Thư viện mẫu tin nhắn"
        ordering = ["loai", "ten"]

    def __str__(self):
        return self.ten


# ---------- nhật ký ----------
class NhatKy(models.Model):
    MUC = [("info", "Thông tin"), ("ok", "Thành công"), ("warn", "Cảnh báo"), ("error", "Lỗi")]
    thoi_gian = models.DateTimeField("Thời gian", auto_now_add=True, db_index=True)
    muc = models.CharField("Mức", max_length=10, choices=MUC, default="info")
    noi_dung = models.TextField("Nội dung")
    nguoi_dung = models.ForeignKey(NguoiDung, verbose_name="Người thao tác", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "nhật ký"
        verbose_name_plural = "Nhật ký"
        ordering = ["-id"]

    def __str__(self):
        return self.noi_dung[:80]
