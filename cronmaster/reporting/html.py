# -*- coding: utf-8 -*-
"""تقرير HTML مكتفٍ بذاته.

لا CSS ولا JS ولا خطوط ولا صور خارجية، ولا أي طلب شبكة: كل شيء مضمّن.
الرسوم مرسومة يدوياً بـ SVG داخلي — بلا أي مكتبة رسم.
التخطيط RTL افتراضاً، والألوان تتبع ``prefers-color-scheme``.
"""

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..i18n import get_lang, t
from ..models import FailureAnalysis, Job

_CSS = """
:root {
  --bg: #f7f7f8; --panel: #ffffff; --fg: #1a1a1c; --muted: #5c5c66;
  --line: #e2e2e8; --ok: #1f8a4c; --warn: #b8860b; --err: #c0392b; --accent: #2d6cdf;
  --grid: #ececf1;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16161a; --panel: #1e1e24; --fg: #e9e9ee; --muted: #a0a0ad;
    --line: #2e2e38; --ok: #4ecb7a; --warn: #e0b341; --err: #ef6a5a; --accent: #6ea3ff;
    --grid: #2a2a33;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 1.25rem; background: var(--bg); color: var(--fg);
  font-family: system-ui, -apple-system, "Segoe UI", Tahoma, "Noto Naskh Arabic", sans-serif;
  line-height: 1.6; overflow-x: hidden;
}
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 0 0 .75rem; }
.sub { color: var(--muted); font-size: .85rem; margin: 0 0 1.5rem; }
section {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem; margin-bottom: 1.25rem;
}
.tiles { display: flex; flex-wrap: wrap; gap: .75rem; }
.tile {
  flex: 1 1 8rem; border: 1px solid var(--line); border-radius: 8px;
  padding: .6rem .8rem; background: var(--bg);
}
.tile .n { font-size: 1.6rem; font-weight: 600; display: block; }
.tile .l { color: var(--muted); font-size: .8rem; }
.scroll { overflow-x: auto; max-width: 100%; }
table { border-collapse: collapse; width: 100%; min-width: 40rem; font-size: .9rem; }
th, td { padding: .45rem .6rem; border-bottom: 1px solid var(--line); text-align: start; white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: .8rem; }
tr:last-child td { border-bottom: 0; }
.ok { color: var(--ok); } .err { color: var(--err); } .warn { color: var(--warn); }
.muted { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; }
svg { display: block; max-width: 100%; height: auto; }
footer { color: var(--muted); font-size: .8rem; text-align: center; padding: .5rem 0 1.5rem; }
"""


def _e(value: Any) -> str:
    """تهريب أي قيمة قبل إدراجها في HTML"""
    return html.escape("" if value is None else str(value), quote=True)


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _secs(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}s"


# ============================================================
# الرسوم (SVG يدوي)
# ============================================================


def _trend_svg(points: List[Dict[str, Any]]) -> str:
    """رسم خطي لنسبة النجاح عبر الأيام"""
    if len(points) < 2:
        return f'<p class="empty">{_e(t("html.no_data"))}</p>'

    width, height = 760, 200
    pad_x, pad_y = 34, 16
    inner_w = width - pad_x * 2
    inner_h = height - pad_y * 2
    step = inner_w / (len(points) - 1)

    coords = []
    for i, point in enumerate(points):
        x = pad_x + i * step
        y = pad_y + (1 - max(0.0, min(1.0, float(point["rate"])))) * inner_h
        coords.append((x, y))

    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{pad_x:.1f},{pad_y + inner_h:.1f} " + line + f" {coords[-1][0]:.1f},{pad_y + inner_h:.1f}"

    grid = []
    for fraction, label in ((0.0, "100%"), (0.5, "50%"), (1.0, "0%")):
        y = pad_y + fraction * inner_h
        grid.append(f'<line x1="{pad_x}" y1="{y:.1f}" x2="{width - pad_x}" y2="{y:.1f}" stroke="var(--grid)" />')
        grid.append(
            f'<text x="{pad_x - 6}" y="{y + 4:.1f}" font-size="10" fill="var(--muted)" text-anchor="end">{label}</text>'
        )

    first_day = _e(points[0]["day"])
    last_day = _e(points[-1]["day"])

    return (
        f'<svg viewBox="0 0 {width} {height + 18}" role="img" '
        f'aria-label="{_e(t("html.trend"))}" width="100%">'
        + "".join(grid)
        + f'<polygon points="{area}" fill="var(--accent)" opacity="0.12" />'
        + f'<polyline points="{line}" fill="none" stroke="var(--accent)" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round" />'
        + f'<text x="{pad_x}" y="{height + 12}" font-size="10" fill="var(--muted)">{first_day}</text>'
        + f'<text x="{width - pad_x}" y="{height + 12}" font-size="10" fill="var(--muted)" '
        f'text-anchor="end">{last_day}</text>'
        + "</svg>"
    )


