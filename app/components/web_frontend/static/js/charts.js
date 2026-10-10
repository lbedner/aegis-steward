/* Chart island (the first of two JS islands; the other is chat).

   The server renders a <canvas data-chart="line|bar|doughnut"> next to a
   JSON block (see the chart_panel macro); this file mounts Chart.js over
   every such canvas on load and after each htmx settle. Chart.js itself
   is fetched on first use, so pages without charts pay nothing and a
   swap into a chart page still works. */

const CHART_JS = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.js';
const RAMP = 8; // --aegis-chart-1 .. -8 in tailwind.config.js; cycles past that

// Colors come from the active theme's variables, read at mount time so a
// theme switch repaints charts in the new palette. DaisyUI stores its
// colors as oklch triplets (--p, --n, ...); the chart ramp is literal hex,
// its alpha a byte on the end.
function token(name, alpha) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!name.startsWith('--aegis-chart-')) return `oklch(${value} / ${alpha ?? 1})`;
  return alpha === undefined ? value : `${value}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`;
}
function rampColor(i, alpha) {
  return token(`--aegis-chart-${(i % RAMP) + 1}`, alpha);
}

let chartJsLoading = null;
function ensureChartJs() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (!chartJsLoading) {
    chartJsLoading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = CHART_JS;
      script.onload = () => resolve(window.Chart);
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }
  return chartJsLoading;
}

function money(value) {
  const abs = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return `${value < 0 ? '-' : ''}$${abs}`;
}

// An axis label: a round number, so no cents, and thousands as K - at
// $18,000.00 a phone's y-axis took a third of the chart (issue 474). The
// tooltip keeps the exact figure.
const compactMoney = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
});

// Formats whose axis steps in whole byte units.
const BYTE_FORMATS = new Set(['bytes', 'bytes_per_second']);

// The same units as format_bytes in app/core/formatting.py.
function bytes(value) {
  if (value < 1024) return `${Math.trunc(value)} B`;
  let size = value;
  for (const unit of ['KB', 'MB', 'GB', 'TB']) {
    size /= 1024;
    if (size < 1024 || unit === 'TB') return `${size.toFixed(1)} ${unit}`;
  }
  return `${size.toFixed(1)} TB`;
}

// Tooltips (and every axis but money's): plain numbers unless the format
// says how to read them (``"money" | "percent" | "bytes" |
// "bytes_per_second" | "seconds"``).
function formatValue(value, format) {
  if (format === 'money') return money(value);
  if (format === 'percent') return `${Number(value).toFixed(1)}%`;
  if (format === 'bytes') return bytes(value);
  if (format === 'bytes_per_second') return `${bytes(value)}/s`;
  if (format === 'seconds') return value < 10 ? `${Number(value).toFixed(1)} s` : `${Math.round(value)} s`;
  return Number(value).toLocaleString();
}

// How a chart's figures read: what its data says, else dollars - every
// finance chart is money and says nothing - except a time chart
// (``series.chart``), which is plain unless it says.
function formatOf(data) {
  return data.format ?? (timed(data) ? null : 'money');
}

// ``"x": "time"``: the labels are epoch ms, shown on the viewer's clock,
// and each point is plotted at its own time ({x, y}), so a live chart can
// slide along as points come and go (see refresh).
// The tooltip reads the second; an axis tick, only the minute.
function clock(ms, seconds = true) {
  const parts = { hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}) };
  return new Date(ms).toLocaleTimeString([], parts);
}

// A time axis ticks on the clock: the first of these steps that gives at
// most six ticks, at its whole multiples (11:39, 11:42, ... for 15 minutes).
const TIME_STEPS = [60e3, 120e3, 180e3, 300e3, 600e3, 900e3, 1800e3, 3600e3];
function timeTicks(min, max) {
  const step = TIME_STEPS.find((s) => (max - min) / s <= 6) || TIME_STEPS[TIME_STEPS.length - 1];
  const ticks = [];
  for (let t = Math.ceil(min / step) * step; t <= max; t += step) ticks.push(t);
  return ticks;
}

// A byte axis steps in round amounts of one unit (5 GB, 50 MB), not
// whatever decimal step the raw byte count suggests; never under a whole
// byte, or a quiet disk's 0.3 B/s labels every tick "0 B/s".
function byteStep(max) {
  if (!(max > 0)) return undefined;
  const unit = 1024 ** Math.max(0, Math.min(Math.floor(Math.log(max) / Math.log(1024)), 4));
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((s) => max / unit / s <= 6) || 1000;
  return step * unit;
}
function highest(data) {
  return Math.max(0, ...data.series.flatMap((series) => series.values.filter((v) => v !== null)));
}
function timed(data) {
  return data.x === 'time';
}
// A time axis spans the data's ``window`` (from then to now) when it has
// one, its own first and last points otherwise.
function span(data) {
  return data.window || [data.labels[0], data.labels[data.labels.length - 1]];
}

