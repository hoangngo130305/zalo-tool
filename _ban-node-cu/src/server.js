// Giao diện web. Mặc định chỉ mở trên máy này (127.0.0.1);
// đặt "moChoMangNoiBo": true trong config.json để máy khác cùng mạng truy cập được
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { exec } from "node:child_process";
import XLSX from "xlsx";
import {
  ROOT, loadConfig, saveConfig, readList, writeList, rowsFromWorkbook, normalizePhone, loadDone, pendingRows, fill,
  newZalo, hasZaloSession, clearZaloSession, loginWithSession, loginWithQR, runBatch,
} from "./core.js";
import * as store from "./db.js";
import * as auth from "./auth.js";

const PORT = Number(process.env.PORT) || 3000;
const PUBLIC = path.join(ROOT, "public");

// ---------- trạng thái ----------
const state = {
  api: null,
  account: null,            // tên Zalo đang đăng nhập
  login: "out",             // out | checking | qr | in
  qr: null,                 // ảnh QR dạng data URL
  qrActions: null,
  running: false,
  runBy: null,              // { username, name } người đang gửi
  stopRequested: false,
  current: null,            // SĐT đang gửi
  progress: null,           // { i, total }
  waitUntil: null,
};
const clients = new Map(); // res -> { req, user }

// data có thể là hàm (user) => dữ liệu, để mỗi người chỉ nhận phần mình được xem
function broadcast(type, data) {
  for (const [res, c] of clients) {
    const payload = typeof data === "function" ? data(c.user) : data;
    res.write(`event: ${type}\ndata: ${JSON.stringify(payload)}\n\n`);
  }
}
// Sau khi đổi quyền / xoá tài khoản: cập nhật quyền của các trang đang mở, ngắt người không còn hợp lệ
async function revalidateClients() {
  for (const [res, c] of clients) {
    const user = await auth.userFromRequest(c.req).catch(() => null);
    if (!user) { res.end(); clients.delete(res); } else c.user = user;
  }
}

function log(text, level = "info") {
  const entry = { t: new Date().toLocaleString("vi-VN"), text, level };
  console.log(`[${entry.t}] ${text}`);
  broadcast("log", entry);
  store.addLogRow(entry).catch((e) => console.error("Không ghi được nhật ký:", e.message));
}

// Mã QR chỉ gửi cho người có quyền đăng nhập Zalo
const publicState = (user) => ({
  login: state.login, account: state.account, qr: auth.can(user, "zalo") ? state.qr : null,
  running: state.running, runBy: state.runBy, stopRequested: state.stopRequested,
  current: state.current, progress: state.progress, waitUntil: state.waitUntil,
});
const pushState = () => broadcast("state", publicState);

// ---------- dữ liệu ----------
// Phạm vi danh sách: nhân viên luôn chỉ thấy số của mình; quản trị / chỉ xem chọn được
// "all" (tất cả), "none" (chưa giao) hoặc username của một nhân viên
function ownerScope(user, phamVi = "all") {
  if (!auth.can(user, "all")) return user.username;
  if (phamVi === "none") return null;
  if (!phamVi || phamVi === "all") return undefined;
  return String(phamVi);
}

async function contactsView(user) {
  const [list, done] = await Promise.all([readList(ownerScope(user)), loadDone()]);
  return list.map((r) => ({
    sdt: r.sdtGoc, ten: r.ten, sdtChuan: r.sdt, phuTrach: r.phuTrach, phuTrachTen: r.phuTrachTen,
    ketQua: r.sdt ? done[r.sdt] || null : null,
  }));
}

// Báo các trang đang mở tải lại danh sách (khi người khác vừa sửa)
const notifyContacts = (by) => broadcast("contacts", { by });

// ---------- đăng nhập Zalo ----------
async function finishLogin(api) {
  state.api = api;
  state.login = "in";
  state.qr = null;
  state.qrActions = null;
  try {
    const info = await api.fetchAccountInfo();
    state.account = info?.profile?.displayName || info?.profile?.zaloName || "Tài khoản Zalo";
  } catch {
    state.account = "Tài khoản Zalo";
  }
  log(`Đã đăng nhập Zalo: ${state.account}`, "ok");
  pushState();
}

