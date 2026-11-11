"""Test đầu-cuối: Django (SQLite) + worker thật + Zalo giả (tests/zalo-gia). Chạy: python tests/e2e/chay.py
13 kịch bản: gửi, tự dừng, hạn mức, khung giờ, dừng, gửi nhóm, khách đồng ý / trả lời, nhắc lại, gián đoạn, phiên hết hạn,
tin tự gửi qua lần khởi động lại worker. Không dùng database / Zalo thật."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
PROJ = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]   # mặc định: thư mục dự án
SQ = Path(__file__).parent
WK = SQ / "wk"
DB = SQ / "e2e.sqlite3"
DJ_PORT, WK_PORT = 8766, 3199
PY = str(PROJ / ".venv" / "Scripts" / "python.exe")
env = {**os.environ, "PYTHONPATH": str(SQ), "DJANGO_SETTINGS_MODULE": "sqlite_settings", "PYTHONIOENCODING": "utf-8",
       "SQ_DB": str(DB), "ZALO_WORKER_PORT": str(WK_PORT)}
loi = []


def kiem(dk, mo_ta):
    print(("  OK  " if dk else "  LỖI ") + mo_ta)
    if not dk:
        loi.append(mo_ta)


def shell(code):
    r = subprocess.run([PY, "manage.py", "shell", "-v0", "-c", code], cwd=PROJ / "web", env=env,
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        print(r.stderr)
        raise SystemExit("shell lỗi")
    return r.stdout.strip()


# ---------- chuẩn bị ----------
DB.unlink(missing_ok=True)
subprocess.run([PY, "manage.py", "migrate", "-v0"], cwd=PROJ / "web", env=env, check=True)
NV = int(shell("""
from guitin.models import *; from guitin import services as sv
NguoiDung.objects.create_superuser('admin@demo.vn','demo1234',ho_ten='Quản trị')
n=NguoiDung.objects.create_user('nv@demo.vn','demo1234',ho_ten='Bonnie Nguyễn',loi_moi_ket_ban='Em {nhanvien} dự án {duan} chào {ten}')
c=CaiDat.lay(); c.noi_dung_tin_nhan='Chào {ten}, thông tin {duan}'; c.ten_du_an='Palm City'
import datetime; c.gio_bat_dau=datetime.time(0,0); c.gio_ket_thuc=datetime.time(23,59); c.dung_khi_loi_lien_tiep=3; c.save()
sv.nhap_them([{'sdt':'0901000001','ten':'Anh Nam'},{'sdt':'0901000002','ten':'Chị Lan'},{'sdt':'0901000003'},
              {'sdt':'0901000088','ten':'Chưa có Zalo'},{'sdt':'0901000004','ten':'Từ chối'}], n)
