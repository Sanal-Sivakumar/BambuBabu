/**
 * BambuBabu — Frontend App
 * Polls the FastAPI backend every 5 seconds and updates the UI.
 */

const API = "";
let currentFilter = "all";
let allJobs = [];
let pollingInterval = null;

// ── Tab navigation ─────────────────────────────────────────────────────────

document.querySelectorAll(".nav-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
  });
});

// ── File upload drag-drop ──────────────────────────────────────────────────

const dropZone   = document.getElementById("drop-zone");
const fileInput  = document.getElementById("file-input");
const filePreview = document.getElementById("file-preview");
const submitBtn  = document.getElementById("submit-btn");
let selectedFile = null;

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", e => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

dropZone.addEventListener("dragover", e => {
  e.preventDefault();
  dropZone.classList.add("drag-over");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("drag-over");
  const file = e.dataTransfer.files[0];
  if (file) setFile(file);
});

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) setFile(fileInput.files[0]);
});

document.getElementById("remove-file").addEventListener("click", () => clearFile());

function setFile(file) {
  if (!file.name.toLowerCase().endsWith(".stl")) {
    toast("Only .stl files are accepted", "error");
    return;
  }
  selectedFile = file;
  document.getElementById("file-name-display").textContent = file.name;
  document.getElementById("file-size-display").textContent = formatBytes(file.size);
  dropZone.classList.add("hidden");
  filePreview.classList.remove("hidden");
  checkSubmitReady();
}

function clearFile() {
  selectedFile = null;
  fileInput.value = "";
  dropZone.classList.remove("hidden");
  filePreview.classList.add("hidden");
  submitBtn.disabled = true;
}

function checkSubmitReady() {
  const name  = document.getElementById("user-name").value.trim();
  const email = document.getElementById("user-email").value.trim();
  submitBtn.disabled = !(selectedFile && name && email);
}

["user-name", "user-email"].forEach(id =>
  document.getElementById(id).addEventListener("input", checkSubmitReady)
);

// ── Upload form submit ─────────────────────────────────────────────────────

document.getElementById("upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!selectedFile) return;

  const label   = submitBtn.querySelector(".btn-label");
  const spinner = submitBtn.querySelector(".btn-spinner");
  label.classList.add("hidden");
  spinner.classList.remove("hidden");
  submitBtn.disabled = true;

  const fd = new FormData();
  fd.append("file",        selectedFile);
  fd.append("user_name",   document.getElementById("user-name").value.trim());
  fd.append("user_email",  document.getElementById("user-email").value.trim());
  fd.append("description", document.getElementById("description").value.trim());

  try {
    const res  = await fetch(`${API}/api/jobs`, { method: "POST", body: fd });
    const data = await res.json();

    if (res.ok) {
      showUploadResult(`✅ Job submitted! ID: ${data.job_id.substring(0, 8)} — Slicing will begin shortly.`, "success");
      document.getElementById("upload-form").reset();
      clearFile();
      toast("Job created!", "success");
      // Switch to queue tab
      setTimeout(() => {
        document.querySelector('[data-tab="queue"]').click();
      }, 1200);
    } else {
      showUploadResult(`❌ Error: ${data.detail || JSON.stringify(data)}`, "error");
      toast("Upload failed", "error");
    }
  } catch (err) {
    showUploadResult(`❌ Network error: ${err.message}`, "error");
    toast("Cannot reach BambuBabu server", "error");
  }

  label.classList.remove("hidden");
  spinner.classList.add("hidden");
  checkSubmitReady();
});

function showUploadResult(msg, type) {
  const el = document.getElementById("upload-result");
  el.textContent = msg;
  el.className = `upload-result ${type}`;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 8000);
}

// ── Filter buttons ─────────────────────────────────────────────────────────

document.querySelectorAll(".filter-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentFilter = btn.dataset.filter;
    renderJobs();
  });
});

// ── Polling ────────────────────────────────────────────────────────────────

async function poll() {
  await Promise.all([fetchJobs(), fetchPrinters(), fetchLogs(), fetchHealth()]);
}

async function fetchJobs() {
  try {
    const res = await fetch(`${API}/api/jobs`);
    if (!res.ok) return;
    allJobs = await res.json();
    renderJobs();
    updateQueueBadge();
  } catch (_) {}
}

async function fetchPrinters() {
  try {
    const res = await fetch(`${API}/api/printers`);
    if (!res.ok) return;
    const printers = await res.json();
    renderPrinters(printers);
  } catch (_) {}
}

async function fetchHealth() {
  try {
    const res = await fetch(`${API}/api/health`);
    const data = res.ok ? await res.json() : null;
    checkHealth(Boolean(data && data.status === "ok"));
  } catch (_) { checkHealth(false); }
}

async function fetchLogs() {
  try {
    const res = await fetch(`${API}/api/logs/all?limit=80`);
    if (!res.ok) return;
    const logs = await res.json();
    renderLogs(logs);
  } catch (_) {}
}

