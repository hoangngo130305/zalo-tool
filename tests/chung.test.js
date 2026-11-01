// Chạy: npm test (node --test tests/). Kiểm tra bản Node (src/core.js) và bản trình duyệt (chung.js) cho cùng
// kết quả với bộ ví dụ chung; bản Python được kiểm tra bằng cùng file trong web/guitin/tests.py.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { LoginQRCallbackEventType as QR } from "zca-js";
import { cacMau, fill, tenGoiNho, sanitizeConfig, ngoaiKhungGio, runBatch, processOne, loginWithQR } from "../src/core.js";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const VD = JSON.parse(fs.readFileSync(path.join(DIR, "vi-du-chung.json"), "utf8"));
const ctx = {};
vm.runInNewContext(fs.readFileSync(path.join(DIR, "..", "web", "guitin", "static", "guitin", "chung.js"), "utf8"), ctx);
const Chung = ctx.Chung;
const plain = (x) => JSON.parse(JSON.stringify(x));     // đối tượng tạo trong vm khác prototype

test("chuẩn hoá SĐT (trình duyệt)", () => {
  for (const [vao, ra] of VD.chuan_hoa_sdt) assert.equal(Chung.normalizePhone(vao), ra, vao);
});

test("tách mẫu --- (Node và trình duyệt)", () => {
  for (const [vao, ra] of VD.cac_mau) {
    assert.deepEqual(cacMau(vao), ra);
    assert.deepEqual(plain(Chung.cacMau(vao)), ra);
  }
});

test("điền {ten} {duan} {nhanvien} (Node và trình duyệt)", () => {
  for (const [[tpl, ten, duAn, nhanVien], ra] of VD.dien) {
    assert.equal(fill(tpl, { ten, duAn, nhanVien }), ra);
    assert.equal(Chung.fill(tpl, ten, duAn, nhanVien), ra);
  }
});

test("tên gợi nhớ (Node)", () => {
  for (const [[tpl, ten, sdt, duAn], ra] of VD.ten_goi_nho) assert.equal(tenGoiNho(tpl, { ten, sdt, duAn }), ra);
});

test("định nghĩa thành công (trình duyệt)", () => {
  for (const [[ketBan, tinNhan], ra] of VD.thanh_cong) assert.equal(Chung.thanhCong(ketBan, tinNhan), ra);
});

test("đọc dòng dán (trình duyệt)", () => {
  for (const [vao, ra] of VD.doc_dong_dan) assert.deepEqual(plain(Chung.docDongDan(vao)), ra, vao);
});

// ---------- khung giờ gửi (giờ Việt Nam) ----------
const vn = (s) => Date.parse(`${s}+07:00`);
test("khung giờ: trong giờ thì gửi ngay", () => {
  const cfg = sanitizeConfig({ gioBatDau: 480, gioKetThuc: 1200 });
  assert.equal(ngoaiKhungGio(cfg, vn("2026-11-02T09:30:00")).ms, 0);
});
test("khung giờ: sau 20:00 thì chờ đến 8:00 sáng mai", () => {
  const cfg = sanitizeConfig({ gioBatDau: 480, gioKetThuc: 1200 });
  const k = ngoaiKhungGio(cfg, vn("2026-11-02T21:00:00"));    // thứ Hai
  assert.equal(k.ms, 11 * 3600_000);
  assert.match(k.luc, /^08:00 thứ Ba 03\/11$/);
});
test("khung giờ: trước giờ mở thì chờ đến giờ mở hôm nay", () => {
  const cfg = sanitizeConfig({ gioBatDau: 480, gioKetThuc: 1200 });
  assert.equal(ngoaiKhungGio(cfg, vn("2026-11-02T07:15:00")).ms, 45 * 60_000);
});
test("khung giờ: nghỉ Chủ nhật thì thứ Bảy tối chờ đến sáng thứ Hai", () => {
  const cfg = sanitizeConfig({ gioBatDau: 480, gioKetThuc: 1200, nghiChuNhat: true });
  const k = ngoaiKhungGio(cfg, vn("2026-10-31T22:00:00"));    // thứ Bảy
  assert.match(k.luc, /^08:00 thứ Hai 02\/11$/);
  assert.ok(ngoaiKhungGio(cfg, vn("2026-11-01T10:00:00")).ms > 0, "Chủ nhật không gửi");
});

// ---------- gửi 1 người với Zalo giả ----------
const fakeApi = (over = {}) => ({
  findUser: async () => ({ uid: "u1", display_name: "Nam Zalo" }),
  sendFriendRequest: async () => {},
  changeFriendAlias: async () => {},
  sendMessage: async () => {},
  ...over,
});
const cfgNhanh = sanitizeConfig({ guiKetBan: false, doiTenGoiNho: false, loiMoiKetBan: "", noiDungTinNhan: "Chào {ten}" });

test("processOne: số chưa đăng ký Zalo không tính là lỗi của Zalo", async () => {
  const r = await processOne(fakeApi({ findUser: async () => ({}) }), { sdt: "0901234567", ten: "A" }, cfgNhanh);
  assert.equal(r.tinNhan, "Lỗi");
  assert.ok(!r.loiZalo);
});
test("processOne: Zalo báo lỗi tìm số thì đánh dấu loiZalo", async () => {
  const r = await processOne(fakeApi({ findUser: async () => { throw new Error("limit"); } }), { sdt: "0901234567" }, cfgNhanh);
  assert.ok(r.loiZalo);
});
test("processOne: lời mời có {duan} {nhanvien}", async () => {
  let loiMoi = "";
  const api = fakeApi({ sendFriendRequest: async (m) => { loiMoi = m; } });
  const cfg = sanitizeConfig({ guiTinNhan: false, doiTenGoiNho: false });
  await processOne(api, { sdt: "0901234567", ten: "Nam", duAn: "Palm City", nhanVien: "Lan", loiMoi: "Chào {ten}, {nhanvien} - {duan}" }, cfg);
  assert.equal(loiMoi, "Chào Nam, Lan - Palm City");
});

test("runBatch: dừng khi được yêu cầu, không chờ ngoài giờ khi khung giờ cả ngày", async () => {
  const cfg = sanitizeConfig({ gioBatDau: 0, gioKetThuc: 1440, choToiThieuGiay: 30 });
  let stop = false;
  const sent = [];
  const r = await runBatch({
    api: {}, cfg, batch: [1, 2, 3], task: async (_, x) => { sent.push(x); if (x === 2) stop = true; return {}; },
    shouldStop: () => stop, wait: async () => {},
  });
  assert.deepEqual(sent, [1, 2]);
  assert.equal(r.stopped, true);
});

test("mã QR hết hạn: chỉ tạo mã mới tối đa 3 lần rồi dừng (không lặp mãi)", async () => {
  let taoMa = 0;
  const su_kien = [];
  const zalo = {
    loginQR: async (_, cb) => {
      let dung = false;
      const actions = { retry: () => {}, abort: () => { dung = true; } };
      while (!dung && taoMa < 20) {
        taoMa++;
        await cb({ type: QR.QRCodeGenerated, data: { image: "x" }, actions });
        await cb({ type: QR.QRCodeExpired, actions });
      }
      return null;
    },
  };
  const api = await loginWithQR(zalo, 1, (ev) => su_kien.push(ev.type));
  assert.equal(api, null);
  assert.equal(taoMa, 4);                     // mã đầu + 3 lần làm mới
  assert.equal(su_kien.filter((t) => t === "expired").length, 3);
  assert.equal(su_kien.at(-1), "gaveup");
});
