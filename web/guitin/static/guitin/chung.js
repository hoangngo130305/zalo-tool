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
  // Tên biến / tên cột viết liền, không dấu, chữ thường: "Căn hộ" -> "canho" (giống khoa_cot ở Django)
  const khoaCot = (s) => String(s ?? "").toLowerCase().normalize("NFD").replace(/đ/g, "d")
    .replace(/[̀-ͯ]/g, "").replace(/[^\p{L}\p{N}]/gu, "");
  // {ten} -> tên (trống thì "bạn"), {duan} -> dự án, {nhanvien} -> nhân viên phụ trách,
  // them: các biến khác {xungho, sdt_nhanvien, cột thêm trong file Excel}; biến không có thì giữ nguyên
  function fill(tpl, ten, duAn = "", nhanVien = "", them = null) {
    let s = String(tpl ?? "")
      .replace(/\{ten\}/gi, () => ten || "bạn")
      .replace(/\{duan\}/gi, () => duAn || "")
      .replace(/\{nhanvien\}/gi, () => nhanVien || "");
    if (them) {
      const bien = Object.fromEntries(Object.entries(them).map(([k, v]) => [khoaCot(k), String(v)]));
      s = s.replace(/\{(\w+)\}/g, (m, k) => bien[khoaCot(k)] ?? m);
    }
    return s.trim();
  }
  // Cảnh báo (không chặn) khi tin quá dài / có cụm từ dễ bị coi là spam. Giống canh_bao_noi_dung() ở Django.
  function canhBao(text, loai = "tin_nhan", tuSpam = []) {
    const out = [];
    const toiDa = loai === "loi_moi" ? 150 : 1000;
    const mau = cacMau(text);
    mau.forEach((m, i) => {
      const ten = mau.length > 1 ? `Mẫu ${i + 1}: ` : "";
      if (m.length > toiDa) out.push(`${ten}dài ${m.length} ký tự (nên dưới ${toiDa})`);
      const thap = m.toLowerCase();
      const cum = (tuSpam || []).filter((t) => t && thap.includes(t));
      if (cum.length) out.push(`${ten}có cụm từ dễ bị coi là spam: ${cum.join(", ")}`);
      if ((thap.match(/https?:\/\/|www\./g) || []).length > 1) out.push(`${ten}có nhiều đường link`);
      if ((m.match(/!/g) || []).length >= 4) out.push(`${ten}có nhiều dấu chấm than`);
    });
    return out;
  }
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
  // Biểu đồ cột theo ngày (SVG, không cần thư viện): số người đã gửi, thành công, đồng ý kết bạn, trả lời
  function veBieuDo(el, ngay) {
    const W = 760, H = 200, L = 34, B = 26;
    const max = Math.max(1, ...ngay.map((d) => d.gui));
    const bw = (W - L) / Math.max(1, ngay.length);
    const y = (v) => H - B - (v / max) * (H - B - 10);
    const cot = ngay.map((d, i) => {
      const x = L + i * bw;
      const nhan = ngay.length <= 16 || i % Math.ceil(ngay.length / 12) === 0 ? `<text x="${x + bw / 2}" y="${H - 8}" class="ax" text-anchor="middle">${d.ngay.slice(8, 10)}/${d.ngay.slice(5, 7)}</text>` : "";
      return `<g><title>${d.ngay.split("-").reverse().join("/")}: gửi ${d.gui}, thành công ${d.thanhCong}, đồng ý kết bạn ${d.dongY}, trả lời ${d.traLoi}</title>
        <rect x="${x + bw * 0.15}" y="${y(d.gui)}" width="${bw * 0.7}" height="${H - B - y(d.gui)}" class="b-gui"/>
        <rect x="${x + bw * 0.15}" y="${y(d.thanhCong)}" width="${bw * 0.7}" height="${H - B - y(d.thanhCong)}" class="b-ok"/>
        <circle cx="${x + bw / 2}" cy="${y(d.dongY)}" r="${d.dongY ? 3 : 0}" class="b-dy"/>${nhan}</g>`;
    }).join("");
    const truc = [0, max / 2, max].map((v) => `<text x="${L - 4}" y="${y(v) + 4}" class="ax" text-anchor="end">${Math.round(v)}</text><line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" class="grid"/>`).join("");
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Số người đã gửi theo ngày">${truc}${cot}</svg>
      <div class="legend"><span><i class="b-gui"></i>Đã gửi</span><span><i class="b-ok"></i>Thành công</span><span><i class="b-dy"></i>Đồng ý kết bạn</span></div>`;
  }
  g.Chung = { DAU_SO_CU, normalizePhone, cacMau, fill, thanhCong, docDongDan, canhBao, khoaCot, veBieuDo };
})(typeof window !== "undefined" ? window : globalThis);
