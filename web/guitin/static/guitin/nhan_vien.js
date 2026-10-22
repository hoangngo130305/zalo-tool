// Trang của nhân viên: nhập danh sách (SĐT, họ tên, nội dung), gửi tự động, xem lịch sử & xuất Excel.
const $ = (id) => document.getElementById(id);
const me = JSON.parse($("me-data").textContent);
let contacts = [];        // [{ sdt, ten, noiDung }] các số của mình
let doneMap = {};         // sdtChuan -> kết quả mới nhất
let config = {};
let myDefault = "";       // tin nhắn mặc định của tôi (để trống = dùng nội dung của quản trị)
let st = {};              // trạng thái gửi (từ server)
let version = null;       // phiên bản danh sách trên server
let lastLogId = 0;
let listDirty = false;    // có thay đổi chưa lưu -> không tải đè

// ---------- tiện ích ----------
function normalizePhone(raw) {
  let p = String(raw ?? "").replace(/[^\d+]/g, "");
  if (p.startsWith("+84")) p = "0" + p.slice(3);
  else if (p.startsWith("84") && p.length === 11) p = "0" + p.slice(2);
  else if (p.length === 9) p = "0" + p;
  return /^0\d{9}$/.test(p) ? p : null;
}
// Nhiều mẫu tin nhắn cách nhau bằng dòng "---": mỗi khách nhận ngẫu nhiên 1 mẫu (worker chọn khi gửi)
const cacMau = (t) => String(t || "").split(/^\s*-{3,}\s*$/m).map((s) => s.trim()).filter(Boolean);
const fill = (tpl, ten) => String(tpl || "").replace(/\{ten\}/gi, () => ten || "bạn").trim();
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
    headers: { "Content-Type": opts.raw ? "application/octet-stream" : "application/json", "X-CSRFToken": csrfToken() },
    body: opts.raw ? body : body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) location.href = "/dang-nhap/?next=/";
  if (!res.ok) throw new Error(data.error || "Có lỗi xảy ra");
  return data;
}

// Nội dung tin nhắn thực tế sẽ gửi cho 1 người:
// nội dung riêng của dòng -> tin nhắn của tôi -> nội dung của quản trị
const baseMsg = () => myDefault || config.noiDungTinNhan || "";
const msgFor = (c) => fill(c.noiDung || baseMsg(), c.ten);

