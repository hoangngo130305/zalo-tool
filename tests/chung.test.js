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
test("processOne: khách chặn tin từ người lạ không tính là lỗi của Zalo", async () => {
  const loi = "Bạn chưa thể gửi tin nhắn đến người này vì người này chặn không nhận tin nhắn từ người lạ.";
  const r = await processOne(fakeApi({ sendMessage: async () => { throw new Error(loi); } }), { sdt: "0901234567" }, cfgNhanh);
  assert.equal(r.tinNhan, "Lỗi");
  assert.ok(!r.loiZalo);
  assert.match(r.ghiChu, /tự gửi lại/);
  const r2 = await processOne(fakeApi({ sendMessage: async () => { throw new Error("Tài khoản bị hạn chế"); } }), { sdt: "0901234567" }, cfgNhanh);
  assert.ok(r2.loiZalo);
});
test("processOne: số có Zalo không hợp lệ (bị khoá / huỷ) không tính là lỗi của Zalo", async () => {
  const r = await processOne(fakeApi({ findUser: async () => { throw Object.assign(new Error("User không hợp lệ"), { code: 1 }); } }),
    { sdt: "0901234567" }, cfgNhanh);
  assert.equal(r.tinNhan, "Lỗi");
  assert.ok(!r.loiZalo);
  assert.match(r.ghiChu, /mã 1/);
});
test("processOne: Zalo báo 'Không tìm thấy' (mã 212, khách chặn tìm qua SĐT) không tính là lỗi của Zalo", async () => {
  const r = await processOne(fakeApi({ findUser: async () => { throw Object.assign(new Error("Không tìm thấy"), { code: 212 }); } }),
    { sdt: "0901234567" }, cfgNhanh);
  assert.ok(!r.loiZalo);
  assert.match(r.ghiChu, /mã 212/);
});
test("processOne: phiên Zalo hết hiệu lực (mã 600) -> báo phienHetHan, không tính là lỗi của khách", async () => {
  const r = await processOne(fakeApi({ findUser: async () => { throw Object.assign(new Error("zpw_sek bị thiếu hoặc không đúng"), { code: 600 }); } }),
    { sdt: "0901234567" }, cfgNhanh);
  assert.ok(r.phienHetHan);
  assert.ok(!r.loiZalo);
});
test("processOne: Zalo giới hạn tìm số vẫn tính là lỗi của Zalo (để tự dừng)", async () => {
  const loi = "Tìm số điện thoại quá nhiều lần trong 1 giờ có thể bị xem là hoạt động bất thường. Bạn hãy thử lại vào 12:00.";
  const r = await processOne(fakeApi({ findUser: async () => { throw Object.assign(new Error(loi), { code: 221 }); } }),
    { sdt: "0901234567" }, cfgNhanh);
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

test("điền biến thêm {xungho} {sdt_nhanvien} cột Excel (trình duyệt)", () => {
  for (const [[tpl, ten, them], ra] of VD.dien_them) assert.equal(Chung.fill(tpl, ten, "", "", them), ra);
});

test("cảnh báo tin dài / dễ bị coi là spam (trình duyệt)", () => {
  for (const [[text, loai, tu], so] of VD.canh_bao) assert.equal(Chung.canhBao(text, loai, tu).length, so, text.slice(0, 30));
});

test("processOne: nhắc lại dùng Zalo ID có sẵn, không kết bạn lại", async () => {
  const goi = [];
  const api = fakeApi({ findUser: async () => { goi.push("tim"); return { uid: "x" }; },
    sendFriendRequest: async () => goi.push("ketban"), sendMessage: async (m, uid) => goi.push(`tin:${uid}`) });
  const cfg = sanitizeConfig({});
  const r = await processOne(api, { sdt: "0901234567", kieu: "nhac", zaloUid: "u99", noiDung: "Nhắc anh" }, cfg);
  assert.deepEqual(goi, ["tin:u99"]);
  assert.equal(r.tinNhan, "Đã gửi");
  assert.equal(r.zaloUid, "u99");
});
