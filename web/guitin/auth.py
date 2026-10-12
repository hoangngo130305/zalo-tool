"""Đăng nhập bằng email, sai 5 lần thì khoá 5 phút theo địa chỉ IP (áp dụng cho cả trang quản trị)."""
from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

SAI_TOI_DA = 5
KHOA_GIAY = 5 * 60


def ip_cua(request):
    return (request.META.get("REMOTE_ADDR") or "?") if request else "?"


def con_bi_khoa(request):
    """Số phút còn bị khoá (0 nếu không bị khoá)."""
    het = cache.get(f"khoa:{ip_cua(request)}")
    if not het:
        return 0
    import time
    return max(1, int((het - time.time() + 59) // 60))


class GioiHanDangNhapBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        import time
        ip = ip_cua(request)
        if con_bi_khoa(request):
            raise PermissionDenied("Sai quá nhiều lần")
        if username:
            username = username.strip().lower()
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            dem = cache.get(f"sai:{ip}", 0) + 1
            cache.set(f"sai:{ip}", dem, KHOA_GIAY)
            if dem >= SAI_TOI_DA:
                cache.set(f"khoa:{ip}", time.time() + KHOA_GIAY, KHOA_GIAY)
                cache.delete(f"sai:{ip}")
        else:
            cache.delete(f"sai:{ip}")
        return user