KhongLienHe.objects.create(sdt='0901000004')
print(n.id)"""))
if WK.exists():
    shutil.rmtree(WK)
(WK / "data" / "zalo-sessions").mkdir(parents=True)
shutil.copytree(PROJ / "src", WK / "src")
shutil.copy(PROJ / "package.json", WK / "package.json")
conf = json.loads((PROJ / "config.json").read_text(encoding="utf-8"))
conf["worker"]["port"] = WK_PORT
conf.setdefault("django", {})["port"] = DJ_PORT
(WK / "config.json").write_text(json.dumps(conf), encoding="utf-8")
(WK / "data" / "zalo-sessions" / f"{NV}.json").write_text("{}")
(WK / "data" / "zalo-sessions" / "999.json").write_text('{"hetHan": true}')    # phiên đã hết hạn

dj = subprocess.Popen([PY, "manage.py", "runserver", f"127.0.0.1:{DJ_PORT}", "--noreload"], cwd=PROJ / "web", env=env,
                      stdout=open(SQ / "e2e-django.log", "w"), stderr=subprocess.STDOUT)
hook = (PROJ / "tests" / "zalo-gia" / "dang-ky.mjs").as_uri()


def chay_worker():
    return subprocess.Popen(["node", "--import", hook, "src/worker.js"], cwd=WK, stdout=open(SQ / "e2e-worker.log", "a"),
                            stderr=subprocess.STDOUT, env={**os.environ, "TANG_TOC": "200"})


wk = chay_worker()
time.sleep(5)


def su_kien(loai, uid):
    with open(WK / "data" / "zalo-gia-su-kien.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"loai": loai, "uid": uid}) + "\n")

# ---------- phiên đăng nhập web của nhân viên ----------
BASE = f"http://127.0.0.1:{DJ_PORT}"
jar = CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
op.open(BASE + "/dang-nhap/")
csrf = lambda: next(c.value for c in jar if c.name == "csrftoken")  # noqa: E731
op.open(urllib.request.Request(BASE + "/dang-nhap/", urllib.parse.urlencode(
    {"username": "nv@demo.vn", "password": "demo1234", "csrfmiddlewaretoken": csrf()}).encode(), headers={"Referer": BASE}))


def api(path, body=None):
    req = urllib.request.Request(BASE + path, None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-CSRFToken": csrf(), "Referer": BASE})
    try:
        return 200, json.loads(op.open(req).read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def cho_xong(toi_da=120):
    t = time.time()
    time.sleep(2)
    while time.time() - t < toi_da:
        if not api("/api/poll")[1].get("running"):
            return True
        time.sleep(1)
    return False


def da_gui():
    f = WK / "data" / "zalo-gia-da-gui.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


try:
    s = api("/api/state")[1]
    kiem(s.get("login") == "in", f"Zalo giả tự kết nối lại từ phiên đã lưu (login={s.get('login')})")
    kiem(s.get("hanMuc", {}).get("toiDa") == 0, "Mặc định không giới hạn người mỗi ngày")
    wlog = (SQ / "e2e-worker.log").read_text(encoding="utf-8")
    kiem("#999 Phiên Zalo cũ đã hết hạn" in wlog and "#999 Đang tạo mã QR" not in wlog,
         "Phiên hết hạn lúc khởi động: chỉ báo, không tự tạo mã QR lặp mãi")

    print("A. Gửi bình thường, không giới hạn")
    code, r = api("/api/start", {})
    kiem(code == 200, f"Bắt đầu gửi ({code} {r})")
    kiem(cho_xong(), "Đợt gửi chạy xong")
    out = json.loads(shell("""
from guitin.models import *; import json
print(json.dumps({'ls': list(LichSuGui.objects.order_by('sdt').values_list('sdt','ket_ban','tin_nhan')),
 'ma': LichSuGui.objects.exclude(ma=None).count()}))"""))
    kiem([x[0] for x in out["ls"]] == ["0901000001", "0901000002", "0901000003", "0901000088"],
         f"Gửi đủ 4 số, bỏ số khách từ chối ({[x[0] for x in out['ls']]})")
    kiem(out["ma"] == 4, "Mỗi kết quả có mã chống ghi trùng")
    kiem(["0901000088", "Lỗi", "Lỗi"] in out["ls"], "Số chưa có Zalo ghi Lỗi")
    g = da_gui()
    moi = [x["msg"] for x in g if x["loai"] == "ket-ban"]
    kiem("Em Bonnie Nguyễn dự án Palm City chào Anh Nam" in moi, f"Lời mời điền {{nhanvien}} {{duan}} {{ten}}: {moi[:1]}")
    tin = [x["msg"] for x in g if x["loai"] == "tin-nhan"]
    kiem("Chào Anh Nam, thông tin Palm City" in tin, f"Tin nhắn điền {{duan}}: {tin[:1]}")
    kiem(any(x.get("alias") == "Anh Nam 0901000001 Palm City" for x in g), "Tên gợi nhớ có dự án")
    kiem(not (WK / "data" / "cho-bao-cao.jsonl").read_text().strip(), "Hàng đợi kết quả trống sau khi web đã nhận")
    nk = shell("from guitin.models import *; print(NhatKy.objects.filter(noi_dung__startswith='Chờ ').count())")
    kiem(nk == "0", "Không ghi dòng 'Chờ X giây' vào nhật ký")

    print("B. Zalo báo lỗi liên tiếp -> tự dừng + tạm dừng")
    shell("""
