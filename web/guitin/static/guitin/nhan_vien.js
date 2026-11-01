// Trang của nhân viên: nhập danh sách (SĐT, họ tên, nội dung), gửi tự động, xem lịch sử & xuất Excel.
const $ = (id) => document.getElementById(id);
const me = JSON.parse($("me-data").textContent);
let contacts = [];        // [{ id, sdt, ten, noiDung, duAn, khoa, tuChoi }] các số của mình (id trống = dòng mới)
let doneMap = {};         // sdtChuan -> kết quả mới nhất
let config = {};
let myDefault = "";       // tin nhắn mặc định của tôi (để trống = dùng nội dung của quản trị)
let myInvite = "";        // lời mời kết bạn của tôi (để trống = dùng lời mời của quản trị)
let myImages = [];        // ảnh gửi kèm tin nhắn của tôi (trống = dùng ảnh của quản trị)
let st = {};              // trạng thái gửi (từ server)
let version = null;       // phiên bản danh sách trên server
let lastLogId = 0;
let listDirty = false;    // có thay đổi chưa lưu -> không tải đè
let hanMuc = null;        // { daGui, toiDa, tamDungDen } hạn mức hôm nay của Zalo của tôi

// ---------- tiện ích ----------
// normalizePhone, cacMau, fill, thanhCong, docDongDan: dùng chung với server (chung.js)
const { normalizePhone, cacMau, fill, thanhCong, docDongDan } = window.Chung;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const csrfToken = () => document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] || "";
const now = () => new Date().toLocaleTimeString("vi-VN");
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function flash(id, text) { $(id).textContent = text; clearTimeout(flash[id]); flash[id] = setTimeout(() => ($(id).textContent = ""), 2500); }
const showError = (e) => addLog({ t: now(), text: e.message || String(e), level: "error" });
const info = (text, level = "ok") => addLog({ t: now(), text, level });

async function api(path, body, opts = {}) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body instanceof FormData ? { "X-CSRFToken": csrfToken() }
      : { "Content-Type": opts.raw ? "application/octet-stream" : "application/json", "X-CSRFToken": csrfToken() },
    body: opts.raw || body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) location.href = "/dang-nhap/?next=/";
  if (!res.ok) throw new Error(data.error || "Có lỗi xảy ra");
  return data;
}

// Nội dung tin nhắn thực tế sẽ gửi cho 1 người:
// nội dung riêng của dòng -> tin nhắn của tôi -> nội dung của quản trị
const baseMsg = () => myDefault || config.noiDungTinNhan || "";
const msgFor = (c) => fill(c.noiDung || baseMsg(), c.ten, c.duAn || config.tenDuAn, me.name);

// ---------- trạng thái từng số ----------
function statusOf(c) {
  const p = normalizePhone(c.sdt);
  if (!String(c.sdt).trim()) return { cls: "warn", text: "Thiếu số" };
  if (!p) return { cls: "bad", text: "Số sai" };
  if (st.current === p) return { cls: "run", text: "Đang gửi" };
  const r = doneMap[p];
  if (r) {
    if (!thanhCong(r.ketBan, r.tinNhan)) return { cls: "bad", text: "Lỗi", note: r.ghiChu };
    return { cls: "ok", text: "Đã gửi", note: [r.tenZalo && `Zalo: ${r.tenZalo}`, r.thoiGian].filter(Boolean).join(" · ") };
  }
  if (c.tuChoi) return { cls: "bad", text: "Khách từ chối", note: "Không gửi cho số này nữa" };
  if (config.guiTinNhan && !msgFor(c)) return { cls: "warn", text: "Thiếu nội dung" };
  return { cls: "todo", text: "Chờ gửi" };
}
const statusCell = (s) => `<span class="pill ${s.cls}">${s.text}</span>${s.note ? `<span class="note" title="${esc(s.note)}">${esc(s.note)}</span>` : ""}`;
function pendingList() {
  const seen = new Set();
  return contacts.filter((c) => {
    const p = normalizePhone(c.sdt);
    if (!p || doneMap[p] || c.tuChoi || seen.has(p)) return false;
    if (config.guiTinNhan && !msgFor(c)) return false;
    seen.add(p);
    return true;
  });
}