// ``chart_panel(empty=...)``: said over the chart while its data has no
// ``points`` (counted by ``series.chart``).
function showEmpty(canvas, data) {
  canvas.parentElement?.querySelector('[data-chart-empty]')?.classList.toggle('hidden', data.points > 0);
}
function values(data, series) {
  return timed(data) ? series.values.map((y, i) => ({ x: data.labels[i], y })) : series.values;
}

// A bar's tone, decided by the server: the highest bright teal, the lowest
// violet, the rest a darker teal (Pulse's day-of-week chart).
function toneColor(tone) {
  const teal = token('--aegis-chart-1');
  if (tone === 'high') return teal;
  if (tone === 'low') return token('--aegis-chart-2');
  return `${teal}88`;
}

// The series that are lines of their own: not marker dots, not the
// dashed line another is read against.
function plainLines(data) {
  return data.series.filter((s) => !s.points && !s.compare);
}

// One line is the chart's subject, filled in the primary; several (assets
// and debts, account groups) each take a ramp colour, unfilled, so none
// hides another. A line may name its place in the ramp (``color``: a
// part's colour everywhere).
function datasets(kind, data) {
  const tail = token('--n'); // "Other" reads as tail, never as a category
  const single = kind === 'line' && plainLines(data).length === 1;
  // A line's place in the ramp: its own (``color``), else its order.
  const place = (i) => data.series[i].color ?? i;
  const line = (i) => (!single || data.series[i].color != null ? rampColor(place(i)) : token('--p'));
  // ``"style": "events"`` (see series.chart): dots, no line.
  const events = data.style === 'events';
  return data.series.map((series, i) => {
    if (series.points || events) return markers(data, series, events ? line(i) : token('--er'));
    if (series.compare) return comparison(series);
    if (series.tones) {
      return {
        label: series.label,
        data: series.values,
        backgroundColor: series.tones.map(toneColor),
        borderRadius: 3,
      };
    }
    return {
      label: series.label,
      data: values(data, series),
      backgroundColor:
        kind === 'doughnut'
          ? data.labels.map((label, j) => (label === 'Other' ? tail : rampColor(j)))
          : single
            ? token('--p', 0.15)
            : kind === 'line'
              ? line(i)
              : rampColor(i),
      borderColor: kind === 'line' ? line(i) : undefined,
      borderWidth: kind === 'doughnut' ? 0 : 2,
      fill: single,
      tension: 0.3,
      pointRadius: 0,
      // A time chart answers from anywhere over it (see the interaction
      // option in build); the point at the hovered time grows to show it.
      ...(timed(data) ? {
        pointHoverRadius: 5,
        pointHoverBorderWidth: 2,
        pointHoverBackgroundColor: token('--b2'),
        pointHoverBorderColor: line(i),
      } : {}),
    };
  }).map((set, i) => stacked(data, i, set, rampColor(place(i), 0.35))).concat(kind === 'line' ? guides(data) : []);
}

// A comparison series: the line another series is read against (the
// usual month under this one), dashed and muted so it never reads as data
// of its own.
function comparison(series) {
  return {
    label: series.label,
    data: series.values,
    borderColor: token('--n'),
    borderDash: [5, 4],
    borderWidth: 2,
    fill: false,
    tension: 0.3,
    pointRadius: 0,
  };
}

// ``"style": "stacked"`` (Resources): each line a band filled down to the
// one below, so the top edge is the total; a ``dashed`` line (the host's in
// use) stands apart, unstacked and unfilled, over them.
function stacked(data, i, set, fill) {
  if (data.style !== 'stacked') return set;
  if (data.series[i].dashed) {
    const color = token('--n');
    return { ...set, stack: 'dashed', fill: false, borderDash: [6, 4], borderWidth: 1.5,
      borderColor: color, backgroundColor: color, pointHoverBorderColor: color };
  }
  const below = data.series.slice(0, i).some((series) => !series.dashed);
  return { ...set, fill: below ? '-1' : 'origin', backgroundColor: fill, borderWidth: 1 };
}

// Where warning and alert begin (``thresholds``: the host checks' rule,
// the ones in reach of the data, from the server: series.thresholds_in_reach),
// as dashed lines across the window. Out of the legend and the tooltip.
const GUIDE_TONES = { warn: '--wa', error: '--er' };
function shownThresholds(data) {
  return timed(data) ? data.thresholds || [] : [];
}
function guideLine(data, threshold) {
  const [min, max] = span(data);
  return [{ x: min, y: threshold.value }, { x: max, y: threshold.value }];
}
function guides(data) {
  return shownThresholds(data).map((threshold) => ({
    guide: true,
    label: '',
    data: guideLine(data, threshold),
    borderColor: token(GUIDE_TONES[threshold.tone], 0.8),
    borderDash: [6, 4],
    borderWidth: 1,
    fill: false,
    tension: 0,
    pointRadius: 0,
    pointHoverRadius: 0,
  }));
}