from guitin import services as sv; from guitin.models import NguoiDung
sv.nhap_them([{'sdt':f'090200{i}99'} for i in range(10, 17)], NguoiDung.objects.get(email='nv@demo.vn'))""")
    code, r = api("/api/start", {})
    kiem(code == 200, "Bắt đầu đợt gửi lỗi")
    kiem(cho_xong(), "Đợt gửi dừng")
    n = int(shell("from guitin.models import *; print(LichSuGui.objects.filter(sdt__endswith='99').count())"))
    kiem(n == 3, f"Dừng sau đúng 3 lỗi liên tiếp (đã xử lý {n} số)")
    code, r = api("/api/start", {})
    kiem(code == 400 and "tạm dừng" in r.get("error", ""), f"Không cho gửi khi đang tạm dừng: {r.get('error', '')[:60]}")
    kiem(api("/api/state")[1]["hanMuc"]["tamDungDen"] is not None, "Trang nhân viên thấy tạm dừng")

    print("C. Hạn mức mỗi ngày")
    shell("""
from guitin.models import *; from guitin import services as sv
NguoiDung.objects.filter(email='nv@demo.vn').update(tam_dung_den=None)
NhanNhan = NguoiNhan.objects.filter(sdt__endswith='99').delete()
n=NguoiDung.objects.get(email='nv@demo.vn')
sv.nhap_them([{'sdt':f'090300000{i}'} for i in range(5)], n)
c=CaiDat.lay(); c.gioi_han_moi_ngay=sv.da_gui_hom_nay(n)+2; c.save()""")
    code, r = api("/api/start", {})
    kiem(code == 200, f"Bắt đầu gửi khi còn hạn mức ({r})")
    kiem(cho_xong(), "Đợt gửi xong")
    n = int(shell("from guitin.models import *; print(LichSuGui.objects.filter(sdt__startswith='09030').count())"))
    kiem(n == 2, f"Chỉ gửi đúng phần còn lại của hạn mức (2), thực tế {n}")
    code, r = api("/api/start", {})
    kiem(code == 400 and "đủ" in r.get("error", ""), f"Hết hạn mức thì không cho gửi: {r.get('error', '')[:60]}")
    shell("from guitin.models import *; c=CaiDat.lay(); c.gioi_han_moi_ngay=0; c.save()")

    print("D. Ngoài khung giờ -> tạm nghỉ, không gửi; Dừng được khi đang nghỉ")
    # Khung giờ 1 phút lúc 3:00 sáng (đồng hồ worker chạy nhanh nên không dùng giờ thật để canh)
    shell("""
from guitin.models import *; import datetime
c=CaiDat.lay(); c.gio_bat_dau=datetime.time(3,0); c.gio_ket_thuc=datetime.time(3,1); c.save()""")
    code, r = api("/api/start", {})
    kiem(code == 200, "Bắt đầu gửi ngoài giờ")
    time.sleep(3)
    p = api("/api/poll")[1]
    kiem(p.get("running") and p.get("waitBreak"), "Đang tạm nghỉ ngoài giờ")
    logs = shell("from guitin.models import *; print(NhatKy.objects.filter(noi_dung__contains='Ngoài khung giờ').count())")
    kiem(logs == "1", f"Ghi nhật ký 'Ngoài khung giờ' một lần ({logs})")
    n = int(shell("from guitin.models import *; print(LichSuGui.objects.filter(sdt__startswith='09030').count())"))
    kiem(n == 2, f"Ngoài giờ không gửi thêm ai ({n - 2})")
    api("/api/stop", {})
    kiem(cho_xong(30), "Bấm Dừng khi đang nghỉ ngoài giờ thì dừng ngay")

    print("E. Dừng giữa chừng")
    shell("""
