let trendChart = null;

function ghs(n) {
  return "GH₵" + (Number(n) || 0).toLocaleString("en-GH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function timeAgo(iso) {
  if (!iso) return "never";
  const then = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const secs = Math.floor((Date.now() - then.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return Math.floor(secs / 60) + "m ago";
  if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
  return Math.floor(secs / 86400) + "d ago";
}

function setTable(tbodyId, emptyId, rows, rowFn) {
  const tbody = document.querySelector(`#${tbodyId} tbody`);
  const empty = document.getElementById(emptyId);
  tbody.innerHTML = "";
  if (!rows.length) {
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";
  for (const r of rows) tbody.insertAdjacentHTML("beforeend", rowFn(r));
}

function renderTrend(trend) {
  const labels = trend.map(t => t.date.slice(5));
  const data = trend.map(t => t.total_revenue);
  const ctx = document.getElementById("trend-chart").getContext("2d");
  if (trendChart) { trendChart.destroy(); }
  trendChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Revenue",
        data,
        borderColor: "#4C7CF3",
        backgroundColor: "rgba(76,124,243,.15)",
        fill: true,
        tension: 0.3,
        pointRadius: 2,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: "#262E45" }, ticks: { color: "#8A93AC" } },
        y: { grid: { color: "#262E45" }, ticks: { color: "#8A93AC" } },
      },
    },
  });
}

async function refresh() {
  try {
    const r = await fetch("/api/dashboard/data");
    const d = await r.json();
    if (!d.ok) {
      if (r.status === 401) window.location.href = "/login";
      return;
    }

    document.getElementById("last-seen").textContent = "Last synced " + timeAgo(d.last_seen);
    document.getElementById("kpi-revenue").textContent = ghs(d.today.revenue);
    document.getElementById("kpi-sales").textContent = d.today.num_sales;
    document.getElementById("kpi-discounts").textContent = ghs(d.today.discounts);
    document.getElementById("kpi-lowstock").textContent = d.low_stock_count;

    renderTrend(d.trend);

    setTable("cashier-table", "cashier-empty", d.today.by_cashier, c => `
      <tr><td>${c.cashier || "—"}</td><td>${c.txn}</td><td>${ghs(c.revenue)}</td></tr>`);

    setTable("recent-table", "recent-empty", d.recent_sales, s => `
      <tr>
        <td>${s.invoice_number || "—"}</td>
        <td>${s.customer_name || "Walk-in"}</td>
        <td>${s.cashier || "—"}</td>
        <td>${ghs(s.total)}</td>
        <td>${s.payment_method || "—"}</td>
        <td>${s.created_at ? s.created_at.slice(0, 16).replace("T", " ") : "—"}</td>
      </tr>`);

    setTable("lowstock-table", "lowstock-empty", d.low_stock, p => `
      <tr>
        <td>${p.name || "—"}</td>
        <td>${p.sku || "—"}</td>
        <td><span class="badge ${p.quantity <= 0 ? "badge-red" : "badge-amber"}">${p.quantity} ${p.unit || ""}</span></td>
        <td>${p.reorder_level}</td>
      </tr>`);
  } catch (e) {
    console.error("dashboard refresh failed", e);
  }
}

refresh();
setInterval(refresh, 15000);
