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
    // Cứ gửi nghiSauSoLan lần thì nghỉ nghiPhut phút (0 = không nghỉ)
    nghiSauSoLan: Math.min(1000, Math.max(0, Math.round(num(c.nghiSauSoLan, 0)))),
    nghiPhut: Math.min(240, Math.max(1, num(c.nghiPhut, 10))),
    doiTenGoiNho: c.doiTenGoiNho !== false,
    mauTenGoiNho: String(c.mauTenGoiNho ?? "{ten} {sdt}"),
    anh: Array.isArray(c.anh) ? c.anh.slice(0, 10).map(String) : [],   // ảnh gửi kèm tin nhắn cho từng khách
  };
}

export const randomDelay = (cfg) => {
  const min = cfg.choToiThieuGiay * 1000;
  const max = cfg.choToiDaGiay * 1000;
  return Math.floor(min + Math.random() * (max - min));
};

export const fill = (tpl, row) => tpl.replace(/\{ten\}/gi, row.ten || "bạn").trim();

// Xáo nội dung: nhiều mẫu cách nhau bằng một dòng "---" -> chọn ngẫu nhiên 1 mẫu;
// rồi {a|b|c} -> chọn ngẫu nhiên 1 lựa chọn. {ten} không có "|" nên không bị đụng tới.
export const cacMau = (text) => String(text ?? "").split(/^\s*-{3,}\s*$/m).map((m) => m.trim()).filter(Boolean);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
export function spin(text) {
  const mau = cacMau(text);
  let t = mau.length ? pick(mau) : "";
  for (let k = 0; k < 10; k++) {
    const t2 = t.replace(/\{([^{}]*\|[^{}]*)\}/g, (_, s) => pick(s.split("|")));
    if (t2 === t) break;
    t = t2;
  }
  return t;
}

// Tên gợi nhớ: {ten} {sdt}, không có tên thì chỉ còn SĐT
export const tenGoiNho = (tpl, row) =>
  (tpl || "{ten} {sdt}").replace(/\{ten\}/gi, row.ten || "").replace(/\{sdt\}/gi, row.sdt || "")
    .replace(/\s+/g, " ").replace(/^[\s\-–|,.:]+|[\s\-–|,.:]+$/g, "").slice(0, 60) || row.sdt;

// ---------- ảnh đính kèm ----------
// Loại ảnh và kích thước (Zalo cần width/height khi gửi ảnh từ Buffer). null nếu không phải ảnh hỗ trợ.
export function imageInfo(buf) {
  if (buf.length > 24 && buf.readUInt32BE(0) === 0x89504e47) {
    return { ext: "png", width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
  }
  if (buf.length > 4 && buf[0] === 0xff && buf[1] === 0xd8) {
    let i = 2;
    while (i + 9 < buf.length) {
      if (buf[i] !== 0xff) { i++; continue; }
      const m = buf[i + 1];
      if (m === 0xff || m === 0x01 || (m >= 0xd0 && m <= 0xd8)) { i += m === 0xff ? 1 : 2; continue; }
      // Khối SOF chứa kích thước ảnh
      if (m >= 0xc0 && m <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(m)) {
        return { ext: "jpg", height: buf.readUInt16BE(i + 5), width: buf.readUInt16BE(i + 7) };
      }
      i += 2 + buf.readUInt16BE(i + 2);
    }
    return { ext: "jpg" };
  }
  if (buf.length > 30 && buf.toString("ascii", 0, 4) === "RIFF" && buf.toString("ascii", 8, 12) === "WEBP") {
    const kind = buf.toString("ascii", 12, 16);
    if (kind === "VP8 ") return { ext: "webp", width: buf.readUInt16LE(26) & 0x3fff, height: buf.readUInt16LE(28) & 0x3fff };
    if (kind === "VP8L") {
      const b = buf.readUInt32LE(21);
      return { ext: "webp", width: (b & 0x3fff) + 1, height: ((b >>> 14) & 0x3fff) + 1 };
    }
    if (kind === "VP8X") return { ext: "webp", width: 1 + buf.readUIntLE(24, 3), height: 1 + buf.readUIntLE(27, 3) };
    return { ext: "webp" };
  }
  return null;
}

// Đọc ảnh thành dạng đính kèm của zca-js. Chỉ cho phép ảnh trong data/anh-gui-nhom/ (gửi nhóm)
// và data/anh-tin-nhan/ (gửi kèm tin nhắn cho khách).
const DATA_DIR = path.join(ROOT, "data");
const IMAGE_DIRS = ["anh-gui-nhom", "anh-tin-nhan"].map((d) => path.join(DATA_DIR, d) + path.sep);
export function loadAttachments(names = []) {
  return names.map((name, i) => {
    const file = path.resolve(DATA_DIR, String(name));
    if (!IMAGE_DIRS.some((d) => file.startsWith(d))) throw new Error(`Đường dẫn ảnh không hợp lệ: ${name}`);
    const data = fs.readFileSync(file);
    const info = imageInfo(data);
    if (!info) throw new Error(`File không phải ảnh JPG/PNG/WEBP: ${name}`);
    return {
      data,
      filename: `anh-${i + 1}.${info.ext}`,
      metadata: { totalSize: data.length, width: info.width, height: info.height },
    };
  });
}

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
// attachments: ảnh gửi kèm tin nhắn (đọc sẵn 1 lần cho cả đợt)
export async function processOne(api, row, cfg, attachments = []) {
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
      await api.sendFriendRequest(spin(fill(cfg.loiMoiKetBan, row)), user.uid);
      result.ketBan = "Đã gửi";
    } catch (e) {
      // Đã là bạn / đã gửi lời mời trước đó vẫn tính là ok để tiếp tục nhắn
      result.ketBan = "Bỏ qua";
      result.ghiChu = `Kết bạn: ${e.message}`;
    }
    await sleep(3000 + Math.random() * 4000);
  }

  // Đặt tên gợi nhớ "Tên SĐT" để dễ tìm lại người này trong Zalo
  if (cfg.doiTenGoiNho) {
    const alias = tenGoiNho(cfg.mauTenGoiNho, row);
    try {
      await api.changeFriendAlias(alias, user.uid);
      result.tenGoiNho = alias;
    } catch (e) {
      result.ghiChu = [result.ghiChu, `Đổi tên gợi nhớ: ${e.message}`].filter(Boolean).join(" | ");
    }
    await sleep(1500 + Math.random() * 2000);
  }

  if (cfg.guiTinNhan) {
    // Nội dung riêng của từng người (Django đã điền sẵn), không có thì dùng nội dung mặc định
    // Mỗi người nhận ngẫu nhiên 1 mẫu; lưu đúng mẫu đã gửi vào lịch sử
    const noiDung = spin(row.noiDung ? String(row.noiDung) : fill(cfg.noiDungTinNhan, row));
    result.noiDung = noiDung + (attachments.length ? `\n[kèm ${attachments.length} ảnh]` : "");
    try {
      await api.sendMessage(attachments.length ? { msg: noiDung, attachments } : noiDung, user.uid, ThreadType.User);
      result.tinNhan = "Đã gửi";
    } catch (e) {
      result.tinNhan = "Lỗi";
      result.ghiChu = [result.ghiChu, `Tin nhắn: ${e.message}`].filter(Boolean).join(" | ");
    }
  }
  return result;
}

