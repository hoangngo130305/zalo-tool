// Zalo worker: dịch vụ nội bộ lo đăng nhập Zalo và gửi tin.
// Mỗi tài khoản Zalo Tool (nhân viên / quản trị) có một phiên Zalo riêng, gửi song song được.
// Chỉ mở trên 127.0.0.1, Django gọi vào kèm mã bí mật (config.json -> worker.token).
// Mỗi kết quả / nhật ký được báo ngược về Django để lưu MySQL.
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import {
  ROOT, sanitizeConfig, newZalo, hasZaloSession, clearZaloSession, savedSessionIds,
  loginWithSession, loginWithQR, runBatch, sleep, processGroup, listGroups, loadAttachments,
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
    });
  }
  return accounts.get(uid);
}
const publicState = (a, withQr = true) => {
  const { api, qrActions, pending, uid, ...rest } = a;
  return { ...rest, qr: withQr ? a.qr : null };
};

// ---------- báo về Django ----------
async function report(type, data, tries = 5) {
  for (let i = 1; i <= tries; i++) {
    try {
      const res = await fetch(`${DJANGO}/api/worker/event`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Worker-Token": TOKEN },
        body: JSON.stringify({ type, ...data }),
      });
      if (res.ok) return true;
      throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      if (i === tries) console.error(`Không báo được về Django (${type}): ${e.message}`);
      else await sleep(1000 * i);
    }
  }
  return false;
}
// Nhật ký gắn với chủ tài khoản Zalo để mỗi nhân viên chỉ thấy nhật ký của mình
function log(a, text, level = "info") {
  console.log(`[${new Date().toLocaleTimeString("vi-VN")}] #${a.uid} ${text}`);
  report("log", { text, level, nguoiDungId: a.uid }, 2);
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
}

async function startLogin(a, { useSession = true } = {}) {
  if (a.login === "checking" || a.login === "qr") return;
  a.login = "checking";

  if (useSession) {
    const api = await loginWithSession(newZalo(), a.uid);
    if (api) return finishLogin(a, api);
    if (hasZaloSession(a.uid)) log(a, "Phiên Zalo cũ đã hết hạn, cần quét QR lại.", "warn");
  }

  log(a, "Đang tạo mã QR, mở app Zalo trên điện thoại để quét.");
  try {
    const api = await loginWithQR(newZalo(), a.uid, async (ev) => {
      if (ev.type === "qr") Object.assign(a, { login: "qr", qr: `data:image/png;base64,${ev.image}`, qrActions: ev.actions });
      else if (ev.type === "expired") log(a, "Mã QR hết hạn, đang tạo mã mới...", "warn");
      else if (ev.type === "scanned") log(a, `Đã quét bởi ${ev.name}. Bấm xác nhận trên điện thoại.`);
      else if (ev.type === "declined") log(a, "Đăng nhập bị từ chối trên điện thoại.", "warn");
    });
    if (!api) throw new Error("Đăng nhập bị huỷ");
    await finishLogin(a, api);
  } catch (e) {
    Object.assign(a, { login: "out", qr: null, qrActions: null });
    // Người dùng bấm Huỷ thì không coi là lỗi
    if (/abort|huỷ/i.test(e.message)) log(a, "Đã huỷ kết nối Zalo.", "warn");
    else log(a, `Không kết nối được Zalo: ${e.message}`, "error");
  }
}

function cancelLogin(a) {
  try { a.qrActions?.abort?.(); } catch {}
  Object.assign(a, { login: "out", qr: null, qrActions: null });
}

function logoutZalo(a) {
  if (a.running) throw new Error("Đang gửi, hãy bấm Dừng trước khi ngắt kết nối Zalo");
  Object.assign(a, { api: null, account: null, login: "out" });
  clearZaloSession(a.uid);
}

// ---------- gửi ----------
// Chạy 1 đợt cho tài khoản a: items là danh sách người / nhóm, task xử lý từng mục
function launch(a, { kind, items, cfg, runBy, task, label, keyOf, onResult }) {
  Object.assign(a, {
    running: true, kind, runBy: runBy || null, stopRequested: false,
    progress: { i: 0, total: items.length }, pending: kind === "ca-nhan" ? items.map((r) => r.sdt) : [],
  });
  const nghi = cfg.nghiSauSoLan ? ` Cứ ${cfg.nghiSauSoLan} lần gửi nghỉ ${cfg.nghiPhut} phút.` : "";
  log(a, `${runBy?.name || "Ai đó"} bắt đầu gửi cho ${items.length} ${label} bằng Zalo ${a.account}.${nghi}`, "ok");

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
      if (kind === "ca-nhan") a.pending = a.pending.filter((s) => s !== row.sdt);
      await onResult(row, res);
    },
    onWait: (ms, nghi) => {
      Object.assign(a, { waitUntil: Date.now() + ms, waitBreak: !!nghi });
      log(a, nghi ? `Đã gửi ${cfg.nghiSauSoLan} lần, nghỉ ${Math.round(ms / 60000)} phút rồi gửi tiếp...`
        : `Chờ ${Math.round(ms / 1000)} giây trước lần gửi tiếp theo...`, nghi ? "warn" : "info");
    },
  })
    .then((r) => log(a, r.stopped ? `Đã dừng sau ${r.sent} ${label}.` : "Hoàn tất đợt gửi.", r.stopped ? "warn" : "ok"))
    .catch((e) => log(a, `Lỗi khi gửi: ${e.message}`, "error"))
    .finally(() => Object.assign(a, {
      running: false, kind: null, runBy: null, stopRequested: false, current: null, waitUntil: null, waitBreak: false, pending: [],
    }));
}

