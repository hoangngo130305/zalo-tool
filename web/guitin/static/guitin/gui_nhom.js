// Trang gửi tin vào nhóm Zalo: chọn nhóm đích, soạn nội dung + ảnh, gửi tự động bằng Zalo của chính mình.
const $ = (id) => document.getElementById(id);
let st = {};              // trạng thái Zalo & đợt gửi (từ server)
let config = {};          // cài đặt thời gian chờ của quản trị
let cauHinh = { noiDung: "", nhom: [], anh: [] };
let groups = null;        // danh sách nhóm đã tải từ Zalo (null = chưa tải)
let anhToiDa = 10;
let version = null;
let lastLogId = 0;

// ---------- tiện ích ----------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const csrfToken = () => document.cookie.split("; ").find((c) => c.startsWith("csrftoken="))?.split("=")[1] || "";
const now = () => new Date().toLocaleTimeString("vi-VN");
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function flash(id, text) { $(id).textContent = text; clearTimeout(flash[id]); flash[id] = setTimeout(() => ($(id).textContent = ""), 2500); }
const showError = (e) => addLog({ t: now(), text: e.message || String(e), level: "error" });
const info = (text, level = "ok") => addLog({ t: now(), text, level });

async function api(path, body, opts = {}) {
  const form = body instanceof FormData;
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { ...(form ? {} : { "Content-Type": "application/json" }), "X-CSRFToken": csrfToken() },
    body: form ? body : body === undefined ? undefined : JSON.stringify(body),
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) location.href = "/dang-nhap/?next=/gui-nhom/";
  if (!res.ok) throw new Error(data.error || "Có lỗi xảy ra");
  return data;
}

// ---------- nhóm ----------
const selected = () => new Set(cauHinh.nhom.map((g) => g.id));
function renderGroups() {
  const sel = selected();
  const q = $("groupSearch").value.trim().toLowerCase();
  // Chưa tải từ Zalo thì hiện các nhóm đã chọn lần trước
  const all = groups ?? cauHinh.nhom.map((g) => ({ ...g, soThanhVien: null }));
  const shown = all.filter((g) => !q || g.ten.toLowerCase().includes(q));
  const locked = !!st.running;
  $("groupBody").innerHTML = shown.map((g) => `<tr class="${sel.has(g.id) ? "current" : ""}">
    <td><input type="checkbox" data-id="${esc(g.id)}" id="g-${esc(g.id)}" ${sel.has(g.id) ? "checked" : ""} ${locked ? "disabled" : ""}></td>
    <td><label for="g-${esc(g.id)}" style="cursor:pointer">${esc(g.ten)}</label></td>
    <td class="n">${g.soThanhVien ?? ""}</td>
  </tr>`).join("");
  $("groupEmpty").classList.toggle("hidden", shown.length > 0);
  $("groupEmpty").textContent = all.length ? "Không có nhóm nào khớp từ khoá." : groups ? "Tài khoản Zalo này chưa tham gia nhóm nào." : "Chưa chọn nhóm nào. Bấm “Tải danh sách nhóm”.";
  $("groupHint").innerHTML = groups
    ? `Đã chọn <b>${sel.size}</b> / ${groups.length} nhóm.`
    : `Đã chọn <b>${sel.size}</b> nhóm. Bấm <b>Tải danh sách nhóm</b> để lấy các nhóm mà Zalo của bạn đang tham gia và chọn thêm.`;
  ["btnSelectAll", "btnSelectNone"].forEach((id) => ($(id).disabled = locked || !shown.length));
  renderRunControls();
}
const saveGroups = debounce(async () => {
  try {
    const r = await api("/api/nhom/cau-hinh", { nhom: cauHinh.nhom });
    cauHinh = r.cauHinh;
    flash("groupSaved", "Đã lưu");
  } catch (e) { showError(e); }
}, 500);
function setSelected(ids, on) {
  const byId = new Map((groups ?? cauHinh.nhom).map((g) => [g.id, g]));
  let nhom = cauHinh.nhom.filter((g) => !ids.includes(g.id));
  if (on) nhom = nhom.concat(ids.map((id) => ({ id, ten: byId.get(id)?.ten || id })));
  cauHinh.nhom = nhom;
  renderGroups();
  saveGroups();
}
$("groupBody").addEventListener("change", (e) => {
  if (e.target.dataset.id) setSelected([e.target.dataset.id], e.target.checked);
});
const visibleIds = () => [...document.querySelectorAll("#groupBody input[data-id]")].map((x) => x.dataset.id);
$("btnSelectAll").onclick = () => setSelected(visibleIds(), true);
$("btnSelectNone").onclick = () => setSelected(visibleIds(), false);
$("groupSearch").addEventListener("input", renderGroups);
$("btnLoadGroups").onclick = async () => {
  const b = $("btnLoadGroups");
  b.disabled = true;
  b.textContent = "Đang tải...";
  try {
    groups = (await api("/api/nhom/danh-sach")).groups;
    // Cập nhật tên mới nhất cho các nhóm đã chọn
    const byId = new Map(groups.map((g) => [g.id, g]));
    cauHinh.nhom = cauHinh.nhom.map((g) => ({ id: g.id, ten: byId.get(g.id)?.ten || g.ten }));
    info(`Đã tải ${groups.length} nhóm.`, "info");
    renderGroups();
  } catch (e) { showError(e); }
  b.disabled = false;
  b.textContent = "Tải lại danh sách nhóm";
};