from guitin.models import *; from guitin import services as sv; import datetime
c=CaiDat.lay(); c.gio_bat_dau=datetime.time(0,0); c.gio_ket_thuc=datetime.time(23,59); c.save()
sv.nhap_them([{'sdt':f'090400000{i}'} for i in range(6)], NguoiDung.objects.get(email='nv@demo.vn'))""")
    api("/api/start", {})
    time.sleep(1.5)
    api("/api/stop", {})
    kiem(cho_xong(), "Bấm Dừng thì dừng")
    # Còn 3 số 09030… của phần D và 6 số 09040…: dừng sớm thì chưa xử lý hết 9 số
    n = int(shell("from guitin.models import *; print(LichSuGui.objects.filter(sdt__startswith='0903').count()"
                  " + LichSuGui.objects.filter(sdt__startswith='0904').count() - 2)"))
    kiem(0 < n < 9, f"Dừng trước khi hết danh sách ({n}/9)")

    print("F. Gửi nhóm Zalo")
    code, r = api("/api/nhom/danh-sach")
    kiem(code == 200 and [g["ten"] for g in r.get("groups", [])] == ["Nhóm BĐS Q7", "Nhóm đầu tư"], f"Lấy danh sách nhóm ({r})")
    code, r = api("/api/nhom/cau-hinh", {"noiDung": "Mở bán {Palm City|dự án mới}", "nhom": [{"id": "111", "ten": "Nhóm BĐS Q7"},
                                                                                          {"id": "222", "ten": "Nhóm đầu tư"}]})
    kiem(code == 200, "Lưu cấu hình nhóm")
    code, r = api("/api/nhom/gui", {})
    kiem(code == 200, f"Bắt đầu gửi nhóm ({r})")
    kiem(cho_xong(), "Gửi nhóm xong")
    out = shell("from guitin.models import *; print(list(LichSuGuiNhom.objects.order_by('ten_nhom').values_list('ten_nhom','ket_qua','noi_dung')))")
    kiem("'Nhóm BĐS Q7', 'Đã gửi'" in out and "'Nhóm đầu tư', 'Đã gửi'" in out and "{" not in out, f"Lịch sử gửi nhóm: {out}")

    print("G. Nội dung riêng cho từng nhóm")
    api("/api/nhom/cau-hinh", {"noiDung": "Chung cho mọi nhóm", "nhom": [{"id": "111", "ten": "Nhóm BĐS Q7", "noiDung": "Riêng Q7"},
                                                                       {"id": "222", "ten": "Nhóm đầu tư"}]})
    api("/api/nhom/gui", {})
    kiem(cho_xong(), "Gửi nhóm xong")
    g = [x for x in da_gui() if x["loai"] == "tin-nhan"][-2:]
    kiem({(x["uid"], x["msg"]) for x in g} == {("111", "Riêng Q7"), ("222", "Chung cho mọi nhóm")}, f"Mỗi nhóm đúng nội dung: {g}")

    print("H. Khách chặn tin người lạ -> đồng ý kết bạn -> tool tự gửi tin")
    shell("""
from guitin import services as sv; from guitin.models import NguoiDung
sv.nhap_them([{'sdt':'0905000077','ten':'Chị Chặn'}], NguoiDung.objects.get(email='nv@demo.vn'))""")
    api("/api/start", {})
    kiem(cho_xong(), "Gửi xong")
    kq = shell("from guitin.models import *; print(KetQua.objects.get(sdt='0905000077').tin_nhan)")
    kiem(kq == "Lỗi", f"Tin đầu không tới vì khách chặn người lạ ({kq})")
    su_kien("dong_y", "u0905000077")
    time.sleep(6)
    out = json.loads(shell("""
from guitin.models import *; import json
k=KhachHang.objects.get(sdt='0905000077')
print(json.dumps({'dongY': bool(k.dong_y_luc), 'tt': k.trang_thai, 'kq': KetQua.objects.get(sdt='0905000077').tin_nhan,
 'ls': list(LichSuGui.objects.filter(sdt='0905000077').values_list('loai','tin_nhan'))}))"""))
    kiem(out["dongY"] and out["tt"] == "da_ket_ban", f"Ghi nhận khách đồng ý kết bạn ({out})")
    kiem(["khi_dong_y", "Đã gửi"] in out["ls"] and out["kq"] == "Đã gửi", f"Tự gửi tin sau khi đồng ý, tin đã tới ({out['ls']})")
    tin = [x for x in da_gui() if x["loai"] == "tin-nhan" and x["uid"] == "u0905000077"]
    kiem(len(tin) == 1, f"Khách nhận đúng 1 tin ({len(tin)})")

    print("I. Khách nhắn lại")
    su_kien("nhan_tin", "u0901000001")
    su_kien("nhan_tin", "u0901000001")
    time.sleep(3)
    out = shell("from guitin.models import *; k=KhachHang.objects.get(sdt='0901000001'); print(k.trang_thai, k.so_lan_tra_loi)")
    kiem(out == "da_tra_loi 1", f"Ghi nhận khách trả lời, không đếm trùng khi nhắn liền nhiều tin ({out})")

    print("J. Nhắc lại tự động khách chưa trả lời (lệnh chay_lich)")
    shell("""
