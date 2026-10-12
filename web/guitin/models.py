import re

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def chuan_hoa_sdt(raw):
    """Chuẩn hoá SĐT: Excel hay làm mất số 0 đầu, hoặc ghi +84 / 84. Trả về None nếu không hợp lệ."""
    p = re.sub(r"[^\d+]", "", str(raw or ""))
    if p.startswith("+84"):
        p = "0" + p[3:]
    elif p.startswith("84") and len(p) == 11:
        p = "0" + p[2:]
    elif len(p) == 9:
        p = "0" + p
    return p if re.fullmatch(r"0\d{9}", p) else None


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
                  "để trống thì dùng nội dung mặc định trong Cài đặt gửi tin.")

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
                                help_text="Để trống thì dùng nội dung mặc định trong Cài đặt gửi tin. Có thể dùng {ten}.")
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
                                       help_text="{ten} sẽ được thay bằng tên từng người. Nên dưới 150 ký tự.")
    noi_dung_tin_nhan = models.TextField("Tin nhắn", default="Chào {ten}!", help_text="{ten} sẽ được thay bằng tên từng người.")
    gui_ket_ban = models.BooleanField("Gửi lời mời kết bạn", default=True)
    gui_tin_nhan = models.BooleanField("Gửi tin nhắn", default=True)
    gioi_han_moi_lan = models.PositiveSmallIntegerField("Số người tối đa mỗi lần", default=20,
                                                         validators=[MinValueValidator(1), MaxValueValidator(50)])
    cho_toi_thieu_giay = models.PositiveIntegerField("Chờ tối thiểu (giây)", default=60, validators=[MinValueValidator(30)],
                                                      help_text="Không cho dưới 30 giây để tránh bị Zalo khoá tài khoản.")
    cho_toi_da_giay = models.PositiveIntegerField("Chờ tối đa (giây)", default=150, validators=[MinValueValidator(30)])

    class Meta:
        verbose_name = "cài đặt gửi tin"
        verbose_name_plural = "Cài đặt gửi tin"

    def clean(self):
        if self.cho_toi_da_giay < self.cho_toi_thieu_giay:
            raise ValidationError({"cho_toi_da_giay": "Phải lớn hơn hoặc bằng thời gian chờ tối thiểu."})

    def save(self, *args, **kwargs):
        # Luôn chỉ có 1 dòng, và luôn giữ giới hạn an toàn
        self.pk = 1
        self.gioi_han_moi_lan = min(50, max(1, int(self.gioi_han_moi_lan or 20)))
        self.cho_toi_thieu_giay = max(30, int(self.cho_toi_thieu_giay or 60))
        self.cho_toi_da_giay = max(self.cho_toi_thieu_giay, int(self.cho_toi_da_giay or 150))
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
        }

    def __str__(self):
        return "Cài đặt gửi tin"


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