async function startLogin({ useSession = true } = {}) {
  if (state.login === "checking" || state.login === "qr") return;
  state.login = "checking";
  pushState();

  if (useSession) {
    const api = await loginWithSession(newZalo());
    if (api) return finishLogin(api);
  }

  log("Đang tạo mã QR, mở app Zalo trên điện thoại để quét.");
  try {
    const api = await loginWithQR(newZalo(), async (ev) => {
      if (ev.type === "qr") {
        state.login = "qr";
        state.qr = `data:image/png;base64,${ev.image}`;
        state.qrActions = ev.actions;
        pushState();
      } else if (ev.type === "expired") log("Mã QR hết hạn, đang tạo mã mới...", "warn");
      else if (ev.type === "scanned") log(`Đã quét bởi ${ev.name}. Bấm xác nhận trên điện thoại.`);
      else if (ev.type === "declined") log("Đăng nhập bị từ chối trên điện thoại.", "warn");
    }, path.join(ROOT, "qr.png"));
    if (!api) throw new Error("Đăng nhập bị huỷ");
    await finishLogin(api);
  } catch (e) {
    Object.assign(state, { login: "out", qr: null, qrActions: null });
    log(`Không đăng nhập được Zalo: ${e.message}`, "error");
    pushState();
  }
}

function cancelLogin() {
  try { state.qrActions?.abort?.(); } catch {}
  Object.assign(state, { login: "out", qr: null, qrActions: null });
  pushState();
}

async function logoutZalo() {
  if (state.running) throw new Error("Đang gửi, hãy bấm Dừng trước khi đăng xuất");
  Object.assign(state, { api: null, account: null, login: "out" });
  await clearZaloSession();
  pushState();
}

// ---------- gửi ----------
// Chờ theo từng nhịp ngắn để nút Dừng có hiệu lực ngay
async function interruptibleWait(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end && !state.stopRequested) await new Promise((r) => setTimeout(r, 500));
}

async function startRun(user, phamVi) {
  if (state.running) throw new Error(`${state.runBy?.name || "Người khác"} đang gửi, chờ xong rồi gửi tiếp`);
  if (!state.api) throw new Error("Chưa đăng nhập Zalo");
  const cfg = await loadConfig();
  if (!cfg.guiKetBan && !cfg.guiTinNhan) throw new Error("Đang tắt cả kết bạn lẫn tin nhắn, hãy bật ít nhất một mục");
  const batch = pendingRows(await readList(ownerScope(user, phamVi)), await loadDone()).slice(0, cfg.gioiHanMoiLan);
  if (!batch.length) throw new Error("Không còn ai cần gửi trong phạm vi này");

  state.running = true;
  state.runBy = { username: user.username, name: user.name };
  state.stopRequested = false;
  state.progress = { i: 0, total: batch.length };
  log(`${user.name} bắt đầu gửi cho ${batch.length} người.`, "ok");
  pushState();

  runBatch({
    api: state.api, cfg, batch, nguoiGui: user.username,
    shouldStop: () => state.stopRequested,
    wait: interruptibleWait,
    onStart: (row, i, n) => {
      state.current = row.sdt;
      state.waitUntil = null;
      state.progress = { i, total: n };
      log(`(${i + 1}/${n}) Đang gửi ${row.sdt} - ${row.ten || "không tên"}`);
      pushState();
    },
    onResult: (row, res, i, n) => {
      state.current = null;
      state.progress = { i: i + 1, total: n };
      const bad = res.ketBan === "Lỗi" || res.tinNhan === "Lỗi";
      log(`${row.sdt}: kết bạn ${res.ketBan}, tin nhắn ${res.tinNhan}${res.ghiChu ? ` (${res.ghiChu})` : ""}`, bad ? "error" : "ok");
      broadcast("result", { sdt: row.sdt, ketQua: res });
      pushState();
    },
    onWait: (ms) => {
      state.waitUntil = Date.now() + ms;
      log(`Chờ ${Math.round(ms / 1000)} giây trước người tiếp theo...`);
      pushState();
    },
  })
    .then((r) => log(r.stopped ? `Đã dừng sau ${r.sent} người.` : "Hoàn tất đợt gửi.", r.stopped ? "warn" : "ok"))
    .catch((e) => log(`Lỗi khi gửi: ${e.message}`, "error"))
    .finally(() => {
      Object.assign(state, { running: false, runBy: null, stopRequested: false, current: null, waitUntil: null });
      pushState();
    });
}

