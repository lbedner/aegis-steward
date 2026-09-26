/* Chart island (the first of two JS islands; the other is chat).

   The server renders a <canvas data-chart="line|bar|doughnut"> next to a
   JSON block (see the chart_panel macro); this file mounts Chart.js over
   every such canvas on load and after each htmx settle. Chart.js itself
   is fetched on first use, so pages without charts pay nothing and a
   swap into a chart page still works. */

const CHART_JS =
	"https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.js";
const RAMP = 8; // --aegis-chart-1 .. -8 in tailwind.config.js; cycles past that

// Colors come from the active theme's variables, read at mount time so a
// theme switch repaints charts in the new palette. DaisyUI stores its
// colors as oklch triplets (--p, --n, ...); the chart ramp is literal.
function token(name, alpha) {
	const value = getComputedStyle(document.documentElement)
		.getPropertyValue(name)
		.trim();
	if (value.startsWith("#")) return value;
	return `oklch(${value} / ${alpha ?? 1})`;
}
function rampColor(i) {
	return token(`--aegis-chart-${(i % RAMP) + 1}`);
}

let chartJsLoading = null;
function ensureChartJs() {
	if (window.Chart) return Promise.resolve(window.Chart);
	if (!chartJsLoading) {
		chartJsLoading = new Promise((resolve, reject) => {
			const script = document.createElement("script");
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
	return `${value < 0 ? "-" : ""}$${abs}`;
}

// A bar's tone, decided by the server: the highest bright teal, the lowest
// violet, the rest a darker teal (Pulse's day-of-week chart).
function toneColor(tone) {
	const teal = token("--aegis-chart-1");
	if (tone === "high") return teal;
	if (tone === "low") return token("--aegis-chart-2");
	return `${teal}88`;
}

function datasets(kind, data) {
	const tail = token("--n"); // "Other" reads as tail, never as a category
	return data.series.map((series, i) =>
		series.points
			? markers(series)
			: series.tones
				? {
						label: series.label,
						data: series.values,
						backgroundColor: series.tones.map(toneColor),
						borderRadius: 3,
					}
				: {
					label: series.label,
					data: series.values,
					backgroundColor:
						kind === "doughnut"
							? data.labels.map((label, j) =>
									label === "Other" ? tail : rampColor(j),
								)
							: kind === "line"
								? token("--p", 0.15)
								: rampColor(i),
					borderColor: kind === "line" ? token("--p") : undefined,
					borderWidth: kind === "doughnut" ? 0 : 2,
					fill: kind === "line",
					tension: 0.3,
					pointRadius: 0,
				},
	);
}

// A marker series: dots on the days something was overdue, no line.
function markers(series) {
	return {
		label: series.label,
		data: series.values,
		showLine: false,
		pointRadius: 4,
		pointHoverRadius: 6,
		pointBackgroundColor: token("--er"),
		pointBorderColor: token("--er"),
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
		const params = slice.categories
			.map((c) => `category=${encodeURIComponent(c)}`)
			.join("&");
		htmx.ajax("GET", `${base}&${params}`, {
			target: "#dialog-body",
			swap: "innerHTML",
		});
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
	id: "eventMarks",
	afterDatasetsDraw(chart) {
		const events = chart.config._config.data.events || [];
		if (!events.length) return;
		const meta = chart.getDatasetMeta(0);
		const { ctx } = chart;
		const accent = token("--p");
		ctx.save();
		ctx.font = "600 11px system-ui, sans-serif";
		for (const event of events) {
			const point = meta.data[event.at];
			if (!point) continue;
			ctx.beginPath();
			ctx.arc(point.x, point.y, 5, 0, Math.PI * 2);
			ctx.fillStyle = accent;
			ctx.fill();
			ctx.lineWidth = 2;
			ctx.strokeStyle = token("--b1");
			ctx.stroke();
			// Above the dot, and flipped to the left near the right edge
			// so the label never runs off the canvas.
			const right = point.x > chart.chartArea.right - 120;
			ctx.textAlign = right ? "right" : "left";
			ctx.fillStyle = accent;
			ctx.fillText(event.label, point.x + (right ? -9 : 9), point.y - 9);
		}
		ctx.restore();
	},
};

function build(Chart, canvas) {
	const data = JSON.parse(
		document.getElementById(canvas.dataset.chartData).textContent,
	);
	const kind = canvas.dataset.chart;
	// Dollars unless the data says it counts something else.
	const shown =
		data.format === "count" ? (v) => Number(v).toLocaleString() : money;
	const existing = Chart.getChart(canvas);
	if (existing) existing.destroy();
	const muted = token("--n");
	const grid = token("--b3");
	const axes = {
		x: { ticks: { color: muted }, grid: { color: grid } },
		y: {
			ticks: { color: muted, callback: (v) => shown(v) },
			grid: { color: grid },
		},
	};
	new Chart(canvas, {
		type: kind,
		plugins: data.events ? [eventMarks] : [],
		data: {
			labels: data.labels,
			datasets: datasets(kind, data),
			events: data.events,
		},
		options: {
			responsive: true,
			maintainAspectRatio: false,
			// Hovering anywhere over the line answers, rather than only
			// a direct hit on a point - a line of a few hundred points
			// has none big enough to aim at.
			interaction: { mode: "index", intersect: false },
			plugins: {
				legend: {
					display: kind !== "line" && !data.series[0]?.tones,
					position: kind === "doughnut" ? "right" : "top",
					labels: { color: muted, boxWidth: 10 },
				},
				tooltip: {
					callbacks: {
						label: (ctx) => {
							const value = kind === "doughnut" ? ctx.parsed : ctx.parsed.y;
							return `${ctx.dataset.label ? `${ctx.dataset.label}: ` : ""}${shown(value)}`;
						},
					},
				},
			},
			scales: kind === "doughnut" ? {} : axes,
			onClick: drilldownHandler(canvas, data),
		},
	});
}

function mount(root) {
	const canvases = root.querySelectorAll
		? root.querySelectorAll("canvas[data-chart]")
		: [];
	if (!canvases.length) return;
	ensureChartJs().then((Chart) => {
		canvases.forEach((canvas) => {
			build(Chart, canvas);
		});
	});
}

document.addEventListener("DOMContentLoaded", () => mount(document));
document.body.addEventListener("htmx:afterSettle", (event) =>
	mount(event.detail.elt),
);
document.addEventListener("theme-changed", () => mount(document));
