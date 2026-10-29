"""Draws the result charts (SVG) from results/*.json. No libraries needed.

    python plot_results.py

Writes architecture/chart-*.svg, which the README shows.
"""

import json
from pathlib import Path

RESULTS = Path("results")
OUT = Path("architecture")

# Fixed colour per setup, the same in every chart.
COLORS = {"1 gateway": "#2a78d6", "3 gateways": "#eb6834", "3 cells": "#1baf7a"}
INK, MUTED, GRID = "#0b0b0b", "#898781", "#e1e0d9"


def load(label):
    return json.loads((RESULTS / f"{label}.json").read_text())["rows"]


def line_chart(file, title, subtitle, series, key, unit):
    """series: {name: rows}. One line per setup, users on the x axis."""
    w, h, left, right, top, bottom = 720, 400, 70, 150, 70, 50
    users = [r["users"] for r in next(iter(series.values()))]
    ymax = max(r[key] for rows in series.values() for r in rows) * 1.1
    x = lambda i: left + i * (w - left - right) / (len(users) - 1)
    y = lambda v: h - bottom - v / ymax * (h - top - bottom)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Arial, Helvetica, sans-serif" font-size="12">',
        f'<rect width="{w}" height="{h}" fill="#fcfcfb"/>',
        f'<text x="{left}" y="28" font-size="16" font-weight="bold" fill="{INK}">{title}</text>',
        f'<text x="{left}" y="48" fill="#52514e">{subtitle}</text>',
    ]
    for k in range(5):  # horizontal grid lines with values
        v = ymax * k / 4
        parts.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="{GRID}"/>')
        parts.append(f'<text x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="{MUTED}">{v:,.0f}</text>')
    for i, u in enumerate(users):
        parts.append(f'<text x="{x(i):.1f}" y="{h - bottom + 18}" text-anchor="middle" fill="{MUTED}">{u}</text>')
    parts.append(f'<text x="{(left + w - right) / 2}" y="{h - 12}" text-anchor="middle" fill="{MUTED}">users sending requests at the same time</text>')
    parts.append(f'<text x="16" y="{top - 12}" fill="{MUTED}">{unit}</text>')

    for name, rows in series.items():
        color = COLORS[name]
        points = " ".join(f"{x(i):.1f},{y(r[key]):.1f}" for i, r in enumerate(rows))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for i, r in enumerate(rows):
            parts.append(f'<circle cx="{x(i):.1f}" cy="{y(r[key]):.1f}" r="4" fill="{color}" stroke="#fcfcfb" stroke-width="2"/>')

    # Legend on the right, top to bottom. Lines can lie on top of each other,
    # so the legend (not the line end) is what names each setup.
    for k, (name, rows) in enumerate(series.items()):
        ly = top + 10 + k * 22
        lx = w - right + 14
        parts.append(f'<line x1="{lx}" x2="{lx + 18}" y1="{ly}" y2="{ly}" stroke="{COLORS[name]}" stroke-width="3"/>')
        parts.append(f'<text x="{lx + 24}" y="{ly + 4}" fill="{INK}">{name}: {rows[-1][key]:,.0f}</text>')
    parts.append(f'<text x="{w - right + 14}" y="{top + 10 + len(series) * 22}" fill="{MUTED}">at {users[-1]} users</text>')
    parts.append("</svg>")
    (OUT / file).write_text("\n".join(parts), encoding="utf-8")


def bar_chart(file, title, subtitle, bars, unit):
    """bars: [(label, value, color)]."""
    w, h, left, top = 720, 70 + 46 * len(bars), 230, 70
    vmax = max(v for _, v, _ in bars) * 1.15
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Arial, Helvetica, sans-serif" font-size="12">',
        f'<rect width="{w}" height="{h}" fill="#fcfcfb"/>',
        f'<text x="16" y="28" font-size="16" font-weight="bold" fill="{INK}">{title}</text>',
        f'<text x="16" y="48" fill="#52514e">{subtitle}</text>',
    ]
    for i, (label, value, color) in enumerate(bars):
        yy = top + i * 46
        bw = value / vmax * (w - left - 40)
        parts.append(f'<text x="{left - 10}" y="{yy + 19}" text-anchor="end" fill="{INK}">{label}</text>')
        parts.append(f'<rect x="{left}" y="{yy}" width="{bw:.1f}" height="28" rx="4" fill="{color}"/>')
        parts.append(f'<text x="{left + bw + 8:.1f}" y="{yy + 19}" fill="{INK}">{value:,.1f} {unit}</text>')
    parts.append("</svg>")
    (OUT / file).write_text("\n".join(parts), encoding="utf-8")


def at(rows, users):
    return next(r for r in rows if r["users"] == users)


def main():
    for path, name in [("tickets", "GET /api/tickets/{id} (CPU work in the service)"),
                       ("users", "GET /api/users/{id} (database read, little CPU)")]:
        series = {
            "1 gateway": load(f"step1-{path}"),
            "3 gateways": load(f"step2a-{path}"),
            "3 cells": load(f"step2b-{path}"),
        }
        line_chart(f"chart-{path}-throughput.svg", f"Throughput: {name}",
                   "Requests finished per second. Higher is better.", series, "rps", "req/s")
        line_chart(f"chart-{path}-latency.svg", f"Latency P50: {name}",
                   "Half of the requests were faster than this. Lower is better.", series, "p50", "ms")

    users = 50
    bar_chart("chart-database.svg", f"Database steps at {users} users",
              "Requests per second. Same laptop, same session; each database container has 1 CPU.",
              [("stats read, 1 database", at(load("step1-stats"), users)["rps"], "#2a78d6"),
               ("stats read, 2 replicas", at(load("step3-stats"), users)["rps"], "#1baf7a"),
               ("stats read, 2 shards", at(load("step4-stats"), users)["rps"], "#eda100"),
               ("writes, 1 database", at(load("step1-writes"), users)["rps"], "#2a78d6"),
               ("writes, 2 shards", at(load("step4-writes"), users)["rps"], "#eda100")],
              "req/s")


if __name__ == "__main__":
    main()
