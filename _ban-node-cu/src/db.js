// Lưu trữ bằng MySQL / MariaDB (XAMPP). Tự tạo database, bảng, và chuyển dữ liệu cũ từ file sang.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import mysql from "mysql2/promise";
import XLSX from "xlsx";

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_FILE = path.join(ROOT, "config.json");

let pool = null;
export const db = () => {
  if (!pool) throw new Error("Chưa kết nối database");
  return pool;
};

// ---------- kết nối ----------
const DEFAULT_MYSQL = { host: "127.0.0.1", port: 3306, user: "root", password: "", database: "zalo_tool" };
export const readConfigFile = () => JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));

export async function connect() {
  if (pool) return pool;
  const file = readConfigFile();
  const cfg = { ...DEFAULT_MYSQL, ...(file.mysql || {}) };
  if (!/^[A-Za-z0-9_]+$/.test(cfg.database)) throw new Error("Tên database chỉ gồm chữ không dấu, số, gạch dưới");

  try {
    const admin = await mysql.createConnection({ host: cfg.host, port: cfg.port, user: cfg.user, password: cfg.password });
    await admin.query(`CREATE DATABASE IF NOT EXISTS \`${cfg.database}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`);
    await admin.end();
  } catch (e) {
    const hint = e.code === "ECONNREFUSED"
      ? "MySQL chưa chạy. Mở XAMPP Control Panel và bấm Start ở dòng MySQL."
      : e.code === "ER_ACCESS_DENIED_ERROR" ? "Sai user hoặc mật khẩu MySQL, sửa mục \"mysql\" trong config.json." : e.message;
    throw new Error(`Không kết nối được MySQL (${cfg.host}:${cfg.port}): ${hint}`);
  }

  pool = mysql.createPool({ ...cfg, charset: "utf8mb4", dateStrings: true, connectionLimit: 5 });
  await createTables();
  await migrateFromFiles(file);
  return pool;
}

export async function close() {
  await pool?.end();
  pool = null;
}

