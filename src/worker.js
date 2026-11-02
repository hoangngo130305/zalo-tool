// Zalo worker: dịch vụ nội bộ lo đăng nhập Zalo và gửi tin.
// Mỗi tài khoản Zalo Tool (nhân viên / quản trị) có một phiên Zalo riêng, gửi song song được.
// Chỉ mở trên 127.0.0.1, Django gọi vào kèm mã bí mật (config.json -> worker.token).
// Mỗi kết quả / nhật ký được báo ngược về Django để lưu MySQL.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {
  ROOT, sanitizeConfig, newZalo, hasZaloSession, clearZaloSession, savedSessionIds, secureSessionDir,
  loginWithSession, loginWithQR, runBatch, sleep, processOne, processGroup, listGroups, loadAttachments,
  ngoaiKhungGio, ThreadType, FriendEventType,
} from "./core.js";

const conf = JSON.parse(fs.readFileSync(path.join(ROOT, "config.json"), "utf8"));
// Cho phép đổi cổng bằng biến môi trường (để chạy thử song song với bản đang dùng)
const PORT = Number(process.env.ZALO_WORKER_PORT || conf.worker?.port) || 3100;
const TOKEN = String(conf.worker?.token || "");
const DJANGO = `http://127.0.0.1:${Number(process.env.ZALO_TOOL_PORT || conf.django?.port) || 8000}`;
if (TOKEN.length < 32) {
  console.error("Thiếu worker.token trong config.json. Chạy chay-web.bat (Django sẽ tự tạo) rồi mở lại.");
  process.exit(1);
}

// ---------- trạng thái từng tài khoản ----------
const accounts = new Map(); // id tài khoản Zalo Tool -> trạng thái
function acc(uid) {
  uid = Number(uid);
  if (!Number.isInteger(uid) || uid <= 0) throw new Error("Thiếu tài khoản");
  if (!accounts.has(uid)) {
    accounts.set(uid, {
      uid,
      api: null,
      account: null,          // tên Zalo đang đăng nhập
      login: "out",           // out | checking | qr | in
      qr: null,               // ảnh QR dạng data URL
      qrActions: null,
      running: false,
      kind: null,             // "ca-nhan" (kết bạn + nhắn từng người) | "nhom" (gửi vào nhóm)
      runBy: null,            // { id, name } người bấm gửi
      stopRequested: false,
      current: null,          // SĐT đang gửi
      progress: null,         // { i, total }
      waitUntil: null,
      waitBreak: false,       // đang nghỉ dài sau mỗi X lần gửi
      pending: [],            // các SĐT còn lại trong đợt gửi (để không gửi trùng với người khác)
      dotId: null,            // đợt gửi (Django) đang chạy
      direct: [],             // tin tự gửi khi khách đồng ý kết bạn, chờ đến lượt
      directBusy: false,
      lastSendAt: 0,          // lần gửi gần nhất (giữ khoảng cách giữa tin tự gửi và đợt gửi)
      listening: false,       // đang nghe sự kiện Zalo (khách đồng ý kết bạn, khách nhắn tin)
    });
  }
  return accounts.get(uid);
}
const publicState = (a, withQr = true) => {
  const { api, qrActions, pending, uid, direct, directBusy, lastSendAt, ...rest } = a;
  return { ...rest, qr: withQr ? a.qr : null, choTuGui: direct.length };
};

// ---------- báo về Django ----------
// Trả về nội dung JSON Django trả lời (vd lệnh tự gửi tin), hoặc null nếu không báo được
async function reportJson(type, data, tries = 5) {
  for (let i = 1; i <= tries; i++) {
    try {
      const res = await fetch(`${DJANGO}/api/worker/event`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Worker-Token": TOKEN },
        body: JSON.stringify({ type, ...data }),
      });
      if (res.ok) return await res.json().catch(() => ({}));
      throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      if (i === tries) console.error(`Không báo được về Django (${type}): ${e.message}`);
      else await sleep(1000 * i);
    }
  }
  return null;
}
const report = async (type, data, tries = 5) => (await reportJson(type, data, tries)) !== null;