// ---------- trạng thái từng số ----------
function statusOf(c) {
  const p = normalizePhone(c.sdt);
  if (!String(c.sdt).trim()) return { cls: "warn", text: "Thiếu số" };
  if (!p) return { cls: "bad", text: "Số sai" };
  if (st.current === p) return { cls: "run", text: "Đang gửi" };
  const r = doneMap[p];
  if (r) {
    if (r.tinNhan === "Lỗi") return { cls: "bad", text: "Lỗi", note: r.ghiChu };
    return { cls: "ok", text: "Đã gửi", note: [r.tenZalo && `Zalo: ${r.tenZalo}`, r.thoiGian].filter(Boolean).join(" · ") };
  }
  if (config.guiTinNhan && !msgFor(c)) return { cls: "warn", text: "Thiếu nội dung" };
  return { cls: "todo", text: "Chờ gửi" };
}
const statusCell = (s) => `<span class="pill ${s.cls}">${s.text}</span>${s.note ? `<span class="note" title="${esc(s.note)}">${esc(s.note)}</span>` : ""}`;
function pendingList() {
  const seen = new Set();
  return contacts.filter((c) => {
    const p = normalizePhone(c.sdt);
    if (!p || doneMap[p] || seen.has(p)) return false;
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
    return `<tr data-i="${i}" class="${st.current && st.current === p ? "current" : ""}">
      <td class="idx">${i + 1}</td>
      <td><input class="phone" id="sdt-${i}" value="${esc(c.sdt)}" placeholder="0901234567" aria-label="Số điện thoại dòng ${i + 1}" ${locked ? "disabled" : ""}></td>
      <td><input class="name" value="${esc(c.ten)}" placeholder="Họ tên" aria-label="Họ tên dòng ${i + 1}" ${locked ? "disabled" : ""}></td>
      <td><textarea class="msg" rows="1" placeholder="${esc(ph)}" aria-label="Nội dung tin nhắn dòng ${i + 1}" ${locked || done ? "disabled" : ""}>${esc(c.noiDung)}</textarea></td>
      <td class="status">${statusCell(statusOf(c))}</td>
      <td class="act">${done ? `<button class="btn sm link" data-reset="${p}" ${locked ? "disabled" : ""}>Gửi lại</button>` : ""}<button class="btn sm link" data-del="${i}" aria-label="Xoá dòng ${i + 1}" ${locked ? "disabled" : ""}>Xoá</button></td>
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
const toLocal = (view) => view.map((c) => ({ sdt: c.sdt, ten: c.ten, noiDung: c.noiDung || "" }));
const saveContacts = debounce(async () => {
  try {
    const r = await api("/api/contacts", { contacts });
    listDirty = false;
    version = r.version;
    setDoneFrom(r.contacts);
    refreshStatuses();
    flash("listSaved", "Đã lưu");
  } catch (e) { showError(e); }
}, 600);
function queueSave() { listDirty = true; saveContacts(); }

// Quản trị viên vừa giao thêm số, sửa nội dung mặc định, hoặc có kết quả mới: tải lại
async function reloadContacts() {
  if (listDirty) return;
  try {
    const r = await api("/api/contacts");
    if (listDirty) return;
    version = r.version;
    config = r.config || config;
    if (!myDirty && r.tinNhanCuaToi !== undefined) { myDefault = r.tinNhanCuaToi; if (document.activeElement !== $("myMsg")) $("myMsg").value = myDefault; }
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
  else c.ten = e.target.value;
  tr.querySelector(".status").innerHTML = statusCell(statusOf(c));
  renderStats();
  queueSave();
});
$("tbody").addEventListener("click", async (e) => {
  const { del, reset } = e.target.dataset;
  if (del !== undefined) {
    contacts.splice(Number(del), 1);
    renderTable();
    queueSave();
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
  contacts.push({ sdt: "", ten: "", noiDung: "" });
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
  contacts = [];
  renderTable();
  queueSave();
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
    contacts.push({ sdt: String(r.sdt).trim(), ten: String(r.ten || "").trim(), noiDung });
    added++;
  }
  renderTable();
  queueSave();
  info(`Đã thêm ${added} người (${coNoiDung} có nội dung riêng)${skipped ? `, bỏ qua ${skipped} số trùng` : ""}.`);
}

// Dán: mỗi dòng "SĐT, Họ tên, Nội dung" hoặc 2–3 cột copy từ Excel (Tab)
$("btnPaste").onclick = () => { $("pasteText").value = ""; openModal("pasteModal"); $("pasteText").focus(); };
$("btnPasteOk").onclick = () => {
  const rows = $("pasteText").value.split(/\r?\n/).map((line) => {
    if (!line.trim()) return null;
    let parts;
    if (line.includes("\t")) {
      const [a, b = "", ...rest] = line.split("\t");
      parts = [a, b, rest.join("\t")];
    } else {
      const m = line.trim().match(/^([^,;|]+)(?:\s*[,;|]\s*([^,;|]*))?(?:\s*[,;|]\s*([\s\S]*))?$/);
      parts = m ? [m[1], m[2] || "", m[3] || ""] : [line];
    }
    const sdt = parts[0].replace(/[\s.\-]/g, "");
    return /^\+?\d{8,12}$/.test(sdt) ? { sdt, ten: parts[1].trim(), noiDung: parts[2].trim() } : null;
  }).filter(Boolean);
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
  const soAnh = (config.anh || []).length;
  $("pvMsgLabel").textContent = `Xem trước tin nhắn (ví dụ cho "${ten}")`
    + (soMau > 1 ? `, mẫu 1/${soMau}` : "") + (soAnh && config.guiTinNhan ? `, kèm ${soAnh} ảnh` : "");
  $("pvMsg").textContent = !config.guiTinNhan ? "(quản trị đang tắt gửi tin nhắn)"
    : baseMsg() ? fill(cacMau(baseMsg())[0] || "", ten) : "(chưa có nội dung, soạn tin nhắn ở ô trên hoặc nhập nội dung riêng cho từng người)";
  const mauLoiMoi = cacMau(config.loiMoiKetBan);
  $("pvLabel").textContent = "Lời mời kết bạn (do quản trị soạn)" + (mauLoiMoi.length > 1 ? `, mẫu 1/${mauLoiMoi.length}` : "");
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

// ---------- gửi ----------
function renderRunControls() {
  const pending = pendingList().length;
  const loggedIn = st.login === "in";
  const n = Math.min(pending, Number(config.gioiHanMoiLan) || 0);
  const mine = true;   // mỗi người gửi bằng Zalo của chính mình
  const thieu = contacts.filter((c) => statusOf(c).text === "Thiếu nội dung").length;
  $("btnStart").classList.toggle("hidden", !!st.running);
  $("btnStop").classList.toggle("hidden", !st.running || !mine);
  $("btnStop").disabled = !!st.stopRequested;
  $("btnStop").textContent = st.stopRequested ? "Đang dừng..." : "Dừng";
  $("btnStart").disabled = !loggedIn || !pending || st.worker === false;
  $("btnPreview").disabled = !pending;
  $("runHint").textContent = st.running ? ""
    : !loggedIn ? "Kết nối Zalo của bạn ở khung “Zalo của tôi” phía trên trước khi gửi."
    : !pending ? (thieu ? `Còn ${thieu} người thiếu nội dung tin nhắn, điền nội dung để gửi.` : "Không còn số nào chờ gửi.")
    : `Lần này sẽ gửi cho ${n} người${pending > n ? ` (còn ${pending - n} người cho lần sau)` : ""}. Mỗi người cách nhau ${config.choToiThieuGiay}–${config.choToiDaGiay} giây.${config.nghiSauSoLan ? ` Cứ ${config.nghiSauSoLan} người nghỉ ${config.nghiPhut} phút.` : ""}${thieu ? ` ${thieu} người thiếu nội dung sẽ được bỏ qua.` : ""}`;
  if (st.running) $("confirmBox").classList.add("hidden");
  ["btnAddRow", "btnPaste", "btnImport", "btnClearList", "btnInsertTen", "btnAddMau", "btnUseAdmin"].forEach((id) => ($(id).disabled = !!st.running));
  $("myMsg").disabled = !!st.running;
}
$("btnStart").onclick = () => {
  const n = Math.min(pendingList().length, Number(config.gioiHanMoiLan) || 0);
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
    contacts = toLocal(s.contacts);
    setDoneFrom(s.contacts);
    st = s;
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