def _bars_svg(rows: List[Dict[str, Any]]) -> str:
    """أعمدة أفقية لأكثر المهام فشلاً (قيمة التذبذب 0..1)"""
    rows = [r for r in rows if r.get("flakiness")]
    if not rows:
        return f'<p class="empty">{_e(t("html.no_data"))}</p>'

    rows = sorted(rows, key=lambda r: r["flakiness"], reverse=True)[:8]
    width = 760
    bar_h, gap = 22, 10
    label_w = 220
    height = len(rows) * (bar_h + gap) + gap
    track_w = width - label_w - 60

    parts = []
    for i, row in enumerate(rows):
        y = gap + i * (bar_h + gap)
        value = max(0.0, min(1.0, float(row["flakiness"])))
        name = str(row.get("job_name") or row.get("job_id") or "")
        if len(name) > 32:
            name = name[:31] + "…"
        parts.append(
            f'<text x="{label_w - 8}" y="{y + bar_h * 0.7:.0f}" font-size="12" '
            f'fill="var(--fg)" text-anchor="end">{_e(name)}</text>'
        )
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{track_w}" height="{bar_h}" rx="4" fill="var(--grid)" />'
        )
        parts.append(
            f'<rect x="{label_w}" y="{y}" width="{track_w * value:.1f}" height="{bar_h}" rx="4" fill="var(--err)" />'
        )
        parts.append(
            f'<text x="{label_w + track_w + 8}" y="{y + bar_h * 0.7:.0f}" font-size="11" '
            f'fill="var(--muted)">{_pct(value)}</text>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{_e(t("html.top_failing"))}" width="100%">' + "".join(parts) + "</svg>"
    )


# ============================================================
# الجداول
# ============================================================


def _status_cell(job: Job) -> str:
    if job.is_failed:
        return '<span class="err">error</span>'
    if job.last_status == "ok":
        return '<span class="ok">ok</span>'
    if not job.enabled:
        return '<span class="muted">disabled</span>'
    return f'<span class="muted">{_e(job.last_status or "—")}</span>'


def _jobs_table(jobs: List[Job], stats: List[Dict[str, Any]]) -> str:
    by_id = {s["job_id"]: s for s in stats}
    headers = [
        t("html.col_job"), t("html.col_status"), t("html.col_schedule"),
        t("html.col_success"), t("html.col_flaky"), t("html.col_runs"), t("html.col_duration"),
    ]
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)

    body = []
    for job in jobs:
        s = by_id.get(job.id, {})
        body.append(
            "<tr>"
            f"<td>{_e(job.name)}</td>"
            f"<td>{_status_cell(job)}</td>"
            f"<td><code>{_e(job.schedule)}</code></td>"
            f"<td>{_pct(s.get('success_rate'))}</td>"
            f"<td>{_pct(s.get('flakiness'))}</td>"
            f"<td>{_e(s.get('runs', 0))}</td>"
            f"<td>{_secs(s.get('avg_duration'))}</td>"
            "</tr>"
        )

    if not body:
        return f'<p class="empty">{_e(t("html.no_data"))}</p>'
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _history_table(history: Dict[str, Any]) -> str:
    rows = []
    for fix in reversed(history.get("fixes", [])[-25:]):
        details = f"{fix.get('job_id', '')} — {fix.get('details', '')}"
        rows.append((fix.get("timestamp", ""), fix.get("fix_type", ""), details))
    for key, stamp in sorted(history.get("alerts", {}).items(), key=lambda kv: kv[1], reverse=True)[:15]:
        rows.append((stamp, "alert", key))

    if not rows:
        return f'<p class="empty">{_e(t("html.no_data"))}</p>'

    rows.sort(key=lambda r: r[0], reverse=True)
    head = "".join(f"<th>{_e(h)}</th>" for h in (t("html.col_time"), t("html.col_kind"), t("html.col_details")))
    body = "".join(
        f"<tr><td>{_e(ts[:19].replace('T', ' '))}</td><td>{_e(kind)}</td><td>{_e(details)}</td></tr>"
        for ts, kind, details in rows[:40]
    )
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


# ============================================================
# الصفحة
# ============================================================


def render_html(
    jobs: List[Job],
    analyses: List[FailureAnalysis],
    summary: Dict[str, int],
    stats: List[Dict[str, Any]],
    trend: List[Dict[str, Any]],
    history: Dict[str, Any],
) -> str:
    """بناء صفحة HTML كاملة كنص واحد"""
    lang = get_lang()
    direction = "rtl" if lang == "ar" else "ltr"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tiles = "".join(
        f'<div class="tile"><span class="n {cls}">{summary.get(key, 0)}</span>'
        f'<span class="l">{_e(label)}</span></div>'
        for key, label, cls in (
            ("total", t("report.total_jobs"), ""),
            ("ok", t("report.ok_jobs"), "ok"),
            ("error", t("report.failed_jobs"), "err"),
            ("other", t("report.other_jobs"), "muted"),
        )
    )

    failures = ""
    if analyses:
        items = "".join(
            f"<tr><td>{_e(a.job.name)}</td><td>{_e(a.error_type.value)}</td>"
            f"<td>{_e(a.description)}</td>"
            f"<td>{_e(a.fix_details or a.suggested_fix)}</td></tr>"
            for a in analyses
        )
        head = "".join(
            f"<th>{_e(h)}</th>"
            for h in (t("html.col_job"), t("report.error"), t("report.analysis"), t("report.fix"))
        )
        failures = (
            f"<section><h2>{_e(t('report.failed_section'))} ({len(analyses)})</h2>"
            f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{items}</tbody></table></div>'
            "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="{lang}" dir="{direction}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(t('html.title'))}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>{_e(t('html.title'))}</h1>
  <p class="sub">{_e(t('html.generated_at', ts=now))}</p>

  <section>
    <h2>{_e(t('html.summary'))}</h2>
    <div class="tiles">{tiles}</div>
  </section>

  {failures}

  <section>
    <h2>{_e(t('html.jobs_table'))}</h2>
    {_jobs_table(jobs, stats)}
  </section>

  <section>
    <h2>{_e(t('html.trend'))}</h2>
    {_trend_svg(trend)}
  </section>

  <section>
    <h2>{_e(t('html.top_failing'))}</h2>
    {_bars_svg(stats)}
  </section>

  <section>
    <h2>{_e(t('html.history'))}</h2>
    {_history_table(history)}
  </section>

  <footer>{_e(t('report.footer'))}</footer>
</div>
</body>
</html>
"""