// Hàng đợi kết quả trên đĩa: kết quả gửi được ghi vào data/cho-bao-cao.jsonl TRƯỚC khi báo về Django,
// chỉ xoá khi Django xác nhận đã lưu. Django tắt / MySQL lỗi lâu thì worker cứ 30 giây gửi lại, khởi động lại
// cũng gửi tiếp, nên không mất kết quả và khách không bị nhắn 2 lần. Mỗi mục có mã riêng để Django không ghi trùng.
const QUEUE_FILE = path.join(ROOT, "data", "cho-bao-cao.jsonl");
let queue = [];
try {
  queue = fs.readFileSync(QUEUE_FILE, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
} catch (e) {
  if (e.code !== "ENOENT") console.error(`Không đọc được ${QUEUE_FILE}: ${e.message}`);
}
const saveQueue = () => {
  const tmp = `${QUEUE_FILE}.tmp`;
  fs.writeFileSync(tmp, queue.map((q) => JSON.stringify(q)).join("\n") + (queue.length ? "\n" : ""));
  fs.renameSync(tmp, QUEUE_FILE);
};
// SĐT có kết quả chưa báo được về Django: không gửi lại số này dù Django tưởng là chưa gửi
const unreported = () => new Set(queue.filter((q) => q.type === "result").map((q) => q.data.sdt));
let flushing = null;
function flushQueue(tries = 1) {
  flushing ??= (async () => {
    try {
      for (const item of [...queue]) {
        if (!(await report(item.type, { ...item.data, ma: item.ma }, tries))) break;   // Django chưa nhận: để lần sau
        queue = queue.filter((q) => q !== item);
        saveQueue();
      }
    } finally {
      flushing = null;
    }
  })();
  return flushing;
}
async function reportDurable(type, data) {
  const item = { ma: crypto.randomUUID(), type, data };
  queue.push(item);
  fs.mkdirSync(path.dirname(QUEUE_FILE), { recursive: true });
  fs.appendFileSync(QUEUE_FILE, JSON.stringify(item) + "\n");
  if (flushing) await flushing;      // lượt gửi lại đang chạy không có mục mới này
  await flushQueue(5);
  return !queue.includes(item);
}
setInterval(() => { if (queue.length) flushQueue(); }, 30_000).unref();

// Báo sự cố cho quản trị qua Telegram (không bắt buộc): config.json -> thongBao.telegram { token, chatId }
const TELEGRAM = conf.thongBao?.telegram;
async function notify(text) {
  if (!TELEGRAM?.token || !TELEGRAM?.chatId) return;
  try {
    await fetch(`https://api.telegram.org/bot${TELEGRAM.token}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: TELEGRAM.chatId, text: `Zalo Tool: ${text}` }),
    });
  } catch (e) {
    console.error(`Không gửi được thông báo Telegram: ${e.message}`);
  }
}

// Nhật ký gắn với chủ tài khoản Zalo để mỗi nhân viên chỉ thấy nhật ký của mình.
// luu = false: chỉ in ra màn hình (vd "Chờ 60 giây...", trang web đã tự hiện đồng hồ đếm ngược)
function log(a, text, level = "info", luu = true) {
  console.log(`[${new Date().toLocaleTimeString("vi-VN")}] #${a.uid} ${text}`);
  if (luu) report("log", { text, level, nguoiDungId: a.uid }, 2);
}

// ---------- đăng nhập Zalo ----------
async function finishLogin(a, api) {
  Object.assign(a, { api, login: "in", qr: null, qrActions: null });
  try {
    const info = await api.fetchAccountInfo();
    a.account = info?.profile?.displayName || info?.profile?.zaloName || "Tài khoản Zalo";
  } catch {
    a.account = "Tài khoản Zalo";
  }
  log(a, `Đã kết nối Zalo: ${a.account}`, "ok");
  startListener(a);
}

// ---------- nghe sự kiện Zalo: khách đồng ý kết bạn, khách nhắn tin đến ----------
// Chỉ báo về Django mã Zalo của khách (không gửi nội dung tin khách nhắn). Django đối chiếu với khách đã mời.
const daBaoTraLoi = new Map();    // "uid:zaloUid" -> lúc báo gần nhất (không báo liên tục khi khách nhắn nhiều tin)
function startListener(a) {
  const l = a.api?.listener;
  if (!l || a.listening) return;
  try {
    l.on("friend_event", (ev) => {
      if (ev?.type !== FriendEventType.ADD) return;
      const zaloUid = String(ev.data || ev.threadId || "");
      if (zaloUid) onKhachDongY(a, zaloUid).catch((e) => console.error(`#${a.uid} ket_ban: ${e.message}`));
    });
    l.on("message", (m) => {
      if (!m || m.isSelf || m.type !== ThreadType.User) return;
      const zaloUid = String(m.threadId || "");
      const key = `${a.uid}:${zaloUid}`;
      if (!zaloUid || Date.now() - (daBaoTraLoi.get(key) || 0) < 10 * 60_000) return;
      daBaoTraLoi.set(key, Date.now());
      report("tra_loi", { zaloUid, nguoiDungId: a.uid }, 3);
    });
    l.on("error", () => {});
    l.on("closed", () => {
      a.listening = false;
      console.log(`[${new Date().toLocaleTimeString("vi-VN")}] #${a.uid} Ngừng nhận sự kiện Zalo (đồng ý kết bạn / trả lời).`);
    });
    l.start({ retryOnClose: true });
    a.listening = true;
  } catch (e) {
    log(a, `Không bật được theo dõi khách đồng ý kết bạn / trả lời: ${e.message}`, "warn", false);
  }
}

async function onKhachDongY(a, zaloUid) {
  const res = await reportJson("ket_ban", { zaloUid, nguoiDungId: a.uid }, 3);
  const g = res?.guiTin;
  if (g?.noiDung && g?.sdt) {
    a.direct.push(g);
    log(a, `Khách ${g.sdt} vừa đồng ý kết bạn, sẽ tự gửi tin cho khách.`, "ok");
    processDirect(a);
  }
}

// Tin tự gửi khi khách đồng ý kết bạn: lần lượt từng tin, trong khung giờ gửi, cách lần gửi trước ít nhất
// "chờ tối thiểu", không chen vào lúc đợt gửi đang gửi một người.
async function processDirect(a) {
  if (a.directBusy) return;
  a.directBusy = true;
  try {
    while (a.direct.length && a.api) {
      const g = a.direct[0];
      const cfg = sanitizeConfig(g.cfg || {});
      for (let k = ngoaiKhungGio(cfg); k.ms > 0; k = ngoaiKhungGio(cfg)) await sleep(Math.min(k.ms, 60_000));
      while (a.current || Date.now() - a.lastSendAt < cfg.choToiThieuGiay * 1000) await sleep(2000);
      a.direct.shift();
      a.lastSendAt = Date.now();
      let attachments = [];
      try { attachments = loadAttachments(Array.isArray(g.anh) ? g.anh.slice(0, 10) : []); } catch {}
      const row = { sdt: String(g.sdt), ten: String(g.ten ?? ""), noiDung: String(g.noiDung).slice(0, 5000),
        zaloUid: String(g.zaloUid ?? ""), kieu: "khi_dong_y" };
      const { loiZalo, ...res } = await processOne(a.api, row, cfg, attachments);
      a.lastSendAt = Date.now();
      await reportDurable("result", { sdt: row.sdt, ketQua: { ...res, taiKhoanZalo: a.account }, nguoiGuiId: a.uid,
        kieu: "khi_dong_y", chienDichId: g.chienDichId ?? null });
      log(a, `${row.sdt}: tự gửi tin khi khách đồng ý kết bạn: ${res.tinNhan}${res.ghiChu ? ` (${res.ghiChu})` : ""}`,
        res.tinNhan === "Lỗi" ? "error" : "ok");
    }
  } finally {
    a.directBusy = false;
  }
}

// auto = true: worker tự kết nối lại lúc khởi động. Phiên hết hạn thì chỉ báo, không tự tạo mã QR
// (không ai quét, mã QR sẽ tự làm mới mãi và ghi đầy nhật ký).
async function startLogin(a, { useSession = true, auto = false } = {}) {
  if (a.login === "checking" || a.login === "qr") return;
  a.login = "checking";

  if (useSession) {
    const api = await loginWithSession(newZalo(), a.uid);
    if (api) return finishLogin(a, api);
    if (hasZaloSession(a.uid)) {
      log(a, "Phiên Zalo cũ đã hết hạn, cần quét QR lại.", "warn");
      notify(`phiên Zalo của tài khoản #${a.uid} đã hết hạn, cần quét QR lại.`);
    }
  }
  if (auto) {
    a.login = "out";
    return;
  }

  log(a, "Đang tạo mã QR, mở app Zalo trên điện thoại để quét.");
  let boQua = false;     // mã QR hết hạn quá số lần cho phép
  try {
    const api = await loginWithQR(newZalo(), a.uid, async (ev) => {
      if (ev.type === "qr") Object.assign(a, { login: "qr", qr: `data:image/png;base64,${ev.image}`, qrActions: ev.actions });
      else if (ev.type === "expired") log(a, "Mã QR hết hạn, đang tạo mã mới...", "warn");
      else if (ev.type === "gaveup") boQua = true;
      else if (ev.type === "scanned") log(a, `Đã quét bởi ${ev.name}. Bấm xác nhận trên điện thoại.`);
      else if (ev.type === "declined") log(a, "Đăng nhập bị từ chối trên điện thoại.", "warn");
    });
    if (!api) throw new Error("Đăng nhập bị huỷ");
    await finishLogin(a, api);
  } catch (e) {
    Object.assign(a, { login: "out", qr: null, qrActions: null });
    // Người dùng bấm Huỷ thì không coi là lỗi
    if (boQua) log(a, "Không ai quét mã QR nên đã dừng tạo mã. Bấm “Kết nối Zalo” để tạo mã mới.", "warn");
    else if (/abort|huỷ/i.test(e.message)) log(a, "Đã huỷ kết nối Zalo.", "warn");
    else log(a, `Không kết nối được Zalo: ${e.message}`, "error");
  }
}

function cancelLogin(a) {
  try { a.qrActions?.abort?.(); } catch {}
  Object.assign(a, { login: "out", qr: null, qrActions: null });
}

function logoutZalo(a) {
  if (a.running) throw new Error("Đang gửi, hãy bấm Dừng trước khi ngắt kết nối Zalo");
  try { a.api?.listener?.stop?.(); } catch {}
  Object.assign(a, { api: null, account: null, login: "out", listening: false, direct: [] });
  clearZaloSession(a.uid);
}

// ---------- gửi ----------
// Chạy 1 đợt cho tài khoản a: items là danh sách người / nhóm, task xử lý từng mục
function launch(a, { kind, items, cfg, runBy, task, label, keyOf, onResult, dotId = null }) {
  Object.assign(a, {
    running: true, kind, runBy: runBy || null, stopRequested: false, dotId,
    progress: { i: 0, total: items.length }, pending: kind === "ca-nhan" ? items.map((r) => r.sdt) : [],
  });
  let tuDung = false;         // tự dừng vì lỗi liên tiếp
  const nghi = cfg.nghiSauSoLan ? ` Cứ ${cfg.nghiSauSoLan} lần gửi nghỉ ${cfg.nghiPhut} phút.` : "";
  const gio = (p) => `${Math.floor(p / 60)}:${String(p % 60).padStart(2, "0")}`;
  const khung = ` Chỉ gửi ${gio(cfg.gioBatDau)}–${gio(cfg.gioKetThuc)}${cfg.nghiChuNhat ? ", nghỉ Chủ nhật" : ""}.`;
  log(a, `${runBy?.name || "Ai đó"} bắt đầu gửi cho ${items.length} ${label} bằng Zalo ${a.account}.${nghi}${khung}`, "ok");
  let loiLienTiep = 0;
  let baoNgoaiGio = null;     // chỉ ghi nhật ký 1 lần cho mỗi lần nghỉ ngoài giờ

  // Chờ theo từng nhịp ngắn để nút Dừng có hiệu lực ngay
  const wait = async (ms) => {
    const end = Date.now() + ms;
    while (Date.now() < end && !a.stopRequested) await sleep(500);
  };

  runBatch({
    api: a.api, cfg, batch: items, task,
    shouldStop: () => a.stopRequested,
    wait,
    onStart: (row, i, n) => {
      Object.assign(a, { current: keyOf(row), waitUntil: null, waitBreak: false, progress: { i, total: n } });
      log(a, `(${i + 1}/${n}) Đang gửi ${keyOf(row, true)}`);
    },
    onResult: async (row, res, i, n) => {
      Object.assign(a, { current: null, progress: { i: i + 1, total: n } });
      a.lastSendAt = Date.now();
      if (kind === "ca-nhan") a.pending = a.pending.filter((s) => s !== row.sdt);
      const { loiZalo, ...ketQua } = res;
      await onResult(row, ketQua);
      // Zalo báo lỗi nhiều lần liền (thường do bị hạn chế): tự dừng để tài khoản không bị phạt nặng hơn
      loiLienTiep = loiZalo ? loiLienTiep + 1 : 0;
      if (cfg.dungKhiLoiLienTiep && loiLienTiep >= cfg.dungKhiLoiLienTiep && !a.stopRequested && i + 1 < n) {
        a.stopRequested = true;
        tuDung = true;
        log(a, `Zalo báo lỗi ${loiLienTiep} lần liên tiếp, có thể tài khoản ${a.account} đang bị Zalo hạn chế. `
          + "Đã tự dừng đợt gửi. Mở Zalo trên điện thoại kiểm tra trước khi gửi tiếp.", "error");
        report("auto_stop", { nguoiDungId: a.uid, soLoi: loiLienTiep, dotId });
        notify(`Zalo ${a.account} (tài khoản #${a.uid}) báo lỗi ${loiLienTiep} lần liên tiếp, đã tự dừng đợt gửi.`);
      }
    },
    onWait: (ms, nghi) => {
      Object.assign(a, { waitUntil: Date.now() + ms, waitBreak: !!nghi });
      if (nghi) log(a, `Đã gửi ${cfg.nghiSauSoLan} lần, nghỉ ${Math.round(ms / 60000)} phút rồi gửi tiếp...`, "warn");
      else log(a, `Chờ ${Math.round(ms / 1000)} giây trước lần gửi tiếp theo...`, "info", false);
    },
    onOutOfHours: (k) => {
      Object.assign(a, { waitUntil: Date.now() + k.ms, waitBreak: true });
      if (baoNgoaiGio !== k.luc) log(a, `Ngoài khung giờ gửi, tạm nghỉ đến ${k.luc} rồi gửi tiếp (bấm Dừng nếu muốn huỷ).`, "warn");
      baoNgoaiGio = k.luc;
    },
  })
    .then((r) => {
      log(a, r.stopped ? `Đã dừng sau ${r.sent} ${label}.` : "Hoàn tất đợt gửi.", r.stopped ? "warn" : "ok");
      if (dotId) report("dot_ket_thuc", { dotId, trangThai: r.stopped ? (tuDung ? "tu_dung" : "da_dung") : "xong", daGui: r.sent });
    })
    .catch((e) => {
      log(a, `Lỗi khi gửi: ${e.message}`, "error");
      if (dotId) report("dot_ket_thuc", { dotId, trangThai: "gian_doan", daGui: a.progress?.i ?? 0 });
    })
    .finally(() => Object.assign(a, {
      running: false, kind: null, runBy: null, stopRequested: false, current: null, waitUntil: null, waitBreak: false,
      pending: [], dotId: null,
    }));
}

function canRun(a) {
  if (a.running) throw new Error("Bạn đang có một đợt gửi chưa xong");
  if (!a.api) throw new Error("Chưa kết nối Zalo. Quét QR để kết nối Zalo của bạn trước khi gửi");
}

// Kết bạn + nhắn tin từng người
// kieu của từng dòng: "lan_dau" (kết bạn + đổi tên + nhắn) | "nhac" (chỉ nhắn tin nhắc lại cho khách đã gửi trước đó)
const KIEU = new Set(["lan_dau", "nhac"]);
function startRun(a, { batch, cfg, runBy, dotId }) {
  canRun(a);
  cfg = sanitizeConfig(cfg);
  if (!cfg.guiKetBan && !cfg.guiTinNhan && !(batch || []).every((r) => r?.kieu === "nhac")) {
    throw new Error("Đang tắt cả kết bạn lẫn tin nhắn");
  }
  if (cfg.conLaiHomNay === 0) throw new Error("Zalo của bạn đã gửi đủ hạn mức hôm nay. Mai gửi tiếp nhé");
  // Số đang được tài khoản khác gửi, hoặc đã gửi nhưng kết quả chưa báo được về Django: bỏ qua để không ai nhận 2 lần
  const dangGui = new Set([...accounts.values()].filter((x) => x !== a && x.running).flatMap((x) => x.pending));
  const chuaBao = unreported();
  const gioiHan = [cfg.gioiHanMoiLan || Infinity, cfg.conLaiHomNay ?? Infinity];   // mỗi lần / còn lại hôm nay
  batch = (Array.isArray(batch) ? batch : [])
    .filter((r) => /^0\d{9}$/.test(String(r?.sdt)) && !dangGui.has(String(r.sdt)) && !chuaBao.has(String(r.sdt)))
    .slice(0, Math.min(...gioiHan))
    .map((r) => ({ sdt: String(r.sdt), ten: String(r.ten ?? ""), noiDung: String(r.noiDung ?? "").slice(0, 5000),
      duAn: String(r.duAn ?? "").slice(0, 100), loiMoi: String(r.loiMoi ?? "").slice(0, 3000),
      nhanVien: String(r.nhanVien ?? "").slice(0, 255), kieu: KIEU.has(r.kieu) ? r.kieu : "lan_dau",
      zaloUid: /^\d{1,40}$/.test(String(r.zaloUid ?? "")) ? String(r.zaloUid) : "",
      chienDichId: Number.isInteger(r.chienDichId) ? r.chienDichId : null }));
  if (!batch.length) throw new Error("Không có số nào để gửi (các số còn lại đang được người khác gửi)");
  if (cfg.conLaiHomNay != null) log(a, `Hạn mức hôm nay: còn ${cfg.conLaiHomNay} người, đợt này gửi ${batch.length} người.`);
  // Ảnh gửi kèm tin nhắn (quản trị cài), đọc 1 lần cho cả đợt
  const attachments = cfg.guiTinNhan ? loadAttachments(cfg.anh) : [];
  if (attachments.length) log(a, `Tin nhắn sẽ gửi kèm ${attachments.length} ảnh.`);

  launch(a, {
    kind: "ca-nhan", items: batch, cfg, runBy, label: "người", dotId: Number.isInteger(dotId) ? dotId : null,
    task: (api, row, c) => processOne(api, row, c, attachments),
    keyOf: (r, long) => (long ? `${r.sdt} - ${r.ten || "không tên"}` : r.sdt),
    onResult: async (row, res) => {
      // Lưu kết quả là quan trọng nhất: ghi vào hàng đợi trên đĩa, gửi lại đến khi Django nhận
      const daLuu = await reportDurable("result", { sdt: row.sdt, ketQua: { ...res, taiKhoanZalo: a.account },
        nguoiGuiId: runBy?.id ?? a.uid, dotId: a.dotId, kieu: row.kieu, chienDichId: row.chienDichId });
      if (!daLuu) log(a, `${row.sdt}: chưa lưu được kết quả vào web, worker sẽ tự gửi lại (số này không bị gửi 2 lần).`, "warn");
      const bad = res.ketBan === "Lỗi" || res.tinNhan === "Lỗi";
      const ten = res.tenGoiNho ? `, tên gợi nhớ “${res.tenGoiNho}”` : "";
      log(a, `${row.sdt}: kết bạn ${res.ketBan}${ten}, tin nhắn ${res.tinNhan}${res.ghiChu ? ` (${res.ghiChu})` : ""}`, bad ? "error" : "ok");
    },
  });
}

// Gửi cùng một nội dung (kèm ảnh) vào các nhóm Zalo đã chọn
const MAX_NHOM = 100;
function startGroupRun(a, { groups, noiDung, anh, cfg, runBy, dotId }) {
  canRun(a);
  cfg = sanitizeConfig(cfg);
  // Mỗi nhóm có thể có nội dung riêng (noiDung của nhóm), không có thì dùng nội dung chung
  const items = (Array.isArray(groups) ? groups : [])
    .filter((g) => g && /^\d+$/.test(String(g.id)))
    .slice(0, MAX_NHOM)
    .map((g) => ({ id: String(g.id), ten: String(g.ten ?? g.id).slice(0, 255), noiDung: String(g.noiDung ?? "").trim().slice(0, 5000) }));
  if (!items.length) throw new Error("Chưa chọn nhóm nào");
  const job = { noiDung: String(noiDung ?? "").trim().slice(0, 5000), attachments: loadAttachments(Array.isArray(anh) ? anh.slice(0, 10) : []) };
  if (!job.noiDung && !job.attachments.length && !items.some((g) => g.noiDung)) throw new Error("Chưa có nội dung hay ảnh để gửi");

  launch(a, {
    kind: "nhom", items, cfg, runBy, label: "nhóm", dotId: Number.isInteger(dotId) ? dotId : null,
    task: (api, g) => processGroup(api, g, job),
    keyOf: (g, long) => (long ? `nhóm “${g.ten}”` : g.ten),
    onResult: async (g, res) => {
      await reportDurable("result_nhom", { nhomId: g.id, ketQua: { ...res, taiKhoanZalo: a.account },
        nguoiGuiId: runBy?.id ?? a.uid, dotId: a.dotId });
      log(a, `Nhóm “${g.ten}”: ${res.ketQua}${res.ghiChu ? ` (${res.ghiChu})` : ""}`, res.ketQua === "Lỗi" ? "error" : "ok");
    },
  });
}

async function groupsOf(a) {
  if (!a.api) throw new Error("Chưa kết nối Zalo. Quét QR để kết nối Zalo của bạn trước");
  try {
    return { groups: await listGroups(a.api) };
  } catch (e) {
    throw new Error(`Không lấy được danh sách nhóm: ${e.message}`);
  }
}

function stopRun(a) {
  if (a.running && !a.stopRequested) {
    a.stopRequested = true;
    log(a, "Đang dừng... sẽ dừng sau lần gửi hiện tại.", "warn");
  }
}

// ---------- HTTP ----------
const send = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
};
const MAX_BODY = 50_000_000;
const readJson = (req) => new Promise((resolve, reject) => {
  const chunks = [];
  let size = 0;
  req.on("data", (c) => {
    size += c.length;
    if (size > MAX_BODY) {
      req.pause();
      reject(Object.assign(new Error("Dữ liệu gửi lên quá lớn"), { status: 413 }));   // không để request treo
      return;
    }
    chunks.push(c);
  });
  req.on("end", () => { try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {}); } catch (e) { reject(e); } });
  req.on("error", reject);
});
const tokenOk = (req) => {
  const got = Buffer.from(String(req.headers["x-worker-token"] || ""));
  const want = Buffer.from(TOKEN);
  return got.length === want.length && crypto.timingSafeEqual(got, want);
};

