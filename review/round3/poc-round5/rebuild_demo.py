"""Rebuild the round-3 review demo project (same sources as the original)."""

from __future__ import annotations

import sys
from pathlib import Path

# The flag value never enters git: read it from the ignored side file.
FLAG_PATH = Path(__file__).resolve().parents[1] / "flag-value.txt"
FLAG = FLAG_PATH.read_text(encoding="utf-8").strip()


def main(target: str) -> int:
    project = Path(target).resolve()
    if project.exists():
        raise SystemExit(f"refusing to overwrite {project}")
    (project / "sampleproj").mkdir(parents=True)
    (project / "demo" / "management" / "commands").mkdir(parents=True)
    (project / "demo" / "templates" / "demo").mkdir(parents=True)
    (project / "manage.py").write_text('''#!/usr/bin/env python3
import os
import sys

def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sampleproj.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)

if __name__ == "__main__":
    main()
''', encoding="utf-8")
    (project / "sampleproj" / "__init__.py").write_text("", encoding="utf-8")
    (project / "sampleproj" / "settings.py").write_text('''from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "round3-review-demo-not-a-real-secret"
DEBUG = False
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["django.contrib.staticfiles", "demo"]
ROOT_URLCONF = "sampleproj.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": []},
}]
WSGI_APPLICATION = "sampleproj.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
STATIC_URL = "/static/"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
''', encoding="utf-8")
    (project / "sampleproj" / "urls.py").write_text('''from django.urls import include, path
urlpatterns = [path("", include("demo.urls"))]
''', encoding="utf-8")
    (project / "sampleproj" / "wsgi.py").write_text('''import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sampleproj.settings")
application = get_wsgi_application()
''', encoding="utf-8")
    (project / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (project / "demo" / "apps.py").write_text('''from django.apps import AppConfig
class DemoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "demo"
''', encoding="utf-8")
    (project / "demo" / "views.py").write_text('''from django.http import HttpResponse

def index(request):
    return HttpResponse("PyShield round 3 demo is running")
''', encoding="utf-8")
    (project / "demo" / "urls.py").write_text('''from django.urls import path
from . import views
urlpatterns = [path("", views.index, name="index")]
''', encoding="utf-8")
    (project / "demo" / "management" / "__init__.py").write_text("", encoding="utf-8")
    (project / "demo" / "management" / "commands" / "__init__.py").write_text("", encoding="utf-8")
    (project / "demo" / "management" / "commands" / "reveal.py").write_text('''from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Check that the protected demo can execute a management command."

    def handle(self, *args, **options):
        self.stdout.write("protected demo command OK")
''', encoding="utf-8")
    (project / "demo" / "templates" / "demo" / "index.html").write_text(
        "<h1>PyShield round 3 demo</h1>\n", encoding="utf-8",
    )
    (project / "requirements.txt").write_text("Django>=5.0,<6\n", encoding="utf-8")
    # The flag must be assigned before the unconditional raise: CPython drops
    # code after an unconditional raise, including its constants.
    (project / "demo" / "hidden_flag.py").write_text(
        '"""This module deliberately refuses to run; the flag is encrypted in the stub payload."""\n\n'
        f'FLAG = "{FLAG}"\n\n'
        'raise RuntimeError("hidden_flag is not an executable module")\n',
        encoding="utf-8",
    )
    print(project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