// Dots where something happened, no line: a marker series
// (``{"points": true}``, the overdue days on a balance projection, in the
// error colour) or a chart of events (``"style": "events"``, in its own).
function markers(data, series, color) {
  return {
    label: series.label,
    data: values(data, series),
    showLine: false,
    pointRadius: 4,
    pointHoverRadius: 6,
    pointBackgroundColor: color,
    pointBorderColor: color,
    spanGaps: false,
  };
}

function drilldownHandler(canvas, data) {
  const base = canvas.dataset.drilldown;
  if (!base || !data.slices) return undefined;
  return (_event, elements) => {
    if (!elements.length) return;
    const slice = data.slices[elements[0].index];
    if (!slice) return;
    const params = slice.categories.map((c) => `category=${encodeURIComponent(c)}`).join('&');
    htmx.ajax('GET', `${base}&${params}`, { target: '#dialog-body', swap: 'innerHTML' });
  };
}

/* A moment worth marking, drawn on the line itself.

   Chart.js has an annotation plugin and this does not use it: a plugin
   is a second CDN fetch and a version to keep in step, and a mark is a
   dot and a word. What it is NOT is a rule per data point - a history
   with a few hundred monthly estimates in it becomes an unreadable
   picket fence, which is what the first version of this did.

   ``data.events`` is ``[{at, label}]`` where ``at`` indexes the labels.
   Few, or none. */
const eventMarks = {
  id: 'eventMarks',
  afterDatasetsDraw(chart) {
    const events = chart.config._config.data.events || [];
    if (!events.length) return;
    const meta = chart.getDatasetMeta(0);
    const { ctx } = chart;
    const accent = token('--p');
    ctx.save();
    ctx.font = '600 11px system-ui, sans-serif';
    for (const event of events) {
      const point = meta.data[event.at];
      if (!point) continue;
      ctx.beginPath();
      ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = accent;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = token('--b1');
      ctx.stroke();
      // Above the dot, and flipped to the left near the right edge
      // so the label never runs off the canvas.
      const right = point.x > chart.chartArea.right - 120;
      ctx.textAlign = right ? 'right' : 'left';
      ctx.fillStyle = accent;
      ctx.fillText(event.label, point.x + (right ? -9 : 9), point.y - 9);
    }
    ctx.restore();
  },
};

function build(Chart, canvas) {
  const data = JSON.parse(document.getElementById(canvas.dataset.chartData).textContent);
  const kind = canvas.dataset.chart;
  const format = formatOf(data);
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  showEmpty(canvas, data);
  const muted = token('--n');
  const grid = token('--b3');
  const axes = {
    // Times on a real time axis, read flat and spaced out however many
    // points there are; anything else by its labels.
    x: timed(data)
      ? {
        type: 'linear',
        min: span(data)[0],
        max: span(data)[1],
        ticks: { color: muted, maxRotation: 0, callback: (ms) => clock(ms, false) },
        afterBuildTicks: (axis) => { axis.ticks = timeTicks(axis.min, axis.max).map((value) => ({ value })); },
        grid: { color: grid },
      }
      : { ticks: { color: muted }, grid: { color: grid } },
    // A time chart measures from zero, so a small change reads as small.
    y: {
      stacked: data.style === 'stacked',
      beginAtZero: timed(data),
      ticks: {
        color: muted,
        callback: format === 'money' ? compactMoney.format : (v) => formatValue(v, format),
        stepSize: BYTE_FORMATS.has(format) ? byteStep(highest(data)) : undefined,
      },
      grid: { color: grid },
    },
  };
  new Chart(canvas, {
    type: kind,
    plugins: data.events ? [eventMarks] : [],
    data: {
      labels: timed(data) ? undefined : data.labels,
      datasets: datasets(kind, data),
      events: data.events,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      // A stack's bands redraw whole on every frame: hovering animates none.
      animation: data.style === 'stacked' ? false : undefined,
      // Hovering anywhere over the line answers, rather than only
      // a direct hit on a point - a line of a few hundred points
      // has none big enough to aim at.
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          // A lone line's card title already names it; two or more
          // (or one read against another) need a legend.
          display:
            (kind !== 'line' || plainLines(data).length > 1 || data.series.some((s) => s.compare)) &&
            !data.series[0]?.tones,
          position: kind === 'doughnut' ? 'right' : 'top',
          labels: { color: muted, boxWidth: 10, filter: (item, chart) => !chart.datasets[item.datasetIndex].guide },
        },
        tooltip: {
          // A time chart's tooltip is drawn in the theme, its title the time.
          ...(timed(data) ? {
            backgroundColor: token('--b2'),
            borderColor: token('--b3'),
            borderWidth: 1,
            titleColor: muted,
            bodyColor: token('--bc'),
            bodyFont: { weight: '600' },
            padding: 10,
            boxPadding: 4,
          } : {}),
          filter: (item) => !item.dataset.guide,
          callbacks: {
            ...(timed(data) ? { title: (items) => (items.length ? clock(items[0].parsed.x) : '') } : {}),
            label: (ctx) => {
              const value = kind === 'doughnut' ? ctx.parsed : ctx.parsed.y;
              return `${ctx.dataset.label ? `${ctx.dataset.label}: ` : ''}${formatValue(value, format)}`;
            },
          },
        },
      },
      scales: kind === 'doughnut' ? {} : axes,
      onClick: drilldownHandler(canvas, data),
    },
  });
}