async function checkHealth(ok) {
  const dot  = document.getElementById("api-dot");
  const text = document.getElementById("api-status-text");
  if (ok) {
    dot.className  = "dot online";
    text.textContent = "Online";
  } else {
    dot.className  = "dot offline";
    text.textContent = "Offline";
  }
}

// ── Render jobs ────────────────────────────────────────────────────────────

function renderJobs() {
  const list = document.getElementById("job-list");
  let jobs = allJobs;

  if (currentFilter !== "all") {
    jobs = jobs.filter(j => j.status.startsWith(currentFilter));
  }

  if (!jobs.length) {
    list.innerHTML = `<div class="empty-state"><div>📭</div><div>No jobs to show.</div></div>`;
    return;
  }

  list.innerHTML = jobs.map(j => jobCard(j)).join("");
}

function jobCard(j) {
  const printerLabel = j.assigned_printer
    ? (j.assigned_printer === "p1s" ? "🖥️ P1S" : "🖨️ A1 Mini")
    : "—";

  const progress = (j.status === "printing" || j.status === "completed")
    ? `<div class="progress-bar-wrap">
         <div class="progress-bar" style="width:${j.print_progress || 0}%"></div>
       </div>`
    : "";

  const score = j.complexity_score != null
    ? `Score: ${j.complexity_score.toFixed(1)}`
    : "";

  const submitted = j.submitted_at
    ? `Submitted ${timeAgo(j.submitted_at)}`
    : "";

  const estTime = j.estimated_minutes
    ? `~${j.estimated_minutes} min`
    : "";

  return `
    <div class="job-card ${j.status}">
      <div class="job-status-dot dot-${j.status}"></div>
      <div class="job-meta">
        <div class="job-filename" title="${esc(j.original_filename)}">${esc(j.original_filename)}</div>
        <div class="job-sub">
          <span>👤 ${esc(j.user_name)}</span>
          ${printerLabel ? `<span>${printerLabel}</span>` : ""}
          ${score        ? `<span>🧠 ${score}</span>` : ""}
          ${estTime      ? `<span>⏱️ ${estTime}</span>` : ""}
          ${submitted    ? `<span>🕒 ${submitted}</span>` : ""}
        </div>
        ${j.status === "printing" ? `
          <div style="margin-top:8px">
            <div class="printer-progress-label">
              <span>Print Progress</span><span>${j.print_progress || 0}%</span>
            </div>
            ${progress}
          </div>` : ""}
        ${j.error_message ? `<div style="color:var(--red);font-size:12px;margin-top:4px">❗ ${esc(j.error_message)}</div>` : ""}
        ${j.rejection_reason ? `<div style="color:var(--red);font-size:12px;margin-top:4px">🚫 ${esc(j.rejection_reason)}</div>` : ""}
      </div>
      <div class="job-right">
        <span class="status-badge s-${j.status}">${j.status}</span>
        <span style="font-size:11px;color:var(--text-muted);font-family:ui-monospace,'SFMono-Regular',Consolas,monospace">${j.id.substring(0, 8)}</span>
      </div>
    </div>
  `;
}

function updateQueueBadge() {
  const active = allJobs.filter(j =>
    ["pending", "analysing", "slicing", "queued", "uploading", "starting", "printing", "attention"].includes(j.status)
  ).length;
  const badge = document.getElementById("badge-queue");
  badge.textContent = active || "";
}

// ── Render printers ────────────────────────────────────────────────────────

function renderPrinters(printers) {
  const grid = document.getElementById("printer-grid");
  const alert = document.getElementById("plate-alert");
  const anyNeedsClearing = printers.some(p => !p.plate_cleared);

  if (anyNeedsClearing) {
    alert.classList.remove("hidden");
  } else {
    alert.classList.add("hidden");
  }

  grid.innerHTML = printers.map(p => printerCard(p)).join("");

  // Attach plate-cleared button handlers
  printers.forEach(p => {
    const btn = document.getElementById(`plate-btn-${p.printer_id}`);
    if (btn) {
      btn.addEventListener("click", () => clearPlate(p.printer_id));
    }
    const acknowledgeBtn = document.getElementById(`idle-btn-${p.printer_id}`);
    if (acknowledgeBtn) {
      acknowledgeBtn.addEventListener("click", () => acknowledgeIdle(p.printer_id));
    }
  });
}

