// Hàm dùng chung cho các trang (chạy trên trình duyệt). Phải cho cùng kết quả với bản Python (models.py, services.py)
// và Node (src/core.js): tests/chung.test.js chạy cả ba bản trên cùng bộ ví dụ trong tests/vi-du-chung.json.
(function (g) {
  // Đầu số di động 11 số cũ -> 10 số mới (giống chuan_hoa_sdt ở server)
  const DAU_SO_CU = {
    "0120": "070", "0121": "079", "0122": "077", "0126": "076", "0128": "078",
    "0123": "083", "0124": "084", "0125": "085", "0127": "081", "0129": "082",
    "0162": "032", "0163": "033", "0164": "034", "0165": "035", "0166": "036", "0167": "037", "0168": "038", "0169": "039",
    "0186": "056", "0188": "058", "0199": "059",
  };
  function normalizePhone(raw) {
    let p = String(raw ?? "").replace(/[^\d+]/g, "");
    if (p.startsWith("+84")) p = "0" + p.slice(3);
    else if (p.startsWith("84") && (p.length === 11 || p.length === 12)) p = "0" + p.slice(2);
    else if ((p.length === 9 || p.length === 10) && !p.startsWith("0")) p = "0" + p;
    if (p.length === 11 && DAU_SO_CU[p.slice(0, 4)]) p = DAU_SO_CU[p.slice(0, 4)] + p.slice(4);
    return /^0\d{9}$/.test(p) ? p : null;
  }
  // Nhiều mẫu cách nhau bằng dòng "---": mỗi khách nhận ngẫu nhiên 1 mẫu (worker chọn khi gửi)
  const cacMau = (t) => String(t ?? "").split(/^\s*-{3,}\s*$/m).map((s) => s.trim()).filter(Boolean);
  // {ten} -> tên (trống thì "bạn"), {duan} -> dự án, {nhanvien} -> nhân viên phụ trách
  const fill = (tpl, ten, duAn = "", nhanVien = "") => String(tpl ?? "")
    .replace(/\{ten\}/gi, () => ten || "bạn")
    .replace(/\{duan\}/gi, () => duAn || "")
    .replace(/\{nhanvien\}/gi, () => nhanVien || "")
    .trim();
  // Gửi thành công = tin nhắn đã gửi, hoặc tắt gửi tin ("-") mà lời mời kết bạn đã gửi (giống thanh_cong() ở Django)
  const thanhCong = (ketBan, tinNhan) => tinNhan === "Đã gửi" || (tinNhan === "-" && ketBan === "Đã gửi");
  // 1 dòng dán vào: "SĐT, Tên, Nội dung" hoặc 2–4 cột copy từ Excel (Tab: SĐT, Tên, Nội dung, Dự án).
  // Giống doc_van_ban() ở Django. Trả về null nếu dòng không bắt đầu bằng số điện thoại.
  function docDongDan(line) {
    if (!String(line ?? "").trim()) return null;
    let parts;
    if (line.includes("\t")) {
      parts = line.split("\t");
      if (parts.length > 4) parts = [parts[0], parts[1], parts.slice(2, -1).join("\t"), parts[parts.length - 1]];
    } else {
      // Tách tối đa 3 phần bằng , ; | (phần cuối giữ nguyên dấu phẩy của nội dung)
      parts = [];
      let rest = line.trim();
      while (parts.length < 2) {
        const m = /\s*[,;|]\s*/.exec(rest);
        if (!m) break;
        parts.push(rest.slice(0, m.index));
        rest = rest.slice(m.index + m[0].length);
      }
      parts.push(rest);
    }
    parts = [...parts.map((p) => p.trim()), "", "", "", ""];
    const sdt = parts[0].replace(/[\s.\-]/g, "");
    return /^\+?\d{8,12}$/.test(sdt) ? { sdt, ten: parts[1], noiDung: parts[2], duAn: parts[3] } : null;
  }
  g.Chung = { DAU_SO_CU, normalizePhone, cacMau, fill, thanhCong, docDongDan };
})(typeof window !== "undefined" ? window : globalThis);