from guitin.models import *; from django.utils import timezone; from datetime import timedelta
c=CaiDat.lay(); c.nhac_lai_sau_ngay=2; c.noi_dung_nhac_lai='{ten} ơi, em nhắc lại thông tin ạ'; c.gio_bat_dau='00:00'; c.gio_ket_thuc='23:59'; c.save()
xa = timezone.now()-timedelta(days=3)
KhachHang.objects.filter(sdt__in=['0901000002','0901000003']).update(gui_dau_luc=xa)
LichSuGui.objects.filter(sdt__in=['0901000002','0901000003']).update(thoi_gian=xa)""")
    r = subprocess.run([PY, "manage.py", "chay_lich"], cwd=PROJ / "web", env=env, capture_output=True, text=True, encoding="utf-8")
    kiem("Nhắc lại 2 khách" in r.stdout, f"chay_lich bắt đầu nhắc lại: {r.stdout.strip()} {r.stderr.strip()[-200:]}")
    kiem(cho_xong(), "Đợt nhắc lại xong")
    out = shell("from guitin.models import *; print(sorted(LichSuGui.objects.filter(loai='nhac_lai').values_list('sdt','tin_nhan')))")
    kiem(out == "[('0901000002', 'Đã gửi'), ('0901000003', 'Đã gửi')]", f"Đã gửi tin nhắc lại ({out})")
    nhac = [x["msg"] for x in da_gui() if x["loai"] == "tin-nhan" and x["uid"] == "u0901000002"]
    kiem(nhac[-1] == "Chị Lan ơi, em nhắc lại thông tin ạ", f"Nội dung nhắc lại đúng ({nhac[-1]})")
    kiem(not [x for x in da_gui() if x["loai"] == "ket-ban" and x["uid"] == "u0901000002"][1:], "Nhắc lại không gửi lại lời mời kết bạn")
    r = subprocess.run([PY, "manage.py", "chay_lich"], cwd=PROJ / "web", env=env, capture_output=True, text=True, encoding="utf-8")
    kiem("Nhắc lại" not in r.stdout, "Không nhắc lại lần 2")

    print("K. Worker khởi động lại giữa đợt -> đợt bị gián đoạn -> tiếp tục")
    shell("""
from guitin.models import *; from guitin import services as sv
c=CaiDat.lay(); c.nhac_lai_sau_ngay=0; c.save()
sv.nhap_them([{'sdt':f'09060000{i:02d}'} for i in range(8)], NguoiDung.objects.get(email='nv@demo.vn'))""")
    code, r = api("/api/start", {})
    dot_id = r.get("dot", {}).get("id")
    time.sleep(1.2)
    wk.kill()
    wk.wait()
    time.sleep(1)
    wk = chay_worker()
    time.sleep(5)
    shell(f"from guitin.models import *; from django.utils import timezone; from datetime import timedelta; "
          f"DotGui.objects.filter(id={dot_id}).update(bat_dau=timezone.now()-timedelta(minutes=2))")
    r = subprocess.run([PY, "manage.py", "chay_lich"], cwd=PROJ / "web", env=env, capture_output=True, text=True, encoding="utf-8")
    time.sleep(1)
    s = api("/api/state")[1]
    d = s.get("dot") or {}
    kiem(d.get("trangThai") == "gian_doan" and 0 < (d.get("conLai") or 0) < 8, f"Đợt bị gián đoạn, còn lại số chưa gửi ({d})")
    code, r = api("/api/dot/tiep-tuc", {"dot": dot_id})
    kiem(code == 200, f"Bấm tiếp tục ({r})")
    kiem(cho_xong(), "Đợt tiếp tục chạy xong")
    n = shell("from guitin.models import *; print(LichSuGui.objects.filter(sdt__startswith='090600').values('sdt').distinct().count(), LichSuGui.objects.filter(sdt__startswith='090600').count())")
    kiem(n == "8 8", f"Đủ 8 số, không số nào gửi 2 lần ({n})")

    print("M. Tin tự gửi đang chờ (ngoài giờ) không mất khi worker khởi động lại")
    shell("""