// ---------- nội dung & ảnh ----------
const cacMau = (t) => String(t || "").split(/^\s*-{3,}\s*$/m).map((s) => s.trim()).filter(Boolean);
function renderMsg() {
  const n = $("msg").value.length;
  const soMau = cacMau($("msg").value).length;
  $("msgCount").textContent = (n ? `${n} ký tự` : "") + (soMau > 1 ? ` · ${soMau} mẫu, xáo ngẫu nhiên` : "");
  $("imgCount").textContent = `${cauHinh.anh.length}/${anhToiDa} ảnh`;
  const locked = !!st.running;
  $("thumbs").innerHTML = cauHinh.anh.map((a, i) => `<figure class="thumb">
    <img src="/api/nhom/anh/${encodeURIComponent(a)}" alt="Ảnh đính kèm ${i + 1}">
    <button class="btn sm danger" data-del="${esc(a)}" aria-label="Xoá ảnh ${i + 1}" ${locked ? "disabled" : ""}>Xoá</button>
  </figure>`).join("") || `<p class="hint" style="margin:6px 0 0">Chưa có ảnh (không bắt buộc).</p>`;
  $("btnAddImg").disabled = locked || cauHinh.anh.length >= anhToiDa;
  $("msg").disabled = locked;
  $("btnAddMau").disabled = locked;
  renderRunControls();
}
const saveMsg = debounce(async () => {
  try {
    await api("/api/nhom/cau-hinh", { noiDung: $("msg").value });
    flash("msgSaved", "Đã lưu");
  } catch (e) { showError(e); }
}, 700);
$("msg").addEventListener("input", () => {
  cauHinh.noiDung = $("msg").value.trim();
  renderMsg();
  saveMsg();
});
$("btnAddMau").onclick = () => {
  const t = $("msg");
  if (t.value.trim()) t.value = `${t.value.trimEnd()}\n---\n`;
  t.focus();
  t.selectionStart = t.selectionEnd = t.value.length;
  t.dispatchEvent(new Event("input"));
};
$("btnAddImg").onclick = () => $("imgInput").click();
$("imgInput").onchange = async () => {
  const files = [...$("imgInput").files];
  $("imgInput").value = "";
  if (!files.length) return;
  const fd = new FormData();
  files.slice(0, anhToiDa - cauHinh.anh.length).forEach((f) => fd.append("anh", f));
  $("btnAddImg").disabled = true;
  try {
    cauHinh = (await api("/api/nhom/anh", fd)).cauHinh;
  } catch (e) { showError(e); }
  renderMsg();
};
$("thumbs").addEventListener("click", async (e) => {
  const ten = e.target.dataset.del;
  if (!ten) return;
  try {
    cauHinh = (await api("/api/nhom/anh/xoa", { ten })).cauHinh;
    renderMsg();
  } catch (err) { showError(err); }
});

// ---------- gửi ----------
const fmtWait = () => {
  const base = `Mỗi nhóm cách nhau ${config.choToiThieuGiay}–${config.choToiDaGiay} giây (ngẫu nhiên).`;
  return config.nghiSauSoLan ? `${base} Cứ ${config.nghiSauSoLan} lần gửi thì nghỉ ${config.nghiPhut} phút.` : base;
};
function renderRunControls() {
  const n = cauHinh.nhom.length;
  const coNoiDung = !!(cauHinh.noiDung || cauHinh.anh.length);
  const loggedIn = st.login === "in";
  $("timing").textContent = fmtWait();
  $("btnStart").classList.toggle("hidden", !!st.running);
  $("btnStop").classList.toggle("hidden", !st.running);
  $("btnStop").disabled = !!st.stopRequested;
  $("btnStop").textContent = st.stopRequested ? "Đang dừng..." : "Dừng";
  $("btnStart").disabled = !loggedIn || !n || !coNoiDung || st.worker === false;
  $("runHint").textContent = st.running ? (st.kind === "ca-nhan" ? "Đang có đợt gửi từng người chạy bằng Zalo này." : "")
    : !loggedIn ? "Kết nối Zalo của bạn ở khung “Zalo của tôi” trước khi gửi."
    : !n ? "Chọn ít nhất một nhóm ở bước 1."
    : !coNoiDung ? "Soạn nội dung hoặc thêm ảnh ở bước 2."
    : `Sẽ gửi vào ${n} nhóm${cauHinh.anh.length ? `, kèm ${cauHinh.anh.length} ảnh` : ""}.`;
  if (st.running) $("confirmBox").classList.add("hidden");
  $("btnLoadGroups").disabled = !loggedIn || $("btnLoadGroups").textContent === "Đang tải...";
}
$("btnStart").onclick = () => {
  const n = cauHinh.nhom.length;
  const anh = cauHinh.anh.length ? ` kèm <b>${cauHinh.anh.length} ảnh</b>` : "";
  $("confirmText").innerHTML = `Gửi nội dung${anh} vào <b>${n} nhóm</b> bằng Zalo <b>${esc(st.account)}</b>?<br><span class="hint">${esc(fmtWait())}</span>`;
  $("confirmBox").classList.remove("hidden");
};
$("btnCancelConfirm").onclick = () => $("confirmBox").classList.add("hidden");
$("btnConfirm").onclick = async () => {
  $("confirmBox").classList.add("hidden");
  try { await api("/api/nhom/gui", {}); } catch (e) { showError(e); }
};
$("btnStop").onclick = async () => { try { await api("/api/stop", {}); } catch (e) { showError(e); } };

