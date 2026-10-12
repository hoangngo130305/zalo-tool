import path from "node:path";
import {
  ROOT, loadConfig, readList, loadDone, pendingRows, fill, newZalo,
  loginWithSession, loginWithQR, runBatch,
} from "./core.js";
import { connect } from "./db.js";

const QR_FILE = path.join(ROOT, "qr.png");
const args = new Set(process.argv.slice(2));
const dryRun = args.has("--thu");

const log = (...m) => console.log(`[${new Date().toLocaleTimeString("vi-VN")}]`, ...m);

// ---------- đăng nhập ----------
async function login() {
  const zalo = newZalo();

  if (!args.has("--dang-nhap-lai")) {
    const api = await loginWithSession(zalo);
    if (api) {
      log("Đăng nhập bằng phiên đã lưu thành công.");
      return api;
    }
    log("Chưa có phiên hoặc phiên cũ hết hạn, chuyển sang quét QR...");
  }

  const api = await loginWithQR(zalo, async (ev) => {
    if (ev.type === "qr") {
      await ev.actions.saveToFile(QR_FILE);
      log(`Mở file ${QR_FILE} và quét bằng app Zalo trên điện thoại.`);
    } else if (ev.type === "expired") log("QR hết hạn, đang tạo mã mới...");
    else if (ev.type === "scanned") log(`Đã quét: ${ev.name}. Bấm xác nhận trên điện thoại.`);
    else if (ev.type === "declined") log("Bạn đã từ chối đăng nhập.");
  }, QR_FILE);
  log("Đăng nhập QR thành công, đã lưu phiên vào database.");
  return api;
}

// ---------- main ----------
async function main() {
  await connect();
  const config = await loadConfig();
  const list = await readList();
  const done = await loadDone();

  list.filter((r) => !r.sdt).forEach((r) => log(`Dòng ${r.dong}: SĐT không hợp lệ "${r.sdtGoc}" -> bỏ qua`));

  const todo = pendingRows(list, done);
  const batch = todo.slice(0, config.gioiHanMoiLan);

  log(`Tổng ${list.length} người | đã xử lý trước đó: ${Object.keys(done).length} | còn lại: ${todo.length} | lần này chạy: ${batch.length}`);
  if (!batch.length) return log("Không còn ai cần xử lý.");

  if (dryRun) {
    log("=== CHẠY THỬ: không đăng nhập, không gửi gì ===");
    for (const r of batch) {
      console.log(`\n--- ${r.sdt} (${r.ten || "không tên"})`);
      if (config.guiKetBan) console.log("Lời mời:", fill(config.loiMoiKetBan, r));
      if (config.guiTinNhan) console.log("Tin nhắn:\n" + fill(config.noiDungTinNhan, r));
    }
    return;
  }

  const api = await login();

  await runBatch({
    api, cfg: config, batch, nguoiGui: "dong-lenh",
    onStart: (row, i, n) => log(`(${i + 1}/${n}) ${row.sdt} - ${row.ten || "không tên"}`),
    onResult: (_, res) => log(`   Kết bạn: ${res.ketBan} | Tin nhắn: ${res.tinNhan}${res.ghiChu ? " | " + res.ghiChu : ""}`),
    onWait: (ms) => log(`   Chờ ${Math.round(ms / 1000)}s...`),
  });

  log("Xong. Tải báo cáo Excel trên giao diện web.");
}

main()
  .then(() => process.exit(0))
  .catch((e) => {
    console.error("Lỗi:", e.message || e);
    process.exit(1);
  });