// ---------- bảng ----------
function renderTable() {
  const locked = !!st.running;
  const ph = baseMsg() ? "(để trống: dùng tin nhắn gửi hàng loạt)" : "Nhập nội dung tin nhắn...";
  $("tbody").innerHTML = contacts.map((c, i) => {
    const p = normalizePhone(c.sdt);
    const done = !!doneMap[p];
    // Số quản trị giao: không đổi SĐT, không xoá được (chỉ sửa tên, nội dung, dự án)
    const khoa = c.khoa ? ' title="Số quản trị giao: không đổi SĐT hay xoá được"' : "";
    const nutTuChoi = p && !c.tuChoi
      ? `<button class="btn sm link" data-tuchoi="${p}" title="Khách nói không muốn nhận tin: không bao giờ gửi cho số này nữa" ${locked ? "disabled" : ""}>Từ chối</button>` : "";
    const nutXoa = c.khoa ? ""
      : `<button class="btn sm link" data-del="${i}" aria-label="Xoá dòng ${i + 1}" ${locked ? "disabled" : ""}>Xoá</button>`;
    return `<tr data-i="${i}" class="${st.current && st.current === p ? "current" : ""}">
      <td class="idx"${khoa}>${i + 1}${c.khoa ? " 🔒" : ""}</td>
      <td><input class="phone" id="sdt-${i}" value="${esc(c.sdt)}" placeholder="0901234567" aria-label="Số điện thoại dòng ${i + 1}" ${locked || c.khoa ? "disabled" : ""}${khoa}></td>
      <td><input class="name" value="${esc(c.ten)}" placeholder="Họ tên" aria-label="Họ tên dòng ${i + 1}" ${locked ? "disabled" : ""}></td>
      <td><input class="duan" value="${esc(c.duAn)}" placeholder="${esc(config.tenDuAn || "Dự án")}" aria-label="Dự án dòng ${i + 1}" ${locked ? "disabled" : ""}></td>
      <td><textarea class="msg" rows="1" placeholder="${esc(ph)}" aria-label="Nội dung tin nhắn dòng ${i + 1}" ${locked || done ? "disabled" : ""}>${esc(c.noiDung)}</textarea></td>
      <td class="status">${statusCell(statusOf(c))}</td>
      <td class="act">${done && !c.tuChoi ? `<button class="btn sm link" data-reset="${p}" ${locked ? "disabled" : ""}>Gửi lại</button>` : ""}${nutTuChoi}${nutXoa}</td>
    </tr>`;
  }).join("");
  $("emptyList").classList.toggle("hidden", contacts.length > 0);
  renderStats();
}
function refreshStatuses() {
  document.querySelectorAll("#tbody tr").forEach((tr) => {
    const c = contacts[tr.dataset.i];
    if (!c) return;
    tr.querySelector(".status").innerHTML = statusCell(statusOf(c));
    tr.classList.toggle("current", !!st.current && st.current === normalizePhone(c.sdt));
  });
  renderStats();
}
function renderStats() {
  let ok = 0, bad = 0;
  for (const c of contacts) {
    const s = statusOf(c);
    if (s.cls === "ok") ok++;
    else if (s.cls === "bad" || s.cls === "warn") bad++;
  }
  $("stTotal").textContent = contacts.length;
  $("stTodo").textContent = pendingList().length;
  $("stOk").textContent = ok;
  $("stBad").textContent = bad;
  renderRunControls();
  renderPreview();
}

// ---------- lưu danh sách ----------
function setDoneFrom(view) {
  doneMap = {};
  for (const c of view) if (c.sdtChuan && c.ketQua) doneMap[c.sdtChuan] = c.ketQua;
}
const toLocal = (view) => view.map((c) => ({ id: c.id, sdt: c.sdt, ten: c.ten, noiDung: c.noiDung || "", duAn: c.duAn || "",
  khoa: !!c.khoa, tuChoi: !!c.tuChoi }));
// Server chỉ lưu phần thay đổi: dòng mới (chưa có id) nhận id sau khi lưu, ghép theo SĐT và thứ tự.
// Dòng nào gõ tiếp trong lúc đang lưu thì không ghép được: lần lưu sau server tự thay dòng cũ.
function ganId(view) {
  const daCo = new Set(contacts.map((c) => c.id).filter(Boolean));
  const cho = {};
  for (const v of view) if (!daCo.has(v.id)) (cho[v.sdt] ??= []).push(v);
  for (const c of contacts) {
    if (c.id) continue;
    const v = cho[c.sdt]?.shift();
    if (v) Object.assign(c, { id: v.id, khoa: !!v.khoa, tuChoi: !!v.tuChoi });
  }
}
const saveContacts = debounce(async () => {
  try {
    const r = await api("/api/contacts", { contacts });
    listDirty = false;
    version = r.version;
    setDoneFrom(r.contacts);
    ganId(r.contacts);
    const { trungNguoiKhac = [], tuChoi = [], giuLai = 0 } = r.luu || {};
    if (trungNguoiKhac.length || tuChoi.length) {
      // Số bị server từ chối thêm: bỏ khỏi bảng để không lưu lại mãi
      const bo = new Set([...trungNguoiKhac, ...tuChoi]);
      contacts = contacts.filter((c) => c.id || !bo.has(normalizePhone(c.sdt)));
      renderTable();
      if (trungNguoiKhac.length) showError(new Error(`Không thêm ${trungNguoiKhac.length} số đã có trong danh sách của nhân viên khác: ${trungNguoiKhac.slice(0, 5).join(", ")}${trungNguoiKhac.length > 5 ? "…" : ""}`));
      if (tuChoi.length) showError(new Error(`Không thêm ${tuChoi.length} số khách đã từ chối liên hệ: ${tuChoi.slice(0, 5).join(", ")}${tuChoi.length > 5 ? "…" : ""}`));
    } else refreshStatuses();
    if (giuLai) { info(`Giữ lại ${giuLai} số quản trị giao (nhân viên không xoá được).`, "warn"); reloadContacts(); }
    flash("listSaved", "Đã lưu");
  } catch (e) { showError(e); }
}, 600);
function queueSave() { listDirty = true; saveContacts(); }

