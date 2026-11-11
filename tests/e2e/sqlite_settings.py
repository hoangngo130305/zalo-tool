from zalotool.settings import *  # noqa
import os
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ.get("SQ_DB", os.path.join(os.path.dirname(__file__), "db.sqlite3"))}}
