"""Đăng nhập bằng email, sai 5 lần thì khoá 5 phút (áp dụng cho cả trang quản trị).
Khoá theo cặp IP + email: một người gõ sai không khoá luôn những người khác dùng chung máy / mạng."""
import hashlib
import time

from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

SAI_TOI_DA = 5
KHOA_GIAY = 5 * 60


def ip_cua(request):
    return (request.META.get("REMOTE_ADDR") or "?") if request else "?"


def _khoa(request, email):
    email = (email or "").strip().lower()
    return hashlib.sha256(f"{ip_cua(request)}|{email}".encode()).hexdigest()[:32]


def con_bi_khoa(request, email=""):
    """Số phút còn bị khoá (0 nếu không bị khoá)."""
    het = cache.get(f"khoa:{_khoa(request, email)}")
    if not het:
        return 0
    return max(1, int((het - time.time() + 59) // 60))


class GioiHanDangNhapBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            username = username.strip().lower()
        k = _khoa(request, username)
        if con_bi_khoa(request, username):
            raise PermissionDenied("Sai quá nhiều lần")
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            dem = cache.get(f"sai:{k}", 0) + 1
            cache.set(f"sai:{k}", dem, KHOA_GIAY)
            if dem >= SAI_TOI_DA:
                cache.set(f"khoa:{k}", time.time() + KHOA_GIAY, KHOA_GIAY)
                cache.delete(f"sai:{k}")
        else:
            cache.delete(f"sai:{k}")
        return user