// Quản trị viên vừa giao thêm số, sửa nội dung mặc định, hoặc có kết quả mới: tải lại
async function reloadContacts() {
  if (listDirty) return;
  const seq = imgSeq;   // có thao tác ảnh trong lúc tải lại thì bỏ danh sách ảnh cũ nhận được
  try {
    const r = await api("/api/contacts");
    if (listDirty) return;
    version = r.version;
    config = r.config || config;
    if (!myDirty && r.tinNhanCuaToi !== undefined) { myDefault = r.tinNhanCuaToi; if (document.activeElement !== $("myMsg")) $("myMsg").value = myDefault; }
    if (!inviteDirty && r.loiMoiCuaToi !== undefined) { myInvite = r.loiMoiCuaToi; if (document.activeElement !== $("myInvite")) $("myInvite").value = myInvite; }
    if (r.anhCuaToi && !imgBusy && seq === imgSeq) { myImages = r.anhCuaToi; renderMyImages(); }
    const focused = document.activeElement?.closest?.("#tbody");
    contacts = toLocal(r.contacts);
    setDoneFrom(r.contacts);
    if (focused) refreshStatuses(); else renderTable();
    if (!$("panelLichSu").classList.contains("hidden")) loadHistory();
  } catch {}
}

$("tbody").addEventListener("input", (e) => {
  const tr = e.target.closest("tr");
  if (!tr) return;
  const c = contacts[tr.dataset.i];
  if (e.target.classList.contains("phone")) c.sdt = e.target.value;
  else if (e.target.classList.contains("msg")) c.noiDung = e.target.value;
  else if (e.target.classList.contains("duan")) c.duAn = e.target.value;
  else c.ten = e.target.value;
  tr.querySelector(".status").innerHTML = statusCell(statusOf(c));
  renderStats();
  queueSave();
});
$("tbody").addEventListener("click", async (e) => {
  const { del, reset, tuchoi } = e.target.dataset;
  if (del !== undefined) {
    if (contacts[Number(del)]?.khoa) return;
    contacts.splice(Number(del), 1);
    renderTable();
    queueSave();
  } else if (tuchoi) {
    const lyDo = prompt(`Đánh dấu ${tuchoi} là khách từ chối liên hệ: tool sẽ không bao giờ gửi cho số này nữa (nhập lại cũng bị loại).\n\nLý do (không bắt buộc):`, "Khách không muốn nhận tin");
    if (lyDo === null) return;
    try {
      const r = await api("/api/tu-choi", { sdt: tuchoi, lyDo });
      version = r.version;
      const tc = new Set(r.contacts.filter((c) => c.tuChoi).map((c) => c.sdtChuan));
      for (const c of contacts) c.tuChoi = tc.has(normalizePhone(c.sdt));
      renderTable();
      info(`Đã đánh dấu ${tuchoi} là khách từ chối liên hệ.`, "warn");
    } catch (err) { showError(err); }
  } else if (reset) {
    try {
      const r = await api("/api/reset", { sdt: reset });
      version = r.version;
      setDoneFrom(r.contacts);
      renderTable();
    } catch (err) { showError(err); }
  }
});
$("btnAddRow").onclick = () => {
  contacts.push({ sdt: "", ten: "", noiDung: "", duAn: $("duAnAll").value.trim() });
  renderTable();
  $(`sdt-${contacts.length - 1}`).focus();
};
let clearArmed = false;
$("btnClearList").onclick = () => {
  if (!clearArmed) {
    clearArmed = true;
    $("btnClearList").textContent = "Bấm lần nữa để xoá";
    setTimeout(() => { clearArmed = false; $("btnClearList").textContent = "Xoá hết"; }, 3000);
    return;
  }
  clearArmed = false;
  $("btnClearList").textContent = "Xoá hết";
  const giu = contacts.filter((c) => c.khoa);     // số quản trị giao: không xoá được
  contacts = giu;
  renderTable();
  queueSave();
  if (giu.length) info(`Đã xoá các số bạn tự thêm. Giữ lại ${giu.length} số quản trị giao.`, "warn");
};
function addMany(rows) {
  const have = new Set(contacts.map((c) => normalizePhone(c.sdt) || c.sdt));
  let added = 0, skipped = 0, coNoiDung = 0;
  for (const r of rows) {
    const key = normalizePhone(r.sdt) || String(r.sdt).trim();
    if (!key) continue;
    if (have.has(key)) { skipped++; continue; }
    have.add(key);
    const noiDung = String(r.noiDung || "").trim();
    if (noiDung) coNoiDung++;
    contacts.push({ sdt: String(r.sdt).trim(), ten: String(r.ten || "").trim(), noiDung,
      duAn: String(r.duAn || "").trim() || $("duAnAll").value.trim() });
    added++;
  }
  renderTable();
  queueSave();
  info(`Đã thêm ${added} người (${coNoiDung} có nội dung riêng)${skipped ? `, bỏ qua ${skipped} số trùng` : ""}.`);
}

