from django.middleware.gzip import GZipMiddleware


class NenJsonMiddleware(GZipMiddleware):
    """Nén gzip các phản hồi JSON (danh sách vài nghìn số ~1 MB còn ~10%), giúp trang tải nhanh khi nhiều người cùng mở.
    Không nén trang HTML (có mã CSRF) để tránh kiểu tấn công BREACH."""

    def process_response(self, request, response):
        if not response.get("Content-Type", "").startswith("application/json"):
            return response
        return super().process_response(request, response)