function waitText() {
  const s = Math.max(0, Math.round((st.waitUntil - Date.now()) / 1000));
  if (st.stopRequested) return "Đang dừng...";
  if (st.waitBreak) return `Đang nghỉ giữa lượt, còn ${Math.floor(s / 60)} phút ${s % 60} giây`;
  return `Chờ ${s} giây trước lần gửi tiếp theo`;
}
setInterval(() => {
  if (st.running && st.waitUntil && !st.current) $("runNow").textContent = waitText();
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
}
$("zConnect").onclick = async () => {
  try { await api("/api/zalo/login", { qr: false }); info("Đang tạo mã QR, chờ vài giây...", "info"); } catch (e) { showError(e); }
};
$("zCancel").onclick = async () => { try { await api("/api/zalo/login/cancel", {}); } catch (e) { showError(e); } };

function renderState() {
  renderZalo();
  $("workerAlert").classList.toggle("hidden", st.worker !== false);
  $("runBox").classList.toggle("hidden", !st.running);
  if (st.running && st.progress) {
    $("runCount").textContent = `${st.progress.i}/${st.progress.total}`;
    $("runBar").style.width = `${(st.progress.i / st.progress.total) * 100}%`;
    if (st.current) $("runNow").textContent = st.kind === "nhom" ? `Đang gửi vào nhóm “${st.current}”` : `Đang gửi ${st.current}`;
    else if (st.waitUntil) $("runNow").textContent = waitText();
  }
  // Bắt đầu / kết thúc đợt gửi thì khoá / mở các ô nhập
  if (renderState.wasRunning !== !!st.running) { renderGroups(); renderMsg(); }
  else renderRunControls();
  renderState.wasRunning = !!st.running;
}

// ---------- nhật ký & lịch sử ----------
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
async function loadHistory() {
  try {
    const { rows } = await api("/api/nhom/lich-su");
    $("hBody").innerHTML = rows.map((h) => `<tr>
      <td class="nowrap">${esc(h.thoiGian)}</td>
      <td>${esc(h.tenNhom || h.nhomId)}</td>
      <td>${h.thanhCong ? '<span class="pill ok">Đã gửi</span>' : `<span class="pill bad">${esc(h.ketQua)}</span>`}</td>
      <td class="msg-cell">${esc(h.noiDung)}</td>
      <td class="n">${h.soAnh || ""}</td>
      <td>${esc(h.taiKhoanZalo)}</td>
      <td><span class="note" style="white-space:normal;max-width:220px">${esc(h.ghiChu)}</span></td>
    </tr>`).join("");
    $("hEmpty").classList.toggle("hidden", rows.length > 0);
  } catch (e) { showError(e); }
}

// ---------- cập nhật định kỳ ----------
async function poll() {
  try {
    const r = await api(`/api/poll?log=${lastLogId}`);
    $("connAlert").classList.add("hidden");
    st = r;
    renderState();
    r.logs.forEach(addLog);
    if (version !== null && r.version !== version) loadHistory();
    version = r.version;
  } catch {
    $("connAlert").classList.remove("hidden");
  }
  setTimeout(poll, document.hidden ? 5000 : 1500);
}

(async () => {
  try {
    const s = await api("/api/nhom/state");
    st = s;
    config = s.config;
    cauHinh = s.cauHinh;
    anhToiDa = s.anhToiDa;
    version = s.version;
    $("msg").value = cauHinh.noiDung;
    s.logs.forEach(addLog);
    renderGroups();
    renderMsg();
    renderState();
    loadHistory();
  } catch (e) {
    $("connAlert").classList.remove("hidden");
    showError(e);
  }
  poll();
})();