// ---------- gửi vào 1 nhóm ----------
// job: { noiDung, attachments } dùng chung cho mọi nhóm trong đợt
export async function processGroup(api, group, job) {
  // Mỗi nhóm nhận ngẫu nhiên 1 mẫu nội dung
  const noiDung = spin(job.noiDung);
  const result = {
    tenNhom: group.ten, ketQua: "-", noiDung, soAnh: job.attachments.length, thoiGian: new Date().toISOString(),
  };
  try {
    const msg = job.attachments.length ? { msg: noiDung, attachments: job.attachments } : noiDung;
    await api.sendMessage(msg, group.id, ThreadType.Group);
    result.ketQua = "Đã gửi";
  } catch (e) {
    result.ketQua = "Lỗi";
    result.ghiChu = e.message;
  }
  return result;
}

// Lấy danh sách nhóm của tài khoản: [{ id, ten, soThanhVien }]
export async function listGroups(api) {
  const ids = Object.keys((await api.getAllGroups())?.gridVerMap || {});
  const out = [];
  for (let i = 0; i < ids.length; i += 50) {
    const info = (await api.getGroupInfo(ids.slice(i, i + 50)))?.gridInfoMap || {};
    for (const id of ids.slice(i, i + 50)) {
      const g = info[id];
      out.push({ id, ten: g?.name || id, soThanhVien: g?.totalMember ?? null });
    }
  }
  return out.sort((a, b) => a.ten.localeCompare(b.ten, "vi"));
}

// ---------- chạy 1 đợt ----------
// task(api, item, cfg) xử lý 1 mục (mặc định: kết bạn + nhắn tin 1 người).
// wait(ms) cho phép bên gọi tự quyết cách chờ (để bấm Dừng giữa chừng).
// Giữa 2 lần gửi chờ ngẫu nhiên choToiThieuGiay–choToiDaGiay; cứ nghiSauSoLan lần thì nghỉ nghiPhut phút.
export async function runBatch({ api, cfg, batch, task = processOne, onStart, onResult, onWait, shouldStop = () => false, wait = sleep }) {
  for (let i = 0; i < batch.length; i++) {
    if (shouldStop()) return { stopped: true, sent: i };
    const row = batch[i];
    onStart?.(row, i, batch.length);
    const res = await task(api, row, cfg);
    await onResult?.(row, res, i, batch.length);

    if (i < batch.length - 1) {
      const nghi = cfg.nghiSauSoLan > 0 && (i + 1) % cfg.nghiSauSoLan === 0;
      const ms = nghi ? Math.round(cfg.nghiPhut * 60_000) : randomDelay(cfg);
      onWait?.(ms, nghi);
      await wait(ms);
    }
  }
  return { stopped: false, sent: batch.length };
}