const routes = {
  // Trạng thái 1 tài khoản (kèm mã QR cho chính chủ)
  "GET /status": (q) => publicState(acc(q.get("uid"))),
  // Tóm tắt mọi tài khoản đã từng kết nối (cho bảng điều khiển quản trị, không kèm QR)
  "GET /status/all": () => ({
    accounts: Object.fromEntries([...new Set([...accounts.keys(), ...savedSessionIds()])]
      .map((uid) => [uid, { ...publicState(acc(uid), false), saved: hasZaloSession(uid) }])),
  }),
  "POST /login": (q, b) => { startLogin(acc(b.uid), { useSession: !b.qr }); return { ok: true }; },
  "POST /login/cancel": (q, b) => (cancelLogin(acc(b.uid)), { ok: true }),
  "POST /logout": (q, b) => (logoutZalo(acc(b.uid)), { ok: true }),
  "POST /run": (q, b) => (startRun(acc(b.uid), b), { ok: true }),
  "GET /groups": (q) => groupsOf(acc(q.get("uid"))),
  "POST /run-groups": (q, b) => (startGroupRun(acc(b.uid), b), { ok: true }),
  "POST /stop": (q, b) => (stopRun(acc(b.uid)), { ok: true }),
};

http.createServer(async (req, res) => {
  if (!tokenOk(req)) return send(res, 403, { error: "Sai mã worker" });
  const url = new URL(req.url, "http://x");
  const route = routes[`${req.method} ${url.pathname}`];
  if (!route) return send(res, 404, { error: "Không tìm thấy" });
  try {
    send(res, 200, await route(url.searchParams, req.method === "POST" ? await readJson(req) : {}));
  } catch (e) {
    send(res, e.status || 400, { error: e.message });
    if (e.status === 413) req.destroy();
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`Zalo worker đang chạy (nội bộ) tại 127.0.0.1:${PORT}. Giữ cửa sổ này mở.`);
  secureSessionDir();
  if (queue.length) {
    console.log(`Còn ${queue.length} kết quả chưa báo về web từ lần chạy trước, đang gửi lại...`);
    flushQueue(3);
  }
  // Tự kết nối lại các tài khoản đã lưu phiên Zalo
  for (const uid of savedSessionIds()) startLogin(acc(uid), { auto: true });
});
