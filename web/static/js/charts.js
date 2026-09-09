/* Minimal dependency-free SVG charting for the Attestra console.
   Line charts with multiple series, hover tooltips, and legends. */

"use strict";

const AttestraCharts = (() => {
  const NS = "http://www.w3.org/2000/svg";
  const PALETTE = ["#33557a", "#c05621", "#1f7a4d", "#7b5ea7", "#a33a2e", "#5a6b82"];

  function el(tag, attrs = {}, parent = null) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceTicks(min, max, count = 5) {
    if (max === min) max = min + 1;
    const span = max - min;
    const step0 = span / count;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const norm = step0 / mag;
    const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
    const start = Math.floor(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * 0.5; v += step) ticks.push(v);
    return ticks;
  }

  function fmt(v) {
    if (Math.abs(v) >= 10000) return (v / 1000).toFixed(0) + "k";
    if (Math.abs(v) >= 1000) return (v / 1000).toFixed(1) + "k";
    if (Math.abs(v) >= 10) return Math.round(v).toString();
    return v.toFixed(1);
  }

  /**
   * Render a line chart.
   * container: DOM node. opts:
   *   series: [{name, data: [{x, y}], color?, dashed?}]
   *   xLabel, yLabel, yUnit (for tooltip), logY (bool)
   */
  function lineChart(container, opts) {
    container.innerHTML = "";
    const W = container.clientWidth || 560;
    const H = opts.height || 280;
    const pad = { top: 16, right: 18, bottom: 42, left: 58 };
    const iw = W - pad.left - pad.right;
    const ih = H - pad.top - pad.bottom;

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart" }, container);
    svg.style.width = "100%";

    const all = opts.series.flatMap((s) => s.data);
    if (!all.length) {
      const t = el("text", { x: W / 2, y: H / 2, class: "chart-empty", "text-anchor": "middle" }, svg);
      t.textContent = "No data yet";
      return;
    }
    const xs = all.map((p) => p.x);
    const ys = all.map((p) => p.y);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    let yMin = opts.logY ? Math.min(...ys) : 0;
    let yMax = Math.max(...ys);

    const xScale = (x) => pad.left + ((x - xMin) / (xMax - xMin || 1)) * iw;
    const yScale = opts.logY
      ? (y) => pad.top + ih - ((Math.log10(y) - Math.log10(yMin)) / (Math.log10(yMax) - Math.log10(yMin) || 1)) * ih
      : (y) => pad.top + ih - ((y - yMin) / (yMax - yMin || 1)) * ih;

    // grid + y axis
    const yTicks = opts.logY
      ? (() => {
          const ticks = [];
          for (let e = Math.floor(Math.log10(yMin)); e <= Math.ceil(Math.log10(yMax)); e++) ticks.push(Math.pow(10, e));
          return ticks;
        })()
      : niceTicks(yMin, yMax);
    for (const t of yTicks) {
      if (t < yMin - 1e-9 || t > yMax * (opts.logY ? 1.001 : 1.15)) continue;
      const y = yScale(t);
      el("line", { x1: pad.left, x2: W - pad.right, y1: y, y2: y, class: "chart-grid" }, svg);
      const lbl = el("text", { x: pad.left - 8, y: y + 4, "text-anchor": "end", class: "chart-tick" }, svg);
      lbl.textContent = fmt(t);
    }
    // x axis ticks: use the union of data x values (they are sparse and meaningful)
    const xTicks = [...new Set(xs)].sort((a, b) => a - b);
    const maxLabels = 10;
    const stride = Math.ceil(xTicks.length / maxLabels);
    xTicks.forEach((t, i) => {
      if (i % stride !== 0 && i !== xTicks.length - 1) return;
      const x = xScale(t);
      el("line", { x1: x, x2: x, y1: pad.top + ih, y2: pad.top + ih + 4, class: "chart-axis" }, svg);
      const lbl = el("text", { x, y: pad.top + ih + 18, "text-anchor": "middle", class: "chart-tick" }, svg);
      lbl.textContent = t;
    });
    el("line", { x1: pad.left, x2: W - pad.right, y1: pad.top + ih, y2: pad.top + ih, class: "chart-axis" }, svg);

    if (opts.xLabel) {
      const t = el("text", { x: pad.left + iw / 2, y: H - 6, "text-anchor": "middle", class: "chart-label" }, svg);
      t.textContent = opts.xLabel;
    }
    if (opts.yLabel) {
      const t = el("text", {
        x: 14, y: pad.top + ih / 2, class: "chart-label",
        transform: `rotate(-90 14 ${pad.top + ih / 2})`, "text-anchor": "middle",
      }, svg);
      t.textContent = opts.yLabel;
    }

    // series
    opts.series.forEach((s, i) => {
      const color = s.color || PALETTE[i % PALETTE.length];
      const sorted = [...s.data].sort((a, b) => a.x - b.x);
      const d = sorted.map((p, j) => `${j ? "L" : "M"}${xScale(p.x).toFixed(1)},${yScale(p.y).toFixed(1)}`).join(" ");
      el("path", {
        d, fill: "none", stroke: color, "stroke-width": 2.2,
        "stroke-linejoin": "round", "stroke-linecap": "round",
        ...(s.dashed ? { "stroke-dasharray": "6 4" } : {}),
      }, svg);
      for (const p of sorted) {
        el("circle", { cx: xScale(p.x), cy: yScale(p.y), r: 3.2, fill: "#fff", stroke: color, "stroke-width": 2 }, svg);
      }
    });

    // legend
    const legend = document.createElement("div");
    legend.className = "chart-legend";
    opts.series.forEach((s, i) => {
      const item = document.createElement("span");
      item.className = "chart-legend-item";
      item.innerHTML = `<i style="background:${s.color || PALETTE[i % PALETTE.length]}"></i>${s.name}`;
      legend.appendChild(item);
    });
    container.appendChild(legend);

    // tooltip
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    container.style.position = "relative";
    container.appendChild(tip);

    const points = opts.series.flatMap((s, i) =>
      s.data.map((p) => ({ ...p, name: s.name, color: s.color || PALETTE[i % PALETTE.length] })));

    svg.addEventListener("mousemove", (ev) => {
      const rect = svg.getBoundingClientRect();
      const mx = ((ev.clientX - rect.left) / rect.width) * W;
      const my = ((ev.clientY - rect.top) / rect.height) * H;
      let best = null, bd = 1e9;
      for (const p of points) {
        const d = Math.hypot(xScale(p.x) - mx, yScale(p.y) - my);
        if (d < bd) { bd = d; best = p; }
      }
      if (best && bd < 36) {
        tip.hidden = false;
        tip.innerHTML = `<strong>${best.name}</strong>${opts.xLabel ? ` · ${opts.xLabel.split("(")[0].trim()} ${best.x}` : ""}<br>${best.y.toLocaleString()} ${opts.yUnit || ""}`;
        const left = (xScale(best.x) / W) * rect.width;
        const top = (yScale(best.y) / H) * rect.height;
        tip.style.left = Math.min(left + 12, rect.width - 150) + "px";
        tip.style.top = Math.max(top - 44, 4) + "px";
      } else tip.hidden = true;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; });
  }

  /** Simple horizontal stat bars: [{label, value, color?, note?}] */
  function barList(container, rows, unit) {
    container.innerHTML = "";
    const max = Math.max(...rows.map((r) => r.value), 1);
    for (const [i, r] of rows.entries()) {
      const row = document.createElement("div");
      row.className = "barlist-row";
      row.innerHTML =
        `<span class="barlist-label">${r.label}</span>` +
        `<span class="barlist-track"><i style="width:${(r.value / max) * 100}%;background:${r.color || PALETTE[i % PALETTE.length]}"></i></span>` +
        `<span class="barlist-value">${r.value.toLocaleString()} ${unit || ""}${r.note ? ` <small>${r.note}</small>` : ""}</span>`;
      container.appendChild(row);
    }
  }

  /** Donut with center label. parts: [{label, value, color}] */
  function donut(container, parts, centerBig, centerSmall) {
    container.innerHTML = "";
    const size = 168, r = 64, cx = size / 2, cy = size / 2;
    const total = parts.reduce((a, p) => a + p.value, 0) || 1;
    const svg = el("svg", { viewBox: `0 0 ${size} ${size}`, class: "chart-donut" }, container);
    let a0 = -Math.PI / 2;
    for (const p of parts) {
      const frac = p.value / total;
      const a1 = a0 + frac * Math.PI * 2;
      if (frac > 0) {
        const large = frac > 0.5 ? 1 : 0;
        const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
        const x1 = cx + r * Math.cos(a1 - 0.0001), y1 = cy + r * Math.sin(a1 - 0.0001);
        el("path", {
          d: `M${x0},${y0} A${r},${r} 0 ${large} 1 ${x1},${y1}`,
          fill: "none", stroke: p.color, "stroke-width": 16,
        }, svg);
      }
      a0 = a1;
    }
    const big = el("text", { x: cx, y: cy - 2, "text-anchor": "middle", class: "donut-big" }, svg);
    big.textContent = centerBig;
    const small = el("text", { x: cx, y: cy + 18, "text-anchor": "middle", class: "donut-small" }, svg);
    small.textContent = centerSmall;
  }

  return { lineChart, barList, donut, PALETTE };
})();
