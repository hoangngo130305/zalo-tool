from django.core.management.base import BaseCommand, CommandError

from guitin.models import NguoiDung


class Command(BaseCommand):
    help = "Tạo (hoặc đặt lại mật khẩu) tài khoản quản trị đăng nhập bằng email."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--ho-ten", default="Quản trị")

    def handle(self, *args, email, password, ho_ten, **kwargs):
        if len(password) < 6:
            raise CommandError("Mật khẩu cần ít nhất 6 ký tự")
        email = email.strip().lower()
        user = NguoiDung.objects.filter(email=email).first()
        if user:
            user.set_password(password)
            user.vai_tro = NguoiDung.QUAN_TRI
            user.is_active = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Đã đặt lại mật khẩu và quyền quản trị cho {email}"))
        else:
            NguoiDung.objects.create_superuser(email=email, password=password, ho_ten=ho_ten)
            self.stdout.write(self.style.SUCCESS(f"Đã tạo tài khoản quản trị {email}"))