// Gán dự án cho các số chưa có dự án (ghi vào tên gợi nhớ khi kết bạn)
$("btnDuAnAll").onclick = () => {
  const duAn = $("duAnAll").value.trim();
  if (!duAn) return showError(new Error("Nhập tên dự án vào ô Dự án trước."));
  let n = 0;
  for (const c of contacts) if (!String(c.duAn || "").trim() && !doneMap[normalizePhone(c.sdt)]) { c.duAn = duAn; n++; }
  renderTable();
  if (n) queueSave();
  info(n ? `Đã gán dự án “${duAn}” cho ${n} số.` : "Không còn số nào chưa có dự án.", n ? "ok" : "info");
};

// Dán: mỗi dòng "SĐT, Họ tên, Nội dung" hoặc 2–4 cột copy từ Excel (Tab: SĐT, Họ tên, Nội dung, Dự án)
$("btnPaste").onclick = () => { $("pasteText").value = ""; openModal("pasteModal"); $("pasteText").focus(); };
$("btnPasteOk").onclick = () => {
  const rows = $("pasteText").value.split(/\r?\n/).map(docDongDan).filter(Boolean);
  closeModal("pasteModal");
  if (!rows.length) return showError(new Error("Không đọc được dòng nào. Mỗi dòng cần bắt đầu bằng số điện thoại."));
  addMany(rows);
};
$("btnImport").onclick = () => $("fileInput").click();
$("fileInput").onchange = async () => {
  const f = $("fileInput").files[0];
  $("fileInput").value = "";
  if (!f) return;
  try {
    const r = await api("/api/import", await f.arrayBuffer(), { raw: true });
    if (!r.rows.length) throw new Error("Không đọc được dòng nào. File cần có cột SĐT, Họ tên và (không bắt buộc) Nội dung. Bấm “Tải file mẫu” để xem mẫu.");
    addMany(r.rows);
  } catch (e) { showError(e); }
};

// ---------- tin nhắn gửi hàng loạt của tôi ----------
let myDirty = false;
function renderPreview() {
  const ten = (pendingList()[0] || contacts[0])?.ten || "Anh Nam";
  $("myMsg").placeholder = config.noiDungTinNhan
    ? `Để trống sẽ dùng nội dung của quản trị:\n${config.noiDungTinNhan}`
    : "Soạn tin nhắn gửi cho tất cả người trong danh sách...";
  const soMau = cacMau(baseMsg()).length;
  $("myMsgCount").textContent = (myDefault ? `${myDefault.length} ký tự` : "đang dùng nội dung của quản trị")
    + (soMau > 1 ? ` · ${soMau} mẫu, xáo ngẫu nhiên` : "");
  $("btnUseAdmin").classList.toggle("hidden", !myDefault);
  const soAnh = myImages.length || (config.anh || []).length;   // ảnh của tôi, chưa có thì ảnh của quản trị
  $("pvMsgLabel").textContent = `Xem trước tin nhắn (ví dụ cho "${ten}")`
    + (soMau > 1 ? `, mẫu 1/${soMau}` : "") + (soAnh && config.guiTinNhan ? `, kèm ${soAnh} ảnh` : "");
  $("pvMsg").textContent = !config.guiTinNhan ? "(quản trị đang tắt gửi tin nhắn)"
    : baseMsg() ? fill(cacMau(baseMsg())[0] || "", ten) : "(chưa có nội dung, soạn tin nhắn ở ô trên hoặc nhập nội dung riêng cho từng người)";
  const mauLoiMoi = cacMau(myInvite || config.loiMoiKetBan);
  $("pvLabel").textContent = (myInvite ? "Lời mời kết bạn của tôi" : "Lời mời kết bạn (của quản trị)")
    + (mauLoiMoi.length > 1 ? `, mẫu 1/${mauLoiMoi.length}` : "");
  $("myInvite").placeholder = config.loiMoiKetBan
    ? `Để trống sẽ dùng lời mời của quản trị:\n${config.loiMoiKetBan}` : "Soạn lời mời kết bạn của bạn...";
  const soMauMoi = cacMau(myInvite).length;
  const dai = cacMau(myInvite).some((m) => m.length > 150);
  $("myInviteCount").textContent = (myInvite ? `${myInvite.length} ký tự` : "đang dùng lời mời của quản trị")
    + (soMauMoi > 1 ? ` · ${soMauMoi} mẫu, xáo ngẫu nhiên` : "") + (dai ? " · có mẫu dài hơn 150 ký tự" : "");
  $("myInviteCount").classList.toggle("over", dai);
  $("btnUseAdminInvite").classList.toggle("hidden", !myInvite);
  $("pvInvite").textContent = config.guiKetBan ? fill(mauLoiMoi[0] || "", ten) : "(không gửi lời mời kết bạn)";
}
const saveMyMsg = debounce(async () => {
  try {
    const r = await api("/api/tin-nhan-cua-toi", { noiDung: $("myMsg").value });
    myDirty = false;
    version = r.version;
    flash("myMsgSaved", "Đã lưu");
  } catch (e) { showError(e); }
}, 700);
$("myMsg").addEventListener("input", () => {
  myDirty = true;
  myDefault = $("myMsg").value.trim();
  renderTable();
  saveMyMsg();
});
$("btnInsertTen").onclick = () => {
  const t = $("myMsg");
  const [a, b] = [t.selectionStart ?? t.value.length, t.selectionEnd ?? t.value.length];
  t.value = t.value.slice(0, a) + "{ten}" + t.value.slice(b);
  t.focus();
  t.selectionStart = t.selectionEnd = a + 5;
  t.dispatchEvent(new Event("input"));
};
$("btnAddMau").onclick = () => {
  const t = $("myMsg");
  if (t.value.trim()) t.value = `${t.value.trimEnd()}\n---\n`;
  t.focus();
  t.selectionStart = t.selectionEnd = t.value.length;
  t.dispatchEvent(new Event("input"));
};
$("btnUseAdmin").onclick = () => {
  $("myMsg").value = "";
  $("myMsg").dispatchEvent(new Event("input"));
};

