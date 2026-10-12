# Zalo Tool – tự động kết bạn & gửi tin nhắn

Gồm 2 phần chạy cùng nhau trên một máy:

| Phần | Làm gì |
|---|---|
| **Django** (`web/`, cổng 8000) | Trang gửi tin cho nhân viên, trang quản trị `/admin/`, đăng nhập, phân quyền, lưu MySQL |
| **Zalo worker** (`src/worker.js`, cổng 3100, chỉ nội bộ) | Đăng nhập Zalo bằng QR, tìm SĐT, gửi lời mời kết bạn và tin nhắn |

## Cài đặt (1 lần)
1. Cài **Node.js**, **Python 3.12+** và **XAMPP**.
2. Mở **XAMPP Control Panel**, bấm **Start** ở dòng **MySQL**.
3. Double-click **`cai-dat.bat`**. Tool tự cài thư viện, tạo database `zalo_tool`, nhập dữ liệu từ bản cũ,
   và hỏi tạo tài khoản quản trị nếu chưa có.

Kết nối MySQL nằm trong `config.json` (mặc định XAMPP: user `root`, không mật khẩu). Các mục
`djangoSecretKey` và `worker.token` được tự tạo, không cần sửa và không chia sẻ cho ai.

## Sử dụng
Bật MySQL trong XAMPP, rồi double-click **`chay-web.bat`**. Trình duyệt tự mở `http://localhost:8000`.
Giữ cửa sổ “Zalo Tool” mở trong lúc dùng (cửa sổ “Zalo worker” được thu nhỏ bên dưới).
Mọi người đăng nhập cùng một địa chỉ, tool tự đưa về đúng giao diện theo vai trò:

| Vai trò | Giao diện | Làm gì |
|---|---|---|
| **Quản trị** | Trang quản trị Django `/admin/` | Toàn quyền: Zalo, gửi tin, nhập & giao số, nội dung tin, tài khoản, kết quả, nhật ký |
| **Nhân viên** | Trang gửi tin riêng `/` | Chỉ thấy số được giao cho mình, thêm số, bấm gửi, theo dõi tiến độ, tải báo cáo của mình |
| **Chỉ xem** | Trang quản trị `/admin/` (chỉ xem) | Xem bảng điều khiển, danh sách, kết quả, nhật ký; không sửa được gì |

### Quản trị: trang chủ /admin/ là bảng điều khiển
- **Zalo của tôi**: Zalo của chính quản trị (dùng khi quản trị tự gửi).
- **Gửi tin bằng Zalo của tôi**: chọn gửi cho tất cả / số chưa giao / số của một nhân viên, theo dõi tiến độ, dừng.
- **Theo nhân viên**: Zalo của từng người đã kết nối chưa, đang gửi tới đâu, được giao bao nhiêu, đã gửi, còn chờ, lỗi.
  Có nút **Dừng** / **Ngắt Zalo** hộ nhân viên. Bấm vào dòng để xem danh sách.
- **Nhật ký** cập nhật trực tiếp, nút **Tải báo cáo Excel**.

### Quy trình giao việc
1. **/admin/ → Tài khoản → Thêm**: tạo tài khoản cho từng nhân viên (email, họ tên, vai trò Nhân viên, mật khẩu).
2. **/admin/ → Người nhận → Nhập danh sách**: chọn file Excel/CSV hoặc dán danh sách, chọn luôn nhân viên
   phụ trách (hoặc để “chưa giao”).
3. Giao số chưa giao: trong **Người nhận**, chọn các dòng → thao tác **“Giao các số đã chọn cho …”** hoặc
   **“Chia đều TẤT CẢ số chưa giao cho nhân viên”**. Cũng sửa được trực tiếp ở cột *Nhân viên phụ trách*.
4. Sửa nội dung lời mời / tin nhắn: **/admin/ → Cài đặt gửi tin**.
5. Nhân viên đăng nhập, thấy danh sách của mình và bấm **Bắt đầu gửi**.

Mỗi nhân viên **gửi bằng Zalo của chính mình** (bắt buộc kết nối trước khi gửi), nhiều người gửi song song được.
Mỗi số chỉ được gửi một lần, ai gửi trước thì những người khác không gửi trùng. Lịch sử ghi rõ tin được gửi
từ tài khoản Zalo nào. Nhân viên chỉ dừng được đợt gửi của mình, quản trị dừng được mọi đợt.

