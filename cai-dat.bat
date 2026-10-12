@echo off
chcp 65001 >nul
cd /d %~dp0
set PYTHONUTF8=1
echo Truoc khi cai: mo XAMPP Control Panel va bam Start o dong MySQL.
echo.
echo [1/5] Cai thu vien Node (Zalo worker)...
call npm install || goto loi
echo [2/5] Tao moi truong Python va cai Django...
if not exist .venv\Scripts\python.exe python -m venv .venv || goto loi
.venv\Scripts\python -m pip install -q -r requirements.txt || goto loi
echo [3/5] Tao bang trong MySQL...
.venv\Scripts\python web\manage.py migrate --noinput || goto loi
.venv\Scripts\python web\manage.py collectstatic --noinput >nul || goto loi
echo [4/5] Nhap du lieu tu ban cu (neu co)...
.venv\Scripts\python web\manage.py nhap_du_lieu_cu || goto loi
echo [5/5] Tai khoan quan tri...
.venv\Scripts\python web\manage.py shell -c "import sys; from guitin.models import NguoiDung as U; sys.exit(0 if U.objects.filter(vai_tro='admin').exists() else 1)"
if errorlevel 1 .venv\Scripts\python web\manage.py createsuperuser
echo.
echo Cai dat xong. Chay chay-web.bat de dung.
pause
exit /b
:loi
echo.
echo Co loi o buoc tren. Kiem tra MySQL trong XAMPP da bat chua, roi chay lai cai-dat.bat.
pause