// ---------- lời mời kết bạn của tôi ----------
let inviteDirty = false;
const saveMyInvite = debounce(async () => {
  try {
    const r = await api("/api/loi-moi-cua-toi", { loiMoi: $("myInvite").value });
    inviteDirty = false;
    version = r.version;
    flash("myMsgSaved", "Đã lưu");
  } catch (e) { showError(e); }
}, 700);
$("myInvite").addEventListener("input", () => {
  inviteDirty = true;
  myInvite = $("myInvite").value.trim();
  renderPreview();
  saveMyInvite();
});
$("btnAddMauInvite").onclick = () => {
  const t = $("myInvite");
  if (t.value.trim()) t.value = `${t.value.trimEnd()}\n---\n`;
  t.focus();
  t.selectionStart = t.selectionEnd = t.value.length;
  t.dispatchEvent(new Event("input"));
};
$("btnUseAdminInvite").onclick = () => {
  $("myInvite").value = "";
  $("myInvite").dispatchEvent(new Event("input"));
};

// ---------- ảnh gửi kèm tin nhắn của tôi ----------
const ANH_TOI_DA = 10;
let imgBusy = 0;          // số thao tác thêm/xoá ảnh đang chạy
let imgSeq = 0;           // tăng mỗi lần thêm/xoá ảnh xong
function renderMyImages() {
  const locked = !!st.running || imgBusy > 0;
  const soAnhQt = (config.anh || []).length;
  $("myThumbs").innerHTML = myImages.map((a, i) => `<figure class="thumb">
    <img src="/api/anh-cua-toi/${encodeURIComponent(a)}" alt="Ảnh gửi kèm ${i + 1}">
    <button class="btn sm danger" type="button" data-del-img="${esc(a)}" aria-label="Xoá ảnh ${i + 1}" ${locked ? "disabled" : ""}>Xoá</button>
  </figure>`).join("");
  $("myImgCount").textContent = myImages.length ? `${myImages.length}/${ANH_TOI_DA} ảnh`
    : soAnhQt ? `đang dùng ${soAnhQt} ảnh của quản trị` : "chưa có ảnh";
  $("myImgHint").textContent = myImages.length
    ? "Mỗi khách nhận tin nhắn kèm các ảnh này. Xoá hết ảnh để dùng lại ảnh của quản trị."
    : "JPG, PNG hoặc WEBP, tối đa 5 MB mỗi ảnh. Chưa thêm ảnh nào thì gửi kèm ảnh của quản trị (nếu có).";
  $("btnAddMyImg").disabled = locked || myImages.length >= ANH_TOI_DA;
  renderPreview();
}
$("btnAddMyImg").onclick = () => $("myImgInput").click();
$("myImgInput").onchange = async () => {
  const files = [...$("myImgInput").files];
  $("myImgInput").value = "";
  if (!files.length) return;
  const fd = new FormData();
  files.slice(0, ANH_TOI_DA - myImages.length).forEach((f) => fd.append("anh", f));
  imgBusy++;
  renderMyImages();
  try {
    const r = await api("/api/anh-cua-toi", fd);
    myImages = r.anhCuaToi;
    version = r.version;
    flash("myMsgSaved", "Đã lưu ảnh");
  } catch (e) { showError(e); }
  imgBusy--;
  imgSeq++;
  renderMyImages();
};
$("myThumbs").addEventListener("click", async (e) => {
  const ten = e.target.dataset.delImg;
  if (!ten || imgBusy) return;
  imgBusy++;
  renderMyImages();
  try {
    const r = await api("/api/anh-cua-toi/xoa", { ten });
    myImages = r.anhCuaToi;
    version = r.version;
  } catch (err) { showError(err); }
  imgBusy--;
  imgSeq++;
  renderMyImages();
});