// A time series moving on: the points older than the new window leave from
// the front and the new ones join at the back, on the same array, so every
// other point (and a hovered tooltip) stays where it was.
function slide(dataset, points, from) {
  const live = dataset.data;
  while (live.length && live[0].x < from) live.shift();
  const last = live[live.length - 1];
  // A long window's newest bucket fills as the tick goes on: it moves.
  const same = last && points.find((point) => point.x === last.x);
  if (same) last.y = same.y;
  const after = last ? last.x : -Infinity;
  points.filter((point) => point.x > after).forEach((point) => {
    live.push(point);
  });
}

// A live chart's data script swapped in again (``chart_panel(live=...)``):
// the drawn chart takes the new data in place, without animation: a tick
// moves a time chart's line under a pixel, and an animated new point swoops
// in from the axis so the line's end redraws itself every tick. A change in
// how many series there are rebuilds them.
function refresh(Chart, script) {
  const canvas = document.querySelector(`canvas[data-chart-data="${script.id}"]`);
  const chart = canvas && Chart.getChart(canvas);
  if (!chart) return false;
  let data = JSON.parse(script.textContent);
  showEmpty(canvas, data);
  const lines = chart.data.datasets.filter((dataset) => !dataset.guide);
  // Its lines renamed or added: the server sent it whole (its own rule in
  // overseer_container.events), so draw it again.
  const names = (list) => list.map((line) => line.label).join('\n');
  if (names(lines) !== names(data.series)) {
    chart.data.datasets = datasets(canvas.dataset.chart, data);
  } else if (timed(data)) {
    // A tick sends only the newest points (series.since): each line keeps
    // the rest back to the window's start, and a byte axis steps by
    // everything the chart now shows.
    data.series.forEach((series, i) => {
      slide(lines[i], values(data, series), span(data)[0]);
    });
    if (BYTE_FORMATS.has(data.format)) {
      data = { ...data, series: lines.map((line) => ({ values: line.data.map((point) => point.y) })) };
    }
    const shown = shownThresholds(data);
    if (chart.data.datasets.length !== lines.length + shown.length) {
      chart.data.datasets = lines.concat(guides(data));
    } else {
      shown.forEach((threshold, j) => {
        chart.data.datasets[lines.length + j].data = guideLine(data, threshold);
      });
    }
  } else {
    data.series.forEach((series, i) => {
      Object.assign(chart.data.datasets[i], { label: series.label, data: series.values });
    });
  }
  if (timed(data)) {
    const [min, max] = span(data);
    Object.assign(chart.options.scales.x, { min, max });
  } else {
    chart.data.labels = data.labels;
  }
  if (BYTE_FORMATS.has(data.format)) chart.options.scales.y.ticks.stepSize = byteStep(highest(data));
  chart.update('none');
  return true;
}

function mount(root) {
  if (!root.querySelectorAll) return;
  const canvases = [...root.querySelectorAll('canvas[data-chart]')];
  const drawn = new Set(canvases.map((canvas) => canvas.dataset.chartData));
  const fresh = [...root.querySelectorAll('script[type="application/json"][id^="chart-"]')]
    .filter((script) => !drawn.has(script.id));
  if (!canvases.length && !fresh.length) return;
  ensureChartJs().then((Chart) => {
    canvases.forEach((canvas) => {
      build(Chart, canvas);
    });
    fresh.forEach((script) => {
      refresh(Chart, script);
    });
  });
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => mount(document));
  document.body.addEventListener('htmx:afterSettle', (event) => mount(event.detail.elt));
  document.addEventListener('theme-changed', () => mount(document));
}

if (typeof module !== 'undefined') module.exports = { datasets, formatValue, refresh, timeTicks, byteStep };
