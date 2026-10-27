import re

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
    QUAN_TRI, NHAN_VIEN, CHI_XEM = "admin", "nhanvien", "xem"
    VAI_TRO = [(QUAN_TRI, "Quản trị"), (NHAN_VIEN, "Nhân viên"), (CHI_XEM, "Chỉ xem")]
    # "all" = thấy toàn bộ danh sách; nhân viên thiếu quyền này nên chỉ thấy số được giao cho mình
    QUYEN = {
        QUAN_TRI: {"view", "all", "list", "send", "settings", "zalo", "users"},
        NHAN_VIEN: {"view", "list", "send"},
        CHI_XEM: {"view", "all"},
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
        return self.is_active and q in self.quyen

    def __str__(self):
        return self.ho_ten or self.email


# ---------- người nhận ----------
class NguoiNhan(models.Model):
    thu_tu = models.PositiveIntegerField("Thứ tự", default=0, db_index=True)
    sdt_goc = models.CharField("Số điện thoại", max_length=32, blank=True)
    sdt = models.CharField("SĐT chuẩn hoá", max_length=10, null=True, blank=True, db_index=True, editable=False)
    ten = models.CharField("Tên", max_length=255, blank=True)
    noi_dung = models.TextField("Nội dung tin nhắn riêng", blank=True,
                                help_text="Để trống thì dùng nội dung mặc định trong Cài đặt gửi tin. Có thể dùng {ten}. " + HD_XAO)
    du_an = models.CharField("Dự án", max_length=100, blank=True, db_index=True,
                             help_text="Ghi vào tên gợi nhớ ({duan}). Để trống thì dùng tên dự án mặc định trong Cài đặt gửi tin.")
    phu_trach = models.ForeignKey(
        NguoiDung, verbose_name="Nhân viên phụ trách", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="nguoi_nhan", limit_choices_to={"vai_tro__in": [NguoiDung.QUAN_TRI, NguoiDung.NHAN_VIEN]},
    )
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

    class Meta:
        verbose_name = "kết quả gửi (mới nhất)"
        verbose_name_plural = "Kết quả gửi"
        ordering = ["-thoi_gian"]

    @property
    def loi(self):
        return self.tin_nhan == "Lỗi" or self.ket_ban == "Lỗi"

    def __str__(self):
        return f"{self.sdt}: kết bạn {self.ket_ban}, tin nhắn {self.tin_nhan}"


# ---------- lịch sử gửi: mỗi lần gửi một dòng, kể cả gửi lại ----------
class LichSuGui(models.Model):
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

    class Meta:
        verbose_name = "lịch sử gửi"
        verbose_name_plural = "Lịch sử gửi"
        ordering = ["-thoi_gian", "-id"]

    @property
    def thanh_cong(self):
        return self.tin_nhan == "Đã gửi" or (self.tin_nhan == "-" and self.ket_ban == "Đã gửi")

    def __str__(self):
        return f"{self.sdt} lúc {self.thoi_gian:%d/%m/%Y %H:%M}"


# ---------- cài đặt (chỉ 1 dòng) ----------
class CaiDat(models.Model):
    loi_moi_ket_ban = models.TextField("Lời mời kết bạn", default="Chào {ten}, mình xin phép kết bạn để tiện trao đổi ạ.",
                                       help_text="{ten} sẽ được thay bằng tên từng người. Nên dưới 150 ký tự (mỗi mẫu). "
                                                 + HD_XAO)
    noi_dung_tin_nhan = models.TextField("Tin nhắn", default="Chào {ten}!",
                                         help_text="{ten} sẽ được thay bằng tên từng người. " + HD_XAO)
    gui_ket_ban = models.BooleanField("Gửi lời mời kết bạn", default=True)
    gui_tin_nhan = models.BooleanField("Gửi tin nhắn", default=True)
    gioi_han_moi_lan = models.PositiveIntegerField(
        "Số người tối đa mỗi lần", default=0,
        help_text="0 = không giới hạn: bấm gửi một lần là tool gửi hết danh sách chờ. "
                  "Nên bật “Nghỉ giữa các lượt” để tool tự nghỉ sau mỗi X người.")
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

    class Meta:
        verbose_name = "cài đặt gửi tin"
        verbose_name_plural = "Cài đặt gửi tin"

    def clean(self):
        if self.cho_toi_da_giay < self.cho_toi_thieu_giay:
            raise ValidationError({"cho_toi_da_giay": "Phải lớn hơn hoặc bằng thời gian chờ tối thiểu."})

    def save(self, *args, **kwargs):
        # Luôn chỉ có 1 dòng, và luôn giữ giới hạn an toàn
        self.pk = 1
        self.gioi_han_moi_lan = max(0, int(self.gioi_han_moi_lan or 0))     # 0 = không giới hạn
        self.cho_toi_thieu_giay = max(30, int(self.cho_toi_thieu_giay or 60))
        self.cho_toi_da_giay = max(self.cho_toi_thieu_giay, int(self.cho_toi_da_giay or 150))
        self.nghi_sau_so_lan = min(1000, max(0, int(self.nghi_sau_so_lan or 0)))
        self.nghi_phut = min(240, max(1, int(self.nghi_phut or 10)))
        super().save(*args, **kwargs)

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
            "doiTenGoiNho": self.doi_ten_goi_nho, "mauTenGoiNho": self.mau_ten_goi_nho, "tenDuAn": self.ten_du_an,
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