### Nhân viên gửi tin
1. Đăng nhập, vào tab **Gửi tin**. Lần đầu: ở khung **Zalo của tôi** bấm **Kết nối Zalo của tôi** và quét mã QR
   bằng app Zalo trên điện thoại của mình (phiên được lưu, lần sau không cần quét).
2. Nhập danh sách: gõ vào bảng, **Dán danh sách**, hoặc **Nhập file Excel** (bấm **Tải file mẫu**).
   File gồm 3 cột: **SĐT**, **Họ tên**, **Nội dung**.
3. Soạn **Tin nhắn gửi hàng loạt** của mình (ô bên phải, tự lưu). Dùng `{ten}` để chèn họ tên.
4. Bấm **Bắt đầu gửi** → **Xác nhận**: tool tự gửi lời mời kết bạn rồi gửi tin nhắn cho từng người.
5. Tab **Lịch sử gửi**: tìm, lọc theo kết quả / ngày, **Xuất Excel** (theo bộ lọc, hoặc chỉ người đã gửi thành công).

Nội dung tin nhắn mỗi người được chọn theo thứ tự: **nội dung riêng của dòng** → **tin nhắn gửi hàng loạt
của nhân viên** → **nội dung mặc định của quản trị** (/admin/ → Cài đặt gửi tin). Lời mời kết bạn do quản trị soạn.

### Tài khoản
- Sai mật khẩu 5 lần sẽ bị khoá đăng nhập 5 phút.
- Khoá tài khoản: /admin/ → Tài khoản → bỏ chọn “Hoạt động”. Xoá tài khoản thì các số người đó
  phụ trách chuyển về “chưa giao”.
- Ai cũng tự đổi được mật khẩu (nhân viên: nút “Đổi mật khẩu” góc trên; quản trị: menu tài khoản trong /admin/).
- Quên mật khẩu quản trị:
  ```
  .venv\Scripts\python web\manage.py tao_admin --email <email> --password <mật khẩu mới>
  ```
- Muốn nhân viên dùng từ máy khác cùng mạng: đặt `"moChoMangNoiBo": true` trong `config.json`,
  chạy lại `chay-web.bat`, gửi địa chỉ `http://<IP máy>:8000` hiện trong cửa sổ cho nhân viên.
  Khi đó nên đặt mật khẩu cho MySQL.

## Dữ liệu
Lưu trong database MySQL `zalo_tool`, xem bằng /admin/ hoặc phpMyAdmin. Các bảng của tool có tiền tố
`guitin_` (người nhận, kết quả, tài khoản, cài đặt, nhật ký).

- **Sao lưu**: phpMyAdmin → `zalo_tool` → Export. Phiên Zalo của từng người nằm ở `data/zalo-sessions/<id>.json`
  (quyền truy cập tài khoản Zalo, không chia sẻ).
- Các bảng không có tiền tố (`nguoi_nhan`, `ket_qua`, `cai_dat`...) là của bản Node cũ, dữ liệu đã được
  nhập sang. Kiểm tra đủ rồi có thể xoá bằng:
  ```
  .venv\Scripts\python web\manage.py nhap_du_lieu_cu --xoa-bang-cu
  ```
- Bản Node web cũ được cất trong `_ban-node-cu/`, `data/da-chuyen-sang-mysql/` là file của bản đầu tiên.
  Không còn dùng, có thể xoá khi đã yên tâm.

## Lưu ý quan trọng
- Tool dùng thư viện **không chính thức** `zca-js` (giả lập Zalo Web). Zalo giới hạn số lời mời
  kết bạn/ngày và có thể **khoá tính năng hoặc khoá tài khoản** nếu gửi nhiều cho người lạ.
  Nên dùng tài khoản phụ, giữ khoảng **20–30 người/ngày**, không giảm thời gian chờ. Tool luôn giới hạn
  tối đa 50 người mỗi lần và chờ ít nhất 30 giây giữa 2 người.
- Người nhận đặt "chặn tin nhắn từ người lạ" sẽ không nhận được tin cho tới khi đồng ý kết bạn.
- Đang chạy thì **không đăng nhập Zalo Web ở trình duyệt khác** (sẽ bị đẩy phiên).