async function createTables() {
  const q = (sql) => pool.query(sql);
  await q(`CREATE TABLE IF NOT EXISTS cai_dat (
    khoa VARCHAR(64) PRIMARY KEY,
    gia_tri LONGTEXT NOT NULL
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);
  await q(`CREATE TABLE IF NOT EXISTS nguoi_nhan (
    id INT AUTO_INCREMENT PRIMARY KEY,
    thu_tu INT NOT NULL,
    sdt_goc VARCHAR(32) NOT NULL DEFAULT '',
    sdt CHAR(10) NULL COMMENT 'SĐT đã chuẩn hoá, NULL nếu sai',
    ten VARCHAR(255) NOT NULL DEFAULT '',
    tao_luc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX (sdt), INDEX (thu_tu)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);
  await q(`CREATE TABLE IF NOT EXISTS ket_qua (
    sdt CHAR(10) PRIMARY KEY,
    ten VARCHAR(255) NOT NULL DEFAULT '',
    ten_zalo VARCHAR(255) NULL,
    ket_ban VARCHAR(20) NOT NULL,
    tin_nhan VARCHAR(20) NOT NULL,
    ghi_chu TEXT NULL,
    thoi_gian DATETIME NOT NULL,
    nguoi_gui VARCHAR(32) NULL COMMENT 'tài khoản Zalo Tool đã bấm gửi'
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);
  await q(`CREATE TABLE IF NOT EXISTS tai_khoan (
    username VARCHAR(32) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(16) NOT NULL,
    salt CHAR(32) NOT NULL,
    hash CHAR(128) NOT NULL,
    tao_luc DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);
  await q(`CREATE TABLE IF NOT EXISTS phien_dang_nhap (
    token CHAR(64) PRIMARY KEY,
    username VARCHAR(32) NOT NULL,
    het_han DATETIME NOT NULL,
    INDEX (username),
    FOREIGN KEY (username) REFERENCES tai_khoan(username) ON DELETE CASCADE ON UPDATE CASCADE
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);
  await q(`CREATE TABLE IF NOT EXISTS nhat_ky (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    thoi_gian DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    muc VARCHAR(10) NOT NULL DEFAULT 'info',
    noi_dung TEXT NOT NULL,
    INDEX (thoi_gian)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`);

  // Nhân viên phụ trách từng số (thêm vào bảng cũ nếu chưa có). Xoá tài khoản thì số trở về "chưa giao".
  await q("ALTER TABLE nguoi_nhan ADD COLUMN IF NOT EXISTS phu_trach VARCHAR(32) NULL COMMENT 'username nhân viên phụ trách' AFTER ten");
  const [fk] = await q(`SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'nguoi_nhan' AND CONSTRAINT_NAME = 'fk_nguoi_nhan_phu_trach'`);
  if (!fk.length) {
    await q(`ALTER TABLE nguoi_nhan ADD CONSTRAINT fk_nguoi_nhan_phu_trach
      FOREIGN KEY (phu_trach) REFERENCES tai_khoan(username) ON DELETE SET NULL ON UPDATE CASCADE`);
  }
}

// ---------- thời gian ----------
// DB lưu "2026-10-10 14:20:24", giao diện & báo cáo hiển thị "14:20:24 10/10/2026"
export function toViTime(s) {
  const m = String(s ?? "").match(/^(\d{4})-(\d{2})-(\d{2}) (\d{2}:\d{2}:\d{2})/);
  return m ? `${m[4]} ${m[3]}/${m[2]}/${m[1]}` : String(s ?? "");
}
function fromViTime(s) {
  const m = String(s ?? "").match(/(\d{1,2}):(\d{2}):(\d{2})\s+(\d{1,2})\/(\d{1,2})\/(\d{4})/);
  if (!m) return new Date();
  return new Date(+m[6], +m[5] - 1, +m[4], +m[1], +m[2], +m[3]);
}

// ---------- cài đặt ----------
export async function getSetting(key) {
  const [rows] = await db().query("SELECT gia_tri FROM cai_dat WHERE khoa = ?", [key]);
  return rows.length ? JSON.parse(rows[0].gia_tri) : undefined;
}
export async function setSetting(key, value) {
  await db().query(
    "INSERT INTO cai_dat (khoa, gia_tri) VALUES (?, ?) ON DUPLICATE KEY UPDATE gia_tri = VALUES(gia_tri)",
    [key, JSON.stringify(value)],
  );
}
export async function deleteSetting(key) {
  await db().query("DELETE FROM cai_dat WHERE khoa = ?", [key]);
}

// ---------- người nhận ----------
// owner: undefined = tất cả, null = chưa giao cho ai, "username" = của nhân viên đó
function ownerFilter(owner) {
  if (owner === undefined) return { where: "", params: [] };
  if (owner === null) return { where: "WHERE n.phu_trach IS NULL", params: [] };
  return { where: "WHERE n.phu_trach = ?", params: [owner] };
}

export async function listContacts(owner) {
  const f = ownerFilter(owner);
  const [rows] = await db().query(
    `SELECT n.id, n.sdt_goc, n.sdt, n.ten, n.phu_trach, t.name AS phu_trach_ten
     FROM nguoi_nhan n LEFT JOIN tai_khoan t ON t.username = n.phu_trach
     ${f.where} ORDER BY n.thu_tu, n.id`,
    f.params,
  );
  return rows.map((r, i) => ({
    dong: i + 1, sdtGoc: r.sdt_goc, sdt: r.sdt, ten: r.ten, phuTrach: r.phu_trach, phuTrachTen: r.phu_trach_ten,
  }));
}

// Thay danh sách (giữ đúng thứ tự trên giao diện).
// Có owner: chỉ thay phần của nhân viên đó, các số khác giữ nguyên. Không có owner: thay toàn bộ.
export async function replaceContacts(rows, { owner } = {}) {
  const conn = await db().getConnection();
  try {
    await conn.beginTransaction();
    let base = 0;
    if (owner) {
      await conn.query("DELETE FROM nguoi_nhan WHERE phu_trach = ?", [owner]);
      const [[m]] = await conn.query("SELECT COALESCE(MAX(thu_tu), 0) AS m FROM nguoi_nhan");
      base = Number(m.m);
    } else {
      await conn.query("DELETE FROM nguoi_nhan");
    }
    if (rows.length) {
      await conn.query(
        "INSERT INTO nguoi_nhan (thu_tu, sdt_goc, sdt, ten, phu_trach) VALUES ?",
        [rows.map((r, i) => [
          base + i + 1, String(r.sdtGoc ?? "").slice(0, 32), r.sdt, String(r.ten ?? "").slice(0, 255),
          owner || r.phuTrach || null,
        ])],
      );
    }
    await conn.commit();
  } catch (e) {
    await conn.rollback();
    throw e;
  } finally {
    conn.release();
  }
}

// ---------- kết quả gửi ----------
const resultFromRow = (r) => ({
  ten: r.ten, tenZalo: r.ten_zalo || undefined, ketBan: r.ket_ban, tinNhan: r.tin_nhan,
  ghiChu: r.ghi_chu || undefined, thoiGian: toViTime(r.thoi_gian), nguoiGui: r.nguoi_gui || undefined,
});

export async function getResults() {
  const [rows] = await db().query("SELECT * FROM ket_qua ORDER BY thoi_gian");
  return Object.fromEntries(rows.map((r) => [r.sdt, resultFromRow(r)]));
}

export async function saveResult(sdt, res) {
  await db().query(
    `INSERT INTO ket_qua (sdt, ten, ten_zalo, ket_ban, tin_nhan, ghi_chu, thoi_gian, nguoi_gui) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
     ON DUPLICATE KEY UPDATE ten = VALUES(ten), ten_zalo = VALUES(ten_zalo), ket_ban = VALUES(ket_ban), tin_nhan = VALUES(tin_nhan),
       ghi_chu = VALUES(ghi_chu), thoi_gian = VALUES(thoi_gian), nguoi_gui = VALUES(nguoi_gui)`,
    [sdt, res.ten || "", res.tenZalo || null, res.ketBan, res.tinNhan, res.ghiChu || null, res.thoiGianDate || fromViTime(res.thoiGian), res.nguoiGui || null],
  );
}

export async function deleteResult(sdt) {
  await db().query("DELETE FROM ket_qua WHERE sdt = ?", [sdt]);
}
export async function clearResults() {
  await db().query("DELETE FROM ket_qua");
}

// Báo cáo Excel tạo trực tiếp từ database
export async function reportBuffer() {
  const done = await getResults();
  const rows = Object.entries(done).map(([sdt, d]) => ({
    "SĐT": sdt,
    "Tên trong danh sách": d.ten,
    "Tên Zalo": d.tenZalo || "",
    "Kết bạn": d.ketBan,
    "Tin nhắn": d.tinNhan,
    "Ghi chú": d.ghiChu || "",
    "Thời gian": d.thoiGian,
    "Người bấm gửi": d.nguoiGui || "",
  }));
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.json_to_sheet(rows.length ? rows : [{ "SĐT": "", "Tên trong danh sách": "", "Tên Zalo": "", "Kết bạn": "", "Tin nhắn": "", "Ghi chú": "", "Thời gian": "", "Người bấm gửi": "" }]);
  ws["!cols"] = [{ wch: 13 }, { wch: 22 }, { wch: 22 }, { wch: 12 }, { wch: 12 }, { wch: 40 }, { wch: 20 }, { wch: 16 }];
  XLSX.utils.book_append_sheet(wb, ws, "KetQua");
  return XLSX.write(wb, { type: "buffer", bookType: "xlsx" });
}

// ---------- nhật ký ----------
export async function addLogRow({ level, text }) {
  await db().query("INSERT INTO nhat_ky (muc, noi_dung) VALUES (?, ?)", [level, text]);
}
export async function recentLogs(limit = 300) {
  const [rows] = await db().query("SELECT thoi_gian, muc, noi_dung FROM nhat_ky ORDER BY id DESC LIMIT ?", [limit]);
  return rows.reverse().map((r) => ({ t: toViTime(r.thoi_gian), text: r.noi_dung, level: r.muc }));
}

// ---------- chuyển dữ liệu cũ từ file sang MySQL (chạy 1 lần) ----------
const OLD_DIR = path.join(ROOT, "data", "da-chuyen-sang-mysql");
const SETTING_KEYS = ["loiMoiKetBan", "noiDungTinNhan", "guiKetBan", "guiTinNhan", "gioiHanMoiLan", "choToiThieuGiay", "choToiDaGiay"];

async function count(table) {
  const [[r]] = await pool.query(`SELECT COUNT(*) AS n FROM ${table}`);
  return Number(r.n);
}
function moveToOld(file) {
  if (!fs.existsSync(file)) return;
  fs.mkdirSync(OLD_DIR, { recursive: true });
  fs.renameSync(file, path.join(OLD_DIR, path.basename(file)));
}

async function migrateFromFiles(file) {
  const moved = [];

  // Nội dung tin nhắn & cài đặt
  if ((await getSetting("cauHinh")) === undefined) {
    const old = Object.fromEntries(SETTING_KEYS.filter((k) => k in file).map((k) => [k, file[k]]));
    await setSetting("cauHinh", old);
    if (Object.keys(old).length) console.log("Đã chuyển nội dung tin nhắn & cài đặt từ config.json vào MySQL.");
  }

  // Danh sách người nhận
  const listFile = path.join(ROOT, file.fileExcel || "data/danh-sach.xlsx");
  if (fs.existsSync(listFile) && (await count("nguoi_nhan")) === 0) {
    const { rowsFromWorkbook } = await import("./core.js");
    const rows = rowsFromWorkbook(XLSX.readFile(listFile));
    await replaceContacts(rows);
    console.log(`Đã chuyển ${rows.length} người nhận từ ${path.basename(listFile)} vào MySQL.`);
    moved.push(listFile);
  }

  // Kết quả đã gửi
  const doneFile = path.join(ROOT, "data", "da-xu-ly.json");
  if (fs.existsSync(doneFile) && (await count("ket_qua")) === 0) {
    const done = JSON.parse(fs.readFileSync(doneFile, "utf8"));
    for (const [sdt, res] of Object.entries(done)) await saveResult(sdt, res);
    console.log(`Đã chuyển ${Object.keys(done).length} kết quả gửi từ da-xu-ly.json vào MySQL.`);
    moved.push(doneFile, path.join(ROOT, file.fileKetQua || "data/ket-qua.xlsx"));
  }

  // Tài khoản người dùng
  const usersFile = path.join(ROOT, "data", "tai-khoan.json");
  if (fs.existsSync(usersFile) && (await count("tai_khoan")) === 0) {
    const users = JSON.parse(fs.readFileSync(usersFile, "utf8"));
    for (const u of users) {
      await pool.query("INSERT INTO tai_khoan (username, name, role, salt, hash) VALUES (?, ?, ?, ?, ?)", [u.username, u.name, u.role, u.salt, u.hash]);
    }
    console.log(`Đã chuyển ${users.length} tài khoản từ tai-khoan.json vào MySQL.`);
    moved.push(usersFile);
  }

  // Phiên đăng nhập Zalo
  const sessionFile = path.join(ROOT, "session.json");
  if (fs.existsSync(sessionFile) && (await getSetting("zaloSession")) === undefined) {
    await setSetting("zaloSession", JSON.parse(fs.readFileSync(sessionFile, "utf8")));
    console.log("Đã chuyển phiên đăng nhập Zalo từ session.json vào MySQL.");
    moved.push(sessionFile);
  }

  // Cất file cũ đi để không ai sửa nhầm (không xoá)
  const legacyKeys = [...SETTING_KEYS, "fileExcel", "fileKetQua", "chayThu"];
  if (moved.length || legacyKeys.some((k) => k in file)) {
    fs.mkdirSync(OLD_DIR, { recursive: true });
    if (legacyKeys.some((k) => k in file)) {
      fs.copyFileSync(CONFIG_FILE, path.join(OLD_DIR, "config.json"));
      const slim = { mysql: { ...DEFAULT_MYSQL, ...(file.mysql || {}) }, moChoMangNoiBo: file.moChoMangNoiBo === true };
      fs.writeFileSync(CONFIG_FILE, JSON.stringify(slim, null, 2) + "\n");
    }
    moved.forEach(moveToOld);
    console.log(`File cũ đã được cất vào ${path.relative(ROOT, OLD_DIR)} (không còn dùng nữa).`);
  }
}
