// Tài khoản người dùng & phân quyền cho giao diện web (lưu trong bảng tai_khoan, phien_dang_nhap)
import crypto from "node:crypto";
import { db } from "./db.js";

const SESSION_HOURS = 12;
const COOKIE = "zt_sid";

// "all" = thấy toàn bộ danh sách; thiếu quyền này (nhân viên) thì chỉ thấy các số được giao cho mình
export const ROLES = {
  admin: { label: "Quản trị", perms: ["view", "all", "list", "send", "settings", "zalo", "users"] },
  nhanvien: { label: "Nhân viên", perms: ["view", "list", "send"] },
  xem: { label: "Chỉ xem", perms: ["view", "all"] },
};

export const publicUser = (u) => ({
  username: u.username, name: u.name, role: u.role,
  roleLabel: ROLES[u.role]?.label ?? u.role, perms: ROLES[u.role]?.perms ?? [],
});
export const can = (user, perm) => !!user && (ROLES[user.role]?.perms ?? []).includes(perm);

// ---------- truy vấn ----------
async function findUser(username) {
  const [rows] = await db().query("SELECT * FROM tai_khoan WHERE username = ?", [username]);
  return rows[0] || null;
}
async function adminCount() {
  const [[r]] = await db().query("SELECT COUNT(*) AS n FROM tai_khoan WHERE role = 'admin'");
  return Number(r.n);
}
export async function needsSetup() {
  const [[r]] = await db().query("SELECT COUNT(*) AS n FROM tai_khoan");
  return Number(r.n) === 0;
}