// ---------- gửi ----------
function renderRunControls() {
  const pending = pendingList().length;
  const loggedIn = st.login === "in";
  const n = soLanNay();
  const conLai = conLaiHomNay();
  const mine = true;   // mỗi người gửi bằng Zalo của chính mình
  const thieu = contacts.filter((c) => statusOf(c).text === "Thiếu nội dung").length;
  $("btnStart").classList.toggle("hidden", !!st.running);
  $("btnStop").classList.toggle("hidden", !st.running || !mine);
  $("btnStop").disabled = !!st.stopRequested;
  $("btnStop").textContent = st.stopRequested ? "Đang dừng..." : "Dừng";
  const chan = hanMuc?.tamDungDen || conLai === 0;
  $("btnStart").disabled = !loggedIn || !pending || st.worker === false || chan;
  $("btnPreview").disabled = !pending || chan;
  const gio = (p) => `${Math.floor(p / 60)}:${String(p % 60).padStart(2, "0")}`;
  const homNay = hanMuc?.toiDa ? ` Hôm nay Zalo của bạn đã gửi ${hanMuc.daGui}/${hanMuc.toiDa} người.` : "";
  $("runHint").textContent = st.running ? homNay.trim()
    : hanMuc?.tamDungDen ? `Zalo của bạn đang tạm dừng gửi đến ${hanMuc.tamDungDen} vì đợt trước gặp nhiều lỗi liên tiếp (có thể Zalo đang hạn chế tài khoản). Kiểm tra Zalo trên điện thoại; quản trị có thể bỏ tạm dừng.`
    : conLai === 0 ? `Zalo của bạn đã gửi đủ ${hanMuc.toiDa} người hôm nay (hạn mức để tránh bị Zalo khoá). Mai gửi tiếp nhé.`
    : !loggedIn ? "Kết nối Zalo của bạn ở khung “Zalo của tôi” phía trên trước khi gửi."
    : !pending ? (thieu ? `Còn ${thieu} người thiếu nội dung tin nhắn, điền nội dung để gửi.` : "Không còn số nào chờ gửi.")
    : `Lần này sẽ gửi cho ${n} người${pending > n ? ` (còn ${pending - n} người cho lần sau)` : ""}.${homNay} Mỗi người cách nhau ${config.choToiThieuGiay}–${config.choToiDaGiay} giây.${config.nghiSauSoLan ? ` Cứ ${config.nghiSauSoLan} người nghỉ ${config.nghiPhut} phút.` : ""} Chỉ gửi trong khung ${gio(config.gioBatDau ?? 480)}–${gio(config.gioKetThuc ?? 1200)}${config.nghiChuNhat ? ", nghỉ Chủ nhật" : ""}.${thieu ? ` ${thieu} người thiếu nội dung sẽ được bỏ qua.` : ""}`;
  if (st.running) $("confirmBox").classList.add("hidden");
  ["btnAddRow", "btnPaste", "btnImport", "btnClearList", "btnInsertTen", "btnAddMau", "btnUseAdmin", "btnDuAnAll", "duAnAll"].forEach((id) => ($(id).disabled = !!st.running));
  $("myMsg").disabled = !!st.running;
  ["myInvite", "btnAddMauInvite", "btnUseAdminInvite"].forEach((id) => ($(id).disabled = !!st.running));
  $("btnAddMyImg").disabled = !!st.running || imgBusy > 0 || myImages.length >= ANH_TOI_DA;
  document.querySelectorAll("#myThumbs [data-del-img]").forEach((b) => (b.disabled = !!st.running || imgBusy > 0));
}
// Hôm nay còn gửi được bao nhiêu người (null = không giới hạn)
const conLaiHomNay = () => (hanMuc?.toiDa ? Math.max(0, hanMuc.toiDa - hanMuc.daGui) : null);
// Lần bấm gửi này gửi bao nhiêu người: không quá "mỗi lần" (0 = không giới hạn) và phần còn lại hôm nay
const soLanNay = () => Math.min(pendingList().length, Number(config.gioiHanMoiLan) || Infinity, conLaiHomNay() ?? Infinity);
$("btnStart").onclick = () => {
  const n = soLanNay();
  const what = [config.guiKetBan && "lời mời kết bạn", config.guiTinNhan && "tin nhắn"].filter(Boolean).join(" rồi ");
  $("confirmText").innerHTML = `Tự động gửi ${esc(what)} cho <b>${n} người</b> trong danh sách của bạn?`;
  $("confirmBox").classList.remove("hidden");
};
$("btnCancelConfirm").onclick = () => $("confirmBox").classList.add("hidden");
$("btnConfirm").onclick = async () => {
  $("confirmBox").classList.add("hidden");
  try { await api("/api/start", {}); } catch (e) { showError(e); }
};
$("btnStop").onclick = async () => { try { await api("/api/stop", {}); } catch (e) { showError(e); } };
$("btnPreview").onclick = async () => {
  try {
    const { items } = await api("/api/preview", {});
    $("pvTitle").textContent = `Xem trước đợt gửi (${items.length} người)`;
    $("pvList").innerHTML = items.map((it, i) => `<div class="pv-item">
      <h3>${i + 1}. ${esc(it.sdt)} · ${esc(it.ten || "không tên")}</h3>
      ${it.loiMoi !== null ? `<div class="preview"><span class="lbl">Lời mời kết bạn${it.soMauLoiMoi > 1 ? ` (ngẫu nhiên 1 trong ${it.soMauLoiMoi} mẫu)` : ""}</span>${esc(it.loiMoi)}</div>` : ""}
      ${it.tenGoiNho ? `<div class="preview"><span class="lbl">Đổi tên gợi nhớ thành</span>${esc(it.tenGoiNho)}</div>` : ""}
      ${it.tinNhan !== null ? `<div class="preview"><span class="lbl">Tin nhắn${it.soMauTinNhan > 1 ? ` (ngẫu nhiên 1 trong ${it.soMauTinNhan} mẫu)` : ""}${it.soAnh ? `, kèm ${it.soAnh} ảnh` : ""}</span>${esc(it.tinNhan)}</div>` : ""}
    </div>`).join("") || `<p class="hint">Không còn ai chờ gửi.</p>`;
    openModal("previewModal");
  } catch (e) { showError(e); }
};

