"""Cấu hình Django cho Zalo Tool. Kết nối MySQL và các mã bí mật đọc từ config.json ở thư mục gốc."""
import json
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent      # web/
ROOT_DIR = BASE_DIR.parent                             # thư mục zalo-tool
CONFIG_FILE = ROOT_DIR / "config.json"


def _load_config():
    """Đọc config.json, tự sinh các mã bí mật còn thiếu rồi ghi lại."""
    conf = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    changed = False
    if not conf.get("djangoSecretKey"):
        conf["djangoSecretKey"] = secrets.token_urlsafe(50)
        changed = True
    worker = conf.setdefault("worker", {})
    if len(str(worker.get("token", ""))) < 32:
        worker["token"] = secrets.token_hex(32)
        changed = True
    worker.setdefault("port", 3100)
    conf.setdefault("django", {}).setdefault("port", 8000)
    conf.setdefault("mysql", {})
    conf.setdefault("moChoMangNoiBo", False)
    if changed or "port" not in conf["django"]:
        CONFIG_FILE.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return conf


CONF = _load_config()
MYSQL = {"host": "127.0.0.1", "port": 3306, "user": "root", "password": "", "database": "zalo_tool", **CONF["mysql"]}
# Cho phép chạy thử trên database khác mà không đụng dữ liệu thật
MYSQL["database"] = os.environ.get("ZALO_TOOL_DB", MYSQL["database"])

SECRET_KEY = CONF["djangoSecretKey"]
DEBUG = False
LAN = CONF.get("moChoMangNoiBo") is True
ALLOWED_HOSTS = ["*"] if LAN else ["127.0.0.1", "localhost"]

# Zalo worker (Node) chạy nội bộ
WORKER_URL = f"http://127.0.0.1:{int(os.environ.get('ZALO_WORKER_PORT') or CONF['worker']['port'])}"
WORKER_TOKEN = CONF["worker"]["token"]
DJANGO_PORT = int(os.environ.get("ZALO_TOOL_PORT") or CONF["django"]["port"])

INSTALLED_APPS = [
    "jazzmin",
    "guitin.apps.ZaloAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "guitin.apps.GuiTinConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "zalotool.urls"
WSGI_APPLICATION = "zalotool.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": MYSQL["database"],
        "USER": MYSQL["user"],
        "PASSWORD": MYSQL["password"],
        "HOST": MYSQL["host"],
        "PORT": str(MYSQL["port"]),
        "OPTIONS": {"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"},
    }
}

# Tài khoản đăng nhập bằng email
AUTH_USER_MODEL = "guitin.NguoiDung"
AUTHENTICATION_BACKENDS = ["guitin.auth.GioiHanDangNhapBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]
LOGIN_URL = "dang-nhap"
LOGIN_REDIRECT_URL = "trang-chu"
LOGOUT_REDIRECT_URL = "dang-nhap"
SESSION_COOKIE_AGE = 7 * 24 * 3600
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR"}},
}

# ---------- Giao diện trang quản trị (Jazzmin) ----------
JAZZMIN_SETTINGS = {
    "site_title": "Zalo Tool",
    "site_header": "Zalo Tool",
    "site_brand": "Zalo Tool",
    "welcome_sign": "Đăng nhập trang quản trị",
    "copyright": "Zalo Tool",
    "search_model": ["guitin.NguoiNhan", "guitin.NguoiDung"],
    "user_avatar": None,
    "topmenu_links": [
        {"name": "Bảng điều khiển", "url": "admin:index"},
        {"name": "Gửi tin vào nhóm", "url": "gui-nhom"},
        {"model": "guitin.NguoiNhan"},
        {"model": "guitin.NguoiDung"},
    ],
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_apps": ["auth"],
    "order_with_respect_to": ["guitin", "guitin.NguoiNhan", "guitin.LichSuGui", "guitin.LichSuGuiNhom", "guitin.KetQua", "guitin.NguoiDung", "guitin.CaiDat", "guitin.NhatKy"],
    "icons": {
        "guitin.NguoiDung": "fas fa-user-shield",
        "guitin.NguoiNhan": "fas fa-address-book",
        "guitin.KetQua": "fas fa-paper-plane",
        "guitin.LichSuGui": "fas fa-history",
        "guitin.LichSuGuiNhom": "fas fa-users",
        "guitin.CaiDat": "fas fa-sliders-h",
        "guitin.NhatKy": "fas fa-clipboard-list",
    },
    "default_icon_parents": "fas fa-folder",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": True,
    "changeform_format": "horizontal_tabs",
    "language_chooser": False,
}
JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "navbar": "navbar-primary navbar-dark",
    "sidebar": "sidebar-dark-primary",
    "brand_colour": "navbar-primary",
    "accent": "accent-primary",
    "button_classes": {"primary": "btn-primary", "secondary": "btn-outline-secondary", "info": "btn-info",
                       "warning": "btn-warning", "danger": "btn-danger", "success": "btn-success"},
}