// ---------- HTTP ----------
const send = (res, code, body, type = "application/json; charset=utf-8") => {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(typeof body === "string" || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};
const readBody = (req, limit = 10 * 1024 * 1024) => new Promise((resolve, reject) => {
  const chunks = [];
  let size = 0;
  req.on("data", (c) => {
    size += c.length;
    if (size > limit) { reject(new Error("File quá lớn")); req.destroy(); }
    else chunks.push(c);
  });
  req.on("end", () => resolve(Buffer.concat(chunks)));
  req.on("error", reject);
});
const readJson = async (req) => {
  const buf = await readBody(req, 2 * 1024 * 1024);
  return buf.length ? JSON.parse(buf.toString("utf8")) : {};
};

// Mỗi route khai báo quyền cần có; handler nhận (req, user)
const routes = {
  "GET /api/state": ["view", async (req, user) => ({
    ...publicState(user), config: await loadConfig(), contacts: await contactsView(user), logs: await store.recentLogs(),
    staff: auth.can(user, "all") ? await auth.listAssignees() : [],
  })],
  "GET /api/contacts": ["view", async (req, user) => ({
    contacts: await contactsView(user), staff: auth.can(user, "all") ? await auth.listAssignees() : [],
  })],

  "POST /api/config": ["settings", async (req) => {
    if (state.running) throw new Error("Đang gửi, không sửa cấu hình được");
    const b = await readJson(req);
    const allowed = ["loiMoiKetBan", "noiDungTinNhan", "guiKetBan", "guiTinNhan", "gioiHanMoiLan", "choToiThieuGiay", "choToiDaGiay"];
    return { config: await saveConfig(Object.fromEntries(Object.entries(b).filter(([k]) => allowed.includes(k)))) };
  }],

  // Nhân viên chỉ ghi được phần của mình; quản trị ghi cả danh sách kèm người phụ trách
  "POST /api/contacts": ["list", async (req, user) => {
    if (state.running) throw new Error("Đang gửi, không sửa danh sách được");
    const { contacts = [] } = await readJson(req);
    if (auth.can(user, "all")) {
      const valid = new Set((await auth.listAssignees()).map((u) => u.username));
      await writeList(contacts.map((c) => ({ ...c, phuTrach: valid.has(c.phuTrach) ? c.phuTrach : null })));
    } else {
      await writeList(contacts, { owner: user.username });
    }
    notifyContacts(user.username);
    return { contacts: await contactsView(user) };
  }],

  // Chia đều các số chưa giao cho nhân viên (lần lượt từng người)
  "POST /api/assign": ["settings", async (req, user) => {
    if (state.running) throw new Error("Đang gửi, không chia lại được");
    const staff = (await auth.listAssignees()).filter((u) => u.role === "nhanvien");
    if (!staff.length) throw new Error("Chưa có tài khoản nhân viên nào. Tạo trong Quản lý tài khoản trước.");
    const all = await readList();
    let k = 0;
    const rows = all.map((r) => ({ sdt: r.sdtGoc, ten: r.ten, phuTrach: r.phuTrach || staff[k++ % staff.length].username }));
    await writeList(rows);
    log(`${user.name} đã chia ${k} số chưa giao cho ${staff.length} nhân viên.`, "ok");
    notifyContacts(user.username);
    return { contacts: await contactsView(user), chia: k };
  }],

  "POST /api/import": ["list", async (req) => {
    const rows = rowsFromWorkbook(XLSX.read(await readBody(req), { type: "buffer" }));
    return { rows: rows.map((r) => ({ sdt: r.sdtGoc, ten: r.ten })) };
  }],

  "POST /api/preview": ["view", async (req, user) => {
    const { phamVi } = await readJson(req);
    const cfg = await loadConfig();
    const batch = pendingRows(await readList(ownerScope(user, phamVi)), await loadDone()).slice(0, cfg.gioiHanMoiLan);
    return {
      items: batch.map((r) => ({
        sdt: r.sdt, ten: r.ten,
        loiMoi: cfg.guiKetBan ? fill(cfg.loiMoiKetBan, r) : null,
        tinNhan: cfg.guiTinNhan ? fill(cfg.noiDungTinNhan, r) : null,
      })),
    };
  }],

  // Gửi lại 1 số: nhân viên được làm; xoá toàn bộ lịch sử: chỉ quản trị
  "POST /api/reset": ["send", async (req, user) => {
    if (state.running) throw new Error("Đang gửi, hãy dừng trước");
    const { sdt } = await readJson(req);
    if (!sdt && !auth.can(user, "settings")) throw new Error("Chỉ quản trị viên được xoá toàn bộ lịch sử gửi");
    const phone = sdt ? normalizePhone(sdt) : null;
    if (sdt && !auth.can(user, "all") && !(await readList(user.username)).some((r) => r.sdt === phone)) {
      throw new Error("Số này không thuộc danh sách của bạn");
    }
    if (sdt) await store.deleteResult(phone);
    else await store.clearResults();
    log(sdt ? `${user.name} đánh dấu ${sdt} để gửi lại.` : `${user.name} đã xoá toàn bộ lịch sử gửi.`, "warn");
    notifyContacts(user.username);
    return { contacts: await contactsView(user) };
  }],

  "POST /api/login": ["zalo", async (req) => {
    const { qr } = await readJson(req);
    startLogin({ useSession: !qr });
    return { ok: true };
  }],
  "POST /api/login/cancel": ["zalo", () => (cancelLogin(), { ok: true })],
  "POST /api/logout": ["zalo", async (req, user) => {
    await logoutZalo();
    log(`${user.name} đã đăng xuất Zalo và xoá phiên đã lưu.`);
    return { ok: true };
  }],
  "POST /api/start": ["send", async (req, user) => {
    const { phamVi } = await readJson(req);
    await startRun(user, phamVi);
    return { ok: true };
  }],
  // Chỉ người đang gửi hoặc quản trị được dừng
  "POST /api/stop": ["send", (req, user) => {
    if (state.running && state.runBy?.username !== user.username && !auth.can(user, "settings")) {
      throw new Error(`Chỉ ${state.runBy?.name} hoặc quản trị viên được dừng đợt gửi này`);
    }
    if (state.running && !state.stopRequested) {
      state.stopRequested = true;
      log(`${user.name} bấm dừng. Sẽ dừng sau người hiện tại.`, "warn");
      pushState();
    }
    return { ok: true };
  }],

  // ---------- tài khoản người dùng ----------
  "GET /api/users": ["users", async () => ({ users: await auth.listUsers() })],
  "POST /api/users": ["users", async (req, user) => {
    const u = await auth.createUser(await readJson(req));
    log(`${user.name} đã tạo tài khoản ${u.username} (${u.roleLabel}).`);
    return { users: await auth.listUsers() };
  }],
  "POST /api/users/update": ["users", async (req, user) => {
    const { username, name, role, password } = await readJson(req);
    if (username === user.username && role && role !== user.role) throw new Error("Không thể tự đổi vai trò của chính mình");
    const u = await auth.updateUser(username, { name, role, password });
    log(`${user.name} đã cập nhật tài khoản ${u.username}${password ? " (đặt lại mật khẩu)" : ""}.`);
    await revalidateClients();
    return { users: await auth.listUsers() };
  }],
  "POST /api/users/delete": ["users", async (req, user) => {
    const { username } = await readJson(req);
    await auth.deleteUser(username, user);
    log(`${user.name} đã xoá tài khoản ${username}. Các số người này phụ trách chuyển về "chưa giao".`, "warn");
    await revalidateClients();
    notifyContacts(user.username);
    return { users: await auth.listUsers() };
  }],
};

// Không cần đăng nhập
const publicRoutes = {
  "GET /api/me": async (req) => {
    const user = await auth.userFromRequest(req);
    return user ? { user } : { user: null, needsSetup: await auth.needsSetup() };
  },
  "POST /api/auth/setup": async (req, res) => {
    const b = await readJson(req);
    await auth.setupFirstAdmin(b);
    return signIn(req, res, b.username, b.password);
  },
  "POST /api/auth/login": async (req, res) => {
    const { username, password } = await readJson(req);
    return signIn(req, res, username, password);
  },
  "POST /api/auth/logout": async (req, res) => {
    await auth.logout(req);
    res.setHeader("Set-Cookie", auth.clearCookie());
    return { ok: true };
  },
  "POST /api/auth/password": async (req) => {
    const user = await auth.userFromRequest(req);
    if (!user) throw Object.assign(new Error("Chưa đăng nhập"), { status: 401 });
    const { current, next } = await readJson(req);
    await auth.changeOwnPassword(user.username, current, next);
    await revalidateClients();
    return { ok: true };
  },
};

async function signIn(req, res, username, password) {
  const { token, user } = await auth.login(req.socket.remoteAddress, username, password);
  res.setHeader("Set-Cookie", auth.sessionCookie(token));
  log(`${user.name} (${user.roleLabel}) đã đăng nhập Zalo Tool.`);
  return { user };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  const key = `${req.method} ${url.pathname}`;

  try {
    if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return send(res, 200, fs.readFileSync(path.join(PUBLIC, "index.html")), "text/html; charset=utf-8");
    }
    // Chặn trang web khác gửi yêu cầu thay người dùng: POST phải là JSON hoặc file tải lên từ chính trang này
    if (req.method === "POST" && !/^application\/(json|octet-stream)/.test(req.headers["content-type"] || "")) {
      return send(res, 415, { error: "Yêu cầu không hợp lệ" });
    }
    if (publicRoutes[key]) return send(res, 200, await publicRoutes[key](req, res));

    const user = await auth.userFromRequest(req);
    if (!user) return send(res, 401, { error: "Phiên đăng nhập đã hết, hãy đăng nhập lại" });

    if (key === "GET /api/events") {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store", Connection: "keep-alive" });
      res.write(`event: state\ndata: ${JSON.stringify(publicState(user))}\n\n`);
      clients.set(res, { req, user });
      const ping = setInterval(() => res.write(": ping\n\n"), 20000);
      req.on("close", () => { clearInterval(ping); clients.delete(res); });
      return;
    }

    if (key === "GET /api/report") {
      const buf = await store.reportBuffer();
      res.writeHead(200, {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": `attachment; filename="ket-qua.xlsx"`,
        "Cache-Control": "no-store",
      });
      return res.end(buf);
    }

    const route = routes[key];
    if (!route) return send(res, 404, { error: "Không tìm thấy" });
    const [perm, handler] = route;
    if (!auth.can(user, perm)) return send(res, 403, { error: "Tài khoản của bạn không có quyền làm việc này" });
    send(res, 200, await handler(req, user));
  } catch (e) {
    // Lỗi database: ghi chi tiết ở cửa sổ đen, trên trang chỉ báo ngắn gọn
    const dbError = typeof e.code === "string" && /^(ER_|ECONN|PROTOCOL_)/.test(e.code);
    if (dbError) console.error("Lỗi database:", e.message);
    send(res, e.status || (dbError ? 500 : 400), { error: dbError ? "Lỗi database. Kiểm tra MySQL trong XAMPP còn chạy không." : e.message });
  }
});

// Địa chỉ IP trong mạng nội bộ để máy khác truy cập
const lanAddresses = () => Object.values(os.networkInterfaces()).flat()
  .filter((a) => a && a.family === "IPv4" && !a.internal).map((a) => a.address);

async function main() {
  await store.connect();
  console.log("Đã kết nối MySQL.");
  const lan = store.readConfigFile().moChoMangNoiBo === true;

  server.listen(PORT, lan ? "0.0.0.0" : "127.0.0.1", async () => {
    const link = `http://localhost:${PORT}`;
    console.log(`Zalo Tool đang chạy tại ${link} (giữ cửa sổ này mở trong lúc dùng)`);
    if (lan) for (const ip of lanAddresses()) console.log(`Máy khác cùng mạng truy cập: http://${ip}:${PORT}`);
    if (await auth.needsSetup()) console.log("Chưa có tài khoản nào: mở trang web để tạo tài khoản quản trị.");
    if (!process.argv.includes("--khong-mo")) exec(process.platform === "win32" ? `start "" ${link}` : `xdg-open ${link}`);
    // Có phiên Zalo cũ thì tự đăng nhập luôn
    if (await hasZaloSession()) startLogin();
  });
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