// Đồng hồ đếm ngược khi đang chờ
setInterval(() => {
  if (st.running && st.waitUntil && !st.current) {
    const s = Math.max(0, Math.round((st.waitUntil - Date.now()) / 1000));
    $("runNow").textContent = st.stopRequested ? "Đang dừng..."
      : st.waitBreak ? `Đang nghỉ giữa lượt, còn ${Math.floor(s / 60)} phút ${s % 60} giây`
      : `Chờ ${s} giây trước lần gửi tiếp theo`;
  }
}, 1000);

// ---------- Zalo của tôi ----------
function renderZalo() {
  const dot = "dot" + (st.login === "in" ? " in" : st.login === "out" ? "" : " wait");
  $("acctDot").className = dot;
  $("zDot").className = dot;
  $("acctText").textContent = "Zalo: " + (st.login === "in" ? st.account : "chưa kết nối");
  $("zAccount").textContent = st.login === "in" ? st.account
    : st.login === "checking" ? "Đang kết nối..." : st.login === "qr" ? "Chờ bạn quét mã QR" : "Chưa kết nối";
  $("zState").textContent = st.login === "in" ? "Đã kết nối" : "";
  $("zQrBox").classList.toggle("hidden", !(st.login === "qr" && st.qr));
  if (st.qr && $("zQr").src !== st.qr) $("zQr").src = st.qr;
  $("zConnect").classList.toggle("hidden", st.login !== "out");
  $("zConnect").disabled = st.worker === false;
  $("zCancel").classList.toggle("hidden", !(st.login === "qr" || st.login === "checking"));
  $("zLogout").classList.toggle("hidden", st.login !== "in" || !!st.running);
}
$("zConnect").onclick = async () => {
  try { await api("/api/zalo/login", { qr: false }); info("Đang tạo mã QR, chờ vài giây...", "info"); } catch (e) { showError(e); }
};
$("zCancel").onclick = async () => { try { await api("/api/zalo/login/cancel", {}); } catch (e) { showError(e); } };
let zLogoutArmed = false;
$("zLogout").onclick = async () => {
  if (!zLogoutArmed) {
    zLogoutArmed = true;
    $("zLogout").textContent = "Bấm lần nữa để ngắt kết nối";
    setTimeout(() => { zLogoutArmed = false; $("zLogout").textContent = "Ngắt kết nối Zalo"; }, 3000);
    return;
  }
  zLogoutArmed = false;
  $("zLogout").textContent = "Ngắt kết nối Zalo";
  try { await api("/api/zalo/logout", {}); } catch (e) { showError(e); }
};

function renderState() {
  renderZalo();
  $("workerAlert").classList.toggle("hidden", st.worker !== false);
  $("runBox").classList.toggle("hidden", !st.running);
  if (st.running && st.progress) {
    $("runCount").textContent = `${st.progress.i}/${st.progress.total}`;
    $("runBar").style.width = `${(st.progress.i / st.progress.total) * 100}%`;
    if (st.current) $("runNow").textContent = st.kind === "nhom" ? `Đang gửi vào nhóm “${st.current}”` : `Đang gửi ${st.current}`;
  }
  // Bắt đầu / kết thúc đợt gửi thì vẽ lại cả bảng (khoá ô nhập, hiện nút Gửi lại)
  if (renderState.wasRunning !== !!st.running) renderTable();
  else refreshStatuses();
  renderState.wasRunning = !!st.running;
}