function canRun(a) {
  if (a.running) throw new Error("Bạn đang có một đợt gửi chưa xong");
  if (!a.api) throw new Error("Chưa kết nối Zalo. Quét QR để kết nối Zalo của bạn trước khi gửi");
}

// Kết bạn + nhắn tin từng người
function startRun(a, { batch, cfg, runBy }) {
  canRun(a);
  cfg = sanitizeConfig(cfg);
  if (!cfg.guiKetBan && !cfg.guiTinNhan) throw new Error("Đang tắt cả kết bạn lẫn tin nhắn");
  // Số đang được tài khoản khác gửi thì bỏ qua để không ai nhận 2 lần
  const dangGui = new Set([...accounts.values()].filter((x) => x !== a && x.running).flatMap((x) => x.pending));
  batch = (Array.isArray(batch) ? batch : [])
    .filter((r) => /^0\d{9}$/.test(String(r?.sdt)) && !dangGui.has(String(r.sdt)))
    .slice(0, cfg.gioiHanMoiLan)
    .map((r) => ({ sdt: String(r.sdt), ten: String(r.ten ?? ""), noiDung: String(r.noiDung ?? "").slice(0, 5000) }));
  if (!batch.length) throw new Error("Không có số nào để gửi (các số còn lại đang được người khác gửi)");

  launch(a, {
    kind: "ca-nhan", items: batch, cfg, runBy, label: "người",
    keyOf: (r, long) => (long ? `${r.sdt} - ${r.ten || "không tên"}` : r.sdt),
    onResult: async (row, res) => {
      // Lưu kết quả là quan trọng nhất: thử lại nhiều lần
      await report("result", { sdt: row.sdt, ketQua: { ...res, taiKhoanZalo: a.account }, nguoiGuiId: runBy?.id ?? a.uid }, 8);
      const bad = res.ketBan === "Lỗi" || res.tinNhan === "Lỗi";
      const ten = res.tenGoiNho ? `, tên gợi nhớ “${res.tenGoiNho}”` : "";
      log(a, `${row.sdt}: kết bạn ${res.ketBan}${ten}, tin nhắn ${res.tinNhan}${res.ghiChu ? ` (${res.ghiChu})` : ""}`, bad ? "error" : "ok");
    },
  });
}

// Gửi cùng một nội dung (kèm ảnh) vào các nhóm Zalo đã chọn
const MAX_NHOM = 100;
function startGroupRun(a, { groups, noiDung, anh, cfg, runBy }) {
  canRun(a);
  cfg = sanitizeConfig(cfg);
  const items = (Array.isArray(groups) ? groups : [])
    .filter((g) => g && /^\d+$/.test(String(g.id)))
    .slice(0, MAX_NHOM)
    .map((g) => ({ id: String(g.id), ten: String(g.ten ?? g.id).slice(0, 255) }));
  if (!items.length) throw new Error("Chưa chọn nhóm nào");
  const job = { noiDung: String(noiDung ?? "").trim().slice(0, 5000), attachments: loadAttachments(Array.isArray(anh) ? anh.slice(0, 10) : []) };
  if (!job.noiDung && !job.attachments.length) throw new Error("Chưa có nội dung hay ảnh để gửi");

  launch(a, {
    kind: "nhom", items, cfg, runBy, label: "nhóm",
    task: (api, g) => processGroup(api, g, job),
    keyOf: (g, long) => (long ? `nhóm “${g.ten}”` : g.ten),
    onResult: async (g, res) => {
      await report("result_nhom", { nhomId: g.id, ketQua: { ...res, taiKhoanZalo: a.account }, nguoiGuiId: runBy?.id ?? a.uid }, 8);
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
const readJson = (req) => new Promise((resolve, reject) => {
  const chunks = [];
  let size = 0;
  req.on("data", (c) => { size += c.length; if (size > 2_000_000) req.destroy(); else chunks.push(c); });
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
    send(res, 400, { error: e.message });
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`Zalo worker đang chạy (nội bộ) tại 127.0.0.1:${PORT}. Giữ cửa sổ này mở.`);
  // Tự kết nối lại các tài khoản đã lưu phiên Zalo
  for (const uid of savedSessionIds()) startLogin(acc(uid));
});
