// Phần gửi Zalo dùng cho worker.js (Django gọi worker để gửi). Không đụng database.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Zalo, ThreadType, LoginQRCallbackEventType } from "zca-js";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Mỗi tài khoản Zalo Tool có một phiên Zalo riêng: data/zalo-sessions/<id>.json
const SESSION_DIR = path.join(ROOT, "data", "zalo-sessions");
const sessionFile = (uid) => path.join(SESSION_DIR, `${Number(uid)}.json`);

// ---------- tiện ích ----------
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Giới hạn cứng để tránh bị Zalo khoá tài khoản (Django cũng kiểm tra, đây là lớp chặn cuối)
export function sanitizeConfig(c = {}) {
  const num = (v, d) => (Number.isFinite(Number(v)) ? Number(v) : d);
  const min = Math.max(30, num(c.choToiThieuGiay, 60));
  return {
    gioiHanMoiLan: Math.min(50, Math.max(1, Math.round(num(c.gioiHanMoiLan, 20)))),
    choToiThieuGiay: min,
    choToiDaGiay: Math.max(min, num(c.choToiDaGiay, 150)),
    guiKetBan: c.guiKetBan !== false,
    guiTinNhan: c.guiTinNhan !== false,
    loiMoiKetBan: String(c.loiMoiKetBan ?? ""),
    noiDungTinNhan: String(c.noiDungTinNhan ?? ""),
  };
}

export const randomDelay = (cfg) => {
  const min = cfg.choToiThieuGiay * 1000;
  const max = cfg.choToiDaGiay * 1000;
  return Math.floor(min + Math.random() * (max - min));
};

export const fill = (tpl, row) => tpl.replace(/\{ten\}/gi, row.ten || "bạn").trim();

// ---------- phiên đăng nhập Zalo (mỗi tài khoản một file) ----------
export const newZalo = () => new Zalo({ selfListen: false, logging: false });
export const hasZaloSession = (uid) => fs.existsSync(sessionFile(uid));
export const clearZaloSession = (uid) => { if (hasZaloSession(uid)) fs.unlinkSync(sessionFile(uid)); };
function saveZaloSession(uid, creds) {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  fs.writeFileSync(sessionFile(uid), JSON.stringify(creds));
}
// Id các tài khoản đã có phiên Zalo lưu sẵn
export function savedSessionIds() {
  if (!fs.existsSync(SESSION_DIR)) return [];
  return fs.readdirSync(SESSION_DIR).map((f) => /^(\d+)\.json$/.exec(f)?.[1]).filter(Boolean).map(Number);
}

// Trả về api, hoặc null nếu không có phiên / phiên hết hạn
export async function loginWithSession(zalo, uid) {
  if (!hasZaloSession(uid)) return null;
  try {
    return await zalo.login(JSON.parse(fs.readFileSync(sessionFile(uid), "utf8")));
  } catch {
    return null;
  }
}

// onEvent nhận: { type: "qr", image, actions } | { type: "expired" } | { type: "scanned", name } | { type: "declined" }
export async function loginWithQR(zalo, uid, onEvent) {
  let creds = null;
  const api = await zalo.loginQR({ qrPath: path.join(ROOT, `qr-${Number(uid)}.png`) }, async (ev) => {
    switch (ev.type) {
      case LoginQRCallbackEventType.QRCodeGenerated:
        await onEvent({ type: "qr", image: ev.data.image, actions: ev.actions });
        break;
      case LoginQRCallbackEventType.QRCodeExpired:
        onEvent({ type: "expired" });
        ev.actions.retry();
        break;
      case LoginQRCallbackEventType.QRCodeScanned:
        onEvent({ type: "scanned", name: ev.data.display_name });
        break;
      case LoginQRCallbackEventType.QRCodeDeclined:
        onEvent({ type: "declined" });
        ev.actions.abort();
        break;
      case LoginQRCallbackEventType.GotLoginInfo:
        creds = { cookie: ev.data.cookie, imei: ev.data.imei, userAgent: ev.data.userAgent };
        break;
    }
  });
  if (creds) saveZaloSession(uid, creds);
  return api;
}

// ---------- xử lý 1 người ----------
export async function processOne(api, row, cfg) {
  const result = { ten: row.ten, ketBan: "-", tinNhan: "-", thoiGian: new Date().toISOString() };

  let user;
  try {
    user = await api.findUser(row.sdt);
  } catch (e) {
    return { ...result, ketBan: "Lỗi", tinNhan: "Lỗi", ghiChu: `Không tìm thấy Zalo: ${e.message}` };
  }
  if (!user?.uid) return { ...result, ketBan: "Lỗi", tinNhan: "Lỗi", ghiChu: "SĐT chưa đăng ký Zalo hoặc chặn tìm kiếm" };
  result.tenZalo = user.display_name || user.zalo_name;

  if (cfg.guiKetBan) {
    try {
      await api.sendFriendRequest(fill(cfg.loiMoiKetBan, row), user.uid);
      result.ketBan = "Đã gửi";
    } catch (e) {
      // Đã là bạn / đã gửi lời mời trước đó vẫn tính là ok để tiếp tục nhắn
      result.ketBan = "Bỏ qua";
      result.ghiChu = `Kết bạn: ${e.message}`;
    }
    await sleep(3000 + Math.random() * 4000);
  }

  if (cfg.guiTinNhan) {
    // Nội dung riêng của từng người (Django đã điền sẵn), không có thì dùng nội dung mặc định
    const noiDung = row.noiDung ? String(row.noiDung) : fill(cfg.noiDungTinNhan, row);
    result.noiDung = noiDung;
    try {
      await api.sendMessage(noiDung, user.uid, ThreadType.User);
      result.tinNhan = "Đã gửi";
    } catch (e) {
      result.tinNhan = "Lỗi";
      result.ghiChu = [result.ghiChu, `Tin nhắn: ${e.message}`].filter(Boolean).join(" | ");
    }
  }
  return result;
}

// ---------- chạy 1 đợt ----------
// wait(ms) cho phép bên gọi tự quyết cách chờ (để bấm Dừng giữa chừng)
export async function runBatch({ api, cfg, batch, onStart, onResult, onWait, shouldStop = () => false, wait = sleep }) {
  for (let i = 0; i < batch.length; i++) {
    if (shouldStop()) return { stopped: true, sent: i };
    const row = batch[i];
    onStart?.(row, i, batch.length);
    const res = await processOne(api, row, cfg);
    await onResult?.(row, res, i, batch.length);

    if (i < batch.length - 1) {
      const ms = randomDelay(cfg);
      onWait?.(ms);
      await wait(ms);
    }
  }
  return { stopped: false, sent: batch.length };
}
