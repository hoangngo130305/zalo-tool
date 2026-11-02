#!/bin/sh
# Sao lưu hằng đêm (cron): database MySQL + thư mục data/ (ảnh, phiên Zalo, hàng đợi kết quả).
# Giữ 14 bản gần nhất trong ~/sao-luu (hoặc config.json -> saoLuu.thuMuc). Trang “Tình trạng hệ thống” đọc thư mục này.
# Cài: crontab -e  ->  0 2 * * * /home/demo/apps/zalo-tool/deploy/sao-luu.sh >> /home/demo/sao-luu/sao-luu.log 2>&1
set -eu
GOC="$(cd "$(dirname "$0")/.." && pwd)"
cd "$GOC"
THU_MUC=$(python3 -c "import json,os;c=json.load(open('config.json'));print(os.path.expanduser(c.get('saoLuu',{}).get('thuMuc') or '~/sao-luu'))")
mkdir -p "$THU_MUC"
chmod 700 "$THU_MUC"
LUC=$(date +%Y%m%d-%H%M)
# Đọc thông tin MySQL từ config.json, mật khẩu đưa qua file tạm (không lộ trên dòng lệnh)
CNF=$(mktemp)
trap 'rm -f "$CNF"' EXIT
python3 - "$CNF" <<'EOF'
import json, sys
m = json.load(open("config.json"))["mysql"]
open(sys.argv[1], "w").write(
    f"[client]\nuser={m.get('user','root')}\npassword={m.get('password','')}\nhost={m.get('host','127.0.0.1')}\nport={m.get('port',3306)}\n")
EOF
DB=$(python3 -c "import json;print(json.load(open('config.json'))['mysql']['database'])")
mysqldump --defaults-extra-file="$CNF" --single-transaction --no-tablespaces "$DB" | gzip > "$THU_MUC/db-$LUC.sql.gz"
tar -czf "$THU_MUC/data-$LUC.tar.gz" --exclude="data/tam-nhap" data
chmod 600 "$THU_MUC"/*.gz
# Giữ 14 bản gần nhất mỗi loại
ls -1t "$THU_MUC"/db-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
ls -1t "$THU_MUC"/data-*.tar.gz 2>/dev/null | tail -n +15 | xargs -r rm -f
echo "$(date '+%F %T') Đã sao lưu: db-$LUC.sql.gz, data-$LUC.tar.gz"