// ---------- mật khẩu ----------
function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString("hex");
  return { salt, hash: crypto.scryptSync(password, salt, 64).toString("hex") };
}
function checkPassword(u, password) {
  const a = Buffer.from(u.hash, "hex");
  const b = crypto.scryptSync(String(password), u.salt, 64);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// ---------- kiểm tra dữ liệu ----------
const cleanUsername = (s) => String(s ?? "").trim().toLowerCase();
function validate({ username, name, role, password }, { requirePassword = true } = {}) {
  if (username !== undefined && !/^[a-z0-9._-]{3,32}$/.test(username)) throw new Error("Tên đăng nhập 3–32 ký tự, chỉ gồm chữ không dấu, số, dấu chấm, gạch dưới, gạch ngang");
  if (name !== undefined && !String(name).trim()) throw new Error("Chưa nhập tên hiển thị");
  if (role !== undefined && !ROLES[role]) throw new Error("Vai trò không hợp lệ");
  if ((requirePassword || password) && String(password ?? "").length < 6) throw new Error("Mật khẩu cần ít nhất 6 ký tự");
}

// ---------- quản lý tài khoản ----------
export async function listUsers() {
  const [rows] = await db().query("SELECT username, name, role FROM tai_khoan ORDER BY tao_luc, username");
  return rows.map(publicUser);
}

// Những người có thể được giao số để gửi (nhân viên và quản trị)
export async function listAssignees() {
  return (await listUsers()).filter((u) => can(u, "send")).map(({ username, name, role }) => ({ username, name, role }));
}

export async function createUser({ username, name, role, password }) {
  username = cleanUsername(username);
  validate({ username, name, role, password });
  if (await findUser(username)) throw new Error(`Tên đăng nhập "${username}" đã tồn tại`);
  const { salt, hash } = hashPassword(password);
  await db().query("INSERT INTO tai_khoan (username, name, role, salt, hash) VALUES (?, ?, ?, ?, ?)", [username, String(name).trim(), role, salt, hash]);
  return publicUser({ username, name: String(name).trim(), role });
}

export async function setupFirstAdmin({ username, name, password }) {
  if (!(await needsSetup())) throw new Error("Đã có tài khoản quản trị");
  return createUser({ username, name, password, role: "admin" });
}

export async function updateUser(username, { name, role, password }) {
  const u = await findUser(username);
  if (!u) throw new Error("Không tìm thấy tài khoản");
  validate({ name, role, password }, { requirePassword: false });
  if (role && role !== "admin" && u.role === "admin" && (await adminCount()) === 1) throw new Error("Phải còn ít nhất một tài khoản quản trị");
  if (name !== undefined) u.name = String(name).trim();
  if (role) u.role = role;
  if (password) Object.assign(u, hashPassword(password));
  await db().query("UPDATE tai_khoan SET name = ?, role = ?, salt = ?, hash = ? WHERE username = ?", [u.name, u.role, u.salt, u.hash, username]);
  if (password) await db().query("DELETE FROM phien_dang_nhap WHERE username = ?", [username]);
  return publicUser(u);
}

export async function deleteUser(username, actor) {
  const u = await findUser(username);
  if (!u) throw new Error("Không tìm thấy tài khoản");
  if (u.username === actor.username) throw new Error("Không thể tự xoá tài khoản đang dùng");
  if (u.role === "admin" && (await adminCount()) === 1) throw new Error("Phải còn ít nhất một tài khoản quản trị");
  // Phiên đăng nhập của người này tự xoá theo (ON DELETE CASCADE)
  await db().query("DELETE FROM tai_khoan WHERE username = ?", [username]);
}

export async function changeOwnPassword(username, current, next) {
  const u = await findUser(username);
  if (!u || !checkPassword(u, current)) throw new Error("Mật khẩu hiện tại không đúng");
  return updateUser(username, { password: next });
}

// ---------- phiên đăng nhập ----------
const failures = new Map(); // ip -> { count, until }
const expiry = () => new Date(Date.now() + SESSION_HOURS * 3600000);

export async function login(ip, username, password) {
  const f = failures.get(ip);
  if (f && f.until > Date.now()) throw new Error(`Sai quá nhiều lần, thử lại sau ${Math.ceil((f.until - Date.now()) / 60000)} phút`);
  const u = await findUser(cleanUsername(username));
  if (!u || !checkPassword(u, password)) {
    // Hết thời gian khoá thì đếm lại từ đầu
    const count = (f?.until && f.until <= Date.now() ? 0 : f?.count ?? 0) + 1;
    failures.set(ip, { count, until: count >= 5 ? Date.now() + 5 * 60000 : 0 });
    throw new Error("Sai tên đăng nhập hoặc mật khẩu");
  }
  failures.delete(ip);
  const token = crypto.randomBytes(32).toString("hex");
  await db().query("DELETE FROM phien_dang_nhap WHERE het_han < NOW()");
  await db().query("INSERT INTO phien_dang_nhap (token, username, het_han) VALUES (?, ?, ?)", [token, u.username, expiry()]);
  return { token, user: publicUser(u) };
}

// Đọc lại tài khoản mỗi lần để đổi quyền / xoá tài khoản có hiệu lực ngay
export async function userFromRequest(req) {
  const token = tokenOf(req);
  if (!token) return null;
  const [rows] = await db().query(
    `SELECT t.username, t.name, t.role FROM phien_dang_nhap p JOIN tai_khoan t ON t.username = p.username
     WHERE p.token = ? AND p.het_han > NOW()`,
    [token],
  );
  if (!rows.length) return null;
  await db().query("UPDATE phien_dang_nhap SET het_han = ? WHERE token = ?", [expiry(), token]);
  return publicUser(rows[0]);
}

export async function logout(req) {
  const token = tokenOf(req);
  if (token) await db().query("DELETE FROM phien_dang_nhap WHERE token = ?", [token]);
}

function tokenOf(req) {
  const m = (req.headers.cookie || "").match(new RegExp(`(?:^|;\\s*)${COOKIE}=([a-f0-9]{64})`));
  return m?.[1] ?? null;
}
export const sessionCookie = (token) => `${COOKIE}=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_HOURS * 3600}`;
export const clearCookie = () => `${COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0`;