// ---------- nhật ký ----------
function addLog(e) {
  if (e.id) lastLogId = Math.max(lastLogId, e.id);
  const box = $("log");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 30;
  const div = document.createElement("div");
  div.className = e.level || "";
  div.innerHTML = `<span class="t">${esc(e.t)}</span>${esc(e.text)}`;
  box.appendChild(div);
  while (box.children.length > 300) box.firstChild.remove();
  if (atBottom) box.scrollTop = box.scrollHeight;
}

// ---------- tab ----------
function showTab(name) {
  const lichSu = name === "lichsu";
  $("tabGui").setAttribute("aria-selected", String(!lichSu));
  $("tabLichSu").setAttribute("aria-selected", String(lichSu));
  $("panelGui").classList.toggle("hidden", lichSu);
  $("panelLichSu").classList.toggle("hidden", !lichSu);
  if (lichSu) loadHistory();
  try { history.replaceState(null, "", lichSu ? "#lichsu" : "#"); } catch {}
}
$("tabGui").onclick = () => showTab("gui");
$("tabLichSu").onclick = () => showTab("lichsu");

// ---------- lịch sử gửi ----------
let hPage = 1;
function histParams() {
  const p = new URLSearchParams();
  const v = { q: $("hQ").value.trim(), tt: $("hTt").value, tu: $("hTu").value, den: $("hDen").value };
  for (const [k, x] of Object.entries(v)) if (x) p.set(k, x);
  return p;
}
async function loadHistory() {
  const p = histParams();
  $("hExcel").href = "/api/lich-su/excel" + (p.toString() ? `?${p}` : "");
  p.set("trang", hPage);
  try {
    const r = await api(`/api/lich-su?${p}`);
    $("hTong").textContent = r.tong;
    $("hOk").textContent = r.thanhCong;
    $("hLoi").textContent = r.loi;
    hPage = r.trang;
    $("hPage").textContent = `Trang ${r.trang}/${r.soTrang}`;
    $("hPrev").disabled = r.trang <= 1;
    $("hNext").disabled = r.trang >= r.soTrang;
    $("hBody").innerHTML = r.rows.map((h) => `<tr>
      <td class="nowrap">${esc(h.thoiGian)}</td>
      <td class="nowrap mono">${esc(h.sdt)}</td>
      <td>${esc(h.ten)}</td>
      <td>${esc(h.tenZalo)}</td>
      <td>${h.thanhCong ? '<span class="pill ok">Thành công</span>' : '<span class="pill bad">Lỗi</span>'}
        <span class="note">Kết bạn: ${esc(h.ketBan)} · Tin nhắn: ${esc(h.tinNhan)}</span></td>
      <td class="msg-cell">${esc(h.noiDung)}</td>
      <td><span class="note" style="white-space:normal;max-width:220px">${esc(h.ghiChu)}</span></td>
    </tr>`).join("");
    $("hEmpty").classList.toggle("hidden", r.rows.length > 0);
  } catch (e) { showError(e); }
}
$("histForm").addEventListener("submit", (e) => { e.preventDefault(); hPage = 1; loadHistory(); });
$("hReset").onclick = () => { ["hQ", "hTu", "hDen"].forEach((id) => ($(id).value = "")); $("hTt").value = ""; hPage = 1; loadHistory(); };
$("hPrev").onclick = () => { hPage = Math.max(1, hPage - 1); loadHistory(); };
$("hNext").onclick = () => { hPage += 1; loadHistory(); };

// ---------- hộp thoại ----------
function openModal(id) { $(id).classList.remove("hidden"); }
function closeModal(id) { $(id).classList.add("hidden"); }
document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => closeModal(b.dataset.close)));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") ["pasteModal", "previewModal"].forEach(closeModal); });

// ---------- cập nhật định kỳ ----------
async function poll() {
  try {
    const r = await api(`/api/poll?log=${lastLogId}`);
    $("connAlert").classList.add("hidden");
    st = r;
    if (r.hanMuc) hanMuc = r.hanMuc;
    renderState();
    r.logs.forEach(addLog);
    if (version !== null && r.version !== version) reloadContacts();
  } catch {
    $("connAlert").classList.remove("hidden");
  }
  setTimeout(poll, document.hidden ? 5000 : 1500);
}

(async () => {
  try {
    const s = await api("/api/state");
    config = s.config;
    myDefault = s.tinNhanCuaToi || "";
    $("myMsg").value = myDefault;
    myInvite = s.loiMoiCuaToi || "";
    $("myInvite").value = myInvite;
    myImages = s.anhCuaToi || [];
    renderMyImages();
    contacts = toLocal(s.contacts);
    setDoneFrom(s.contacts);
    st = s;
    hanMuc = s.hanMuc || null;
    version = s.version;
    s.logs.forEach(addLog);
    renderTable();
    renderState();
  } catch (e) {
    $("connAlert").classList.remove("hidden");
    showError(e);
  }
  if (location.hash === "#lichsu") showTab("lichsu");
  poll();
})();
