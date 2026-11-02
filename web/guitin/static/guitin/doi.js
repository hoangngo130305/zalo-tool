// Trang “Đội của tôi” của trưởng nhóm: xem số của cả đội, giao lại cho thành viên, báo cáo của đội.
const $ = (id) => document.getElementById(id);
const { veBieuDo } = window.Chung;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const csrfToken = () => document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] || "";
let d = { thanhVien: [], thongKe: [], rows: [] };
const chon = new Set();

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) location.href = "/dang-nhap/?next=/doi/";
  if (!res.ok) throw new Error(data.error || "Có lỗi xảy ra");
  return data;
}
function bao(text, loi = false) {
  $("saved").textContent = text;
  $("saved").style.color = loi ? "var(--bad)" : "";
  clearTimeout(bao.t);
  bao.t = setTimeout(() => ($("saved").textContent = ""), 4000);
}

const TT = { cho: ["todo", "Chờ gửi"], ok: ["ok", "Đã gửi"], loi: ["bad", "Lỗi"], tu_choi: ["bad", "Khách từ chối"], so_sai: ["bad", "Số sai"] };
function render() {
  const ten = Object.fromEntries(d.thanhVien.map((t) => [t.id, t.ten]));
  $("tvBody").innerHTML = d.thongKe.filter((g) => g.id).map((g) => `<tr><td>${esc(g.ten)}</td><td class="n">${g.tong}</td><td class="n">${g.cho}</td><td class="n">${g.da_gui}</td><td class="n">${g.loi}</td></tr>`).join("");
  const nguoi = $("locNguoi").value, tt = $("locTt").value, q = $("tim").value.trim().toLowerCase();
  const rows = d.rows.filter((r) => (!nguoi || String(r.phuTrach) === nguoi) && (!tt || r.trangThai === tt)
    && (!q || `${r.sdt} ${r.ten}`.toLowerCase().includes(q)));
  $("body").innerHTML = rows.slice(0, 1000).map((r) => {
    const [cls, txt] = TT[r.trangThai] || ["todo", r.trangThai];
    return `<tr><td><input type="checkbox" data-id="${r.id}" ${chon.has(r.id) ? "checked" : ""}></td><td>${esc(r.sdt)}</td><td>${esc(r.ten)}</td>
      <td>${esc(r.duAn)}</td><td>${esc(r.chienDich || "Chung")}</td><td>${esc(ten[r.phuTrach] || "")}</td><td><span class="pill ${cls}">${txt}</span></td></tr>`;
  }).join("");
  $("empty").classList.toggle("hidden", rows.length > 0);
  $("soChon").textContent = `Đã chọn ${chon.size}` + (rows.length > 1000 ? ` · đang hiện 1000/${rows.length} số, lọc để thu hẹp` : "");
}
async function load(body) {
  try {
    d = await api("/api/doi", body);
    const opts = d.thanhVien.map((t) => `<option value="${t.id}">${esc(t.ten)} (${esc(t.vaiTro)})</option>`).join("");
    const giu = $("locNguoi").value;
    $("locNguoi").innerHTML = `<option value="">Tất cả thành viên</option>${opts}`;
    $("locNguoi").value = giu;
    $("giaoCho").innerHTML = `<option value="">Giao các số đã chọn cho...</option>${opts}`;
    render();
    return true;
  } catch (e) { bao(e.message, true); return false; }
}
$("body").addEventListener("change", (e) => {
  const id = Number(e.target.dataset.id);
  if (!id) return;
  if (e.target.checked) chon.add(id); else chon.delete(id);
  render();
});
$("chonHet").onchange = () => {
  const bat = $("chonHet").checked;
  for (const x of document.querySelectorAll("#body input[data-id]")) {
    if (bat) chon.add(Number(x.dataset.id)); else chon.delete(Number(x.dataset.id));
  }
  render();
};
["locNguoi", "locTt"].forEach((id) => ($(id).onchange = render));
$("tim").oninput = render;
$("btnGiao").onclick = async () => {
  const nguoi = $("giaoCho").value;
  if (!nguoi || !chon.size) return bao("Chọn số và người nhận giao.", true);
  if (await load({ ids: [...chon], nguoi: Number(nguoi) })) { bao(`Đã giao ${chon.size} số.`); chon.clear(); render(); }
};
$("btnChia").onclick = async () => {
  if (!confirm("Chia đều các số chưa gửi đang giao cho bạn cho các nhân viên trong đội?")) return;
  if (await load({ chiaDeu: true })) bao("Đã chia đều.");
};

async function loadBaoCao() {
  try {
    const q = new URLSearchParams({ tu: $("bcTu").value, den: $("bcDen").value });
    const r = await api(`/api/bao-cao?${q}`);
    $("bcTu").value = r.tu;
    $("bcDen").value = r.den;
    const t = r.tong;
    $("bcTong").innerHTML = `<span>Đã gửi: <b>${t.gui}</b></span><span style="color:var(--ok)">Thành công: <b>${t.thanhCong}</b></span>`
      + `<span>Đồng ý kết bạn: <b>${t.dongY}</b></span><span>Trả lời: <b>${t.traLoi}</b></span>`;
    veBieuDo($("bcChart"), r.theoNgay);
    $("bcNv").innerHTML = r.nhanVien.map((n) => `<tr><td>${esc(n.ten)}</td><td class="n">${n.gui}</td><td class="n">${n.thanhCong}</td><td class="n">${n.dongY}</td><td class="n">${n.traLoi}</td></tr>`).join("")
      || `<tr><td colspan="5" class="note">Chưa có dữ liệu.</td></tr>`;
  } catch (e) { bao(e.message, true); }
}
$("bcForm").onsubmit = (e) => { e.preventDefault(); loadBaoCao(); };
load();
loadBaoCao();