from guitin.models import *; from guitin import services as sv; import datetime
NguoiDung.objects.filter(email='nv@demo.vn').update(tam_dung_den=None)
c=CaiDat.lay(); c.gio_bat_dau=datetime.time(0,0); c.gio_ket_thuc=datetime.time(23,59); c.save()
sv.nhap_them([{'sdt':'0907200077','ten':'Chị Chờ'}], NguoiDung.objects.get(email='nv@demo.vn'))""")
    api("/api/start", {})
    kiem(cho_xong(), "Gửi tin đầu (khách chặn người lạ)")
    shell("""
from guitin.models import *; import datetime
c=CaiDat.lay(); c.gio_bat_dau=datetime.time(3,0); c.gio_ket_thuc=datetime.time(3,1); c.save()""")
    su_kien("dong_y", "u0907200077")
    time.sleep(5)
    f = WK / "data" / "cho-tu-gui.json"
    kiem(f.exists() and "0907200077" in f.read_text(encoding="utf-8"), "Tin tự gửi ngoài giờ được lưu ra file")
    wk.kill()
    wk.wait()
    time.sleep(1)
    wk = chay_worker()
    time.sleep(6)
    s = api("/api/state")[1]
    kiem(s.get("choTuGui") == 1, f"Khởi động lại worker: tin tự gửi vẫn chờ ({s.get('choTuGui')})")
    tin = [x for x in da_gui() if x["loai"] == "tin-nhan" and x["uid"] == "u0907200077"]
    kiem(len(tin) == 0, f"Ngoài giờ chưa gửi tin nào tới khách ({len(tin)})")
    shell("""
from guitin.models import *; import datetime
c=CaiDat.lay(); c.gio_bat_dau=datetime.time(0,0); c.gio_ket_thuc=datetime.time(23,59); c.save()""")

    print("L. Phiên Zalo hết hiệu lực giữa đợt -> dừng ngay, khách chưa bị tính đã gửi, không tạm dừng, cần quét QR lại")
    shell("""
from guitin.models import *; from guitin import services as sv
NguoiDung.objects.filter(email='nv@demo.vn').update(tam_dung_den=None)
sv.nhap_them([{'sdt':'0907000001'},{'sdt':'0907000066'},{'sdt':'0907000002'}], NguoiDung.objects.get(email='nv@demo.vn'))""")
    code, r = api("/api/start", {})
    kiem(code == 200, f"Bắt đầu gửi ({r.get('error', '')})")
    kiem(cho_xong(), "Đợt dừng")
    n = shell("from guitin.models import *; print(sorted(LichSuGui.objects.filter(sdt__startswith='090700').values_list('sdt', flat=True)))")
    kiem(n == "['0907000001']", f"Chỉ người trước đó có kết quả, khách gặp lỗi phiên chưa bị tính ({n})")
    td = shell("from guitin.models import *; print(NguoiDung.objects.get(email='nv@demo.vn').tam_dung_den)")
    kiem(td == "None", f"Không tạm dừng 24 giờ ({td})")
    s = api("/api/state")[1]
    kiem(s.get("zalo", s).get("login", s.get("login")) == "out" or '"login": "out"' in json.dumps(s), "Trang báo Zalo chưa kết nối (cần quét QR lại)")
finally:
    wk.terminate()
    dj.terminate()
    time.sleep(1)
    shutil.rmtree(WK, ignore_errors=True)

print("\nKẾT QUẢ:", "TẤT CẢ ĐỀU QUA" if not loi else f"{len(loi)} LỖI: {loi}")
sys.exit(1 if loi else 0)