function printerCard(p) {
  const needsClear = !p.plate_cleared;
  const isOnline   = p.connected;

  const progress = p.status === "printing"
    ? `<div class="printer-progress-label">
         <span>Progress</span><span>${p.progress}%</span>
       </div>
       <div class="progress-bar-wrap">
         <div class="progress-bar" style="width:${p.progress}%"></div>
       </div>`
    : "";

  const plateBtnSection = needsClear
    ? `<div class="plate-btn-wrap">
         <button class="plate-clear-btn" id="plate-btn-${p.printer_id}">
           🗑️ Plate Cleared — Start Next Job
         </button>
       </div>`
    : "";

  const idleAcknowledgeSection = p.connected && p.status === "error" &&
      p.gcode_state === "FAILED" && !p.current_job_id && p.plate_cleared
    ? `<div class="plate-btn-wrap">
         <button class="plate-clear-btn" id="idle-btn-${p.printer_id}">
           ✓ I Inspected It — Printer Is Idle
         </button>
       </div>`
    : "";

  const currentJobHtml = p.current_job_id
    ? `<div style="font-size:12px;color:var(--text-muted);margin-top:4px">
         Job: <code style="color:var(--purple)">${p.current_job_id.substring(0, 8)}</code>
       </div>`
    : "";

  return `
    <div class="printer-card ${needsClear ? "needs-clear" : ""}">
      <div class="printer-header">
        <div>
          <div class="printer-title">${esc(p.name)}</div>
          ${currentJobHtml}
        </div>
        <span class="printer-status-badge s-${p.status}">
          ${isOnline ? p.status : "offline"}
        </span>
      </div>

      <div class="printer-stats">
        <div class="stat-box">
          <div class="stat-label">🌡️ Nozzle</div>
          <div class="stat-value">
            ${p.nozzle_temp ? p.nozzle_temp.toFixed(0) : "—"}
            ${p.nozzle_temp ? '<span class="stat-unit">°C</span>' : ""}
          </div>
        </div>
        <div class="stat-box">
          <div class="stat-label">🛏️ Bed</div>
          <div class="stat-value">
            ${p.bed_temp ? p.bed_temp.toFixed(0) : "—"}
            ${p.bed_temp ? '<span class="stat-unit">°C</span>' : ""}
          </div>
        </div>
        <div class="stat-box">
          <div class="stat-label">🔗 Connection</div>
          <div class="stat-value" style="font-size:14px;font-weight:600">
            <span style="color:${isOnline ? "var(--green)" : "var(--text-muted)"}">
              ${isOnline ? "● Online" : "● Offline"}
            </span>
          </div>
        </div>
        <div class="stat-box">
          <div class="stat-label">🗑️ Plate</div>
          <div class="stat-value" style="font-size:14px;font-weight:600">
            <span style="color:${p.plate_cleared ? "var(--green)" : "var(--amber)"}">
              ${p.plate_cleared ? "✅ Clear" : "⚠️ Needs Clear"}
            </span>
          </div>
        </div>
      </div>

      ${progress}
      ${plateBtnSection}
      ${idleAcknowledgeSection}
    </div>
  `;
}

async function clearPlate(printerId) {
  if (!window.confirm("Confirm that the model has been physically removed and the plate is safe.")) return;
  try {
    const res = await fetch(`${API}/api/printers/${printerId}/plate-cleared`, {
      method: "POST",
    });
    const data = await res.json();
    if (res.ok) {
      toast(`✅ ${data.message}`, "success");
      await fetchPrinters();
    } else {
      toast(`❌ ${data.detail}`, "error");
    }
  } catch (err) {
    toast("Network error", "error");
  }
}

async function acknowledgeIdle(printerId) {
  if (!window.confirm(
    "Confirm the printer is physically idle, cool, motionless, has no active job, and its plate is clear."
  )) return;
  try {
    const res = await fetch(`${API}/api/printers/${printerId}/acknowledge-idle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ physically_idle: true }),
    });
    const data = await res.json();
    if (res.ok) {
      toast(`✅ ${data.message}`, "success");
      await fetchPrinters();
    } else {
      toast(`❌ ${data.detail}`, "error");
    }
  } catch (_err) {
    toast("Network error", "error");
  }
}

// ── Render logs ────────────────────────────────────────────────────────────

function renderLogs(logs) {
  const list = document.getElementById("log-list");
  if (!logs.length) {
    list.innerHTML = `<div class="empty-state"><div>📜</div><div>No logs yet.</div></div>`;
    return;
  }
  list.innerHTML = logs.map(l => `
    <div class="log-entry">
      <span class="log-time">${formatTime(l.timestamp)}</span>
      <span class="log-level ${l.level}">${l.level}</span>
      <span class="log-event">${esc(l.event)}</span>
      <span class="log-msg" title="${esc(l.message)}">${esc(l.message)}</span>
    </div>
  `).join("");
}

// ── Toast ──────────────────────────────────────────────────────────────────

function toast(msg, type = "info") {
  const container = document.getElementById("toast-container");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ── Helpers ────────────────────────────────────────────────────────────────

function esc(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatBytes(bytes) {
  if (bytes < 1024)       return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

function timeAgo(isoStr) {
  const diff = Math.floor((Date.now() - new Date(isoStr)) / 1000);
  if (diff < 60)    return `${diff}s ago`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatTime(isoStr) {
  const d = new Date(isoStr);
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// ── Init ───────────────────────────────────────────────────────────────────

poll();                                  // immediate first fetch
pollingInterval = setInterval(poll, 5000); // then every 5 seconds

// Show offline on start, update after first fetch
document.getElementById("api-dot").className = "dot";
