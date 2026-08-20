"""자기완결 HTML 렌더러 — 브라우저로 보는 워치리스트.

## 왜 파일 하나인가

로컬 파일(`file://`)에서 fetch는 막혀 있다. 그래서 데이터를 페이지 안에 **박아 넣고**
서버 없이 더블클릭으로 열리게 만든다. `python main.py --scan us_large --html out.html`
한 줄이면 끝이고, 인터넷도 서버도 필요 없다.

## 이 모듈도 판정하지 않는다

`render/cli.py`와 같은 규칙이다. 여기서 하는 일은 두 가지뿐:
  1. 이미 내려진 판정(enum)을 색과 모양으로 옮긴다
  2. 이미 계산된 숫자를 문구로 조립한다

임계값 비교가 등장하면 전략으로 되돌린다. JS도 마찬가지다 — 계약이 실어 보낸
`verdict` / `status` / `progress_ratio`를 그리기만 한다.

## 3상태가 화면에서 살아 있어야 한다

PASS / FAIL / **UNAVAILABLE**은 서로 다른 색이다. 확인 못 한 조건을 미달과 같은
색으로 그리면 신규 상장주가 왜 탈락했는지 사용자가 영원히 알 수 없다. 게이트는
칸(segment)으로 그려서 '8개 중 7개 통과, 1개는 데이터 없음'이 한눈에 보이게 한다.
"""

from __future__ import annotations

import json
from typing import Any

from core.types import DiagnosisReport, WatchlistReport
from render.json_out import to_payload, watchlist_to_payload

_TEMPLATE = """<!doctype html>
<html lang="ko" data-theme-preference="system">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet"
  href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap">
<style>__STYLE__</style>
</head>
<body>
<div id="app"></div>
<script type="application/json" id="payload">__DATA__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""

_STYLE = """
*, *::before, *::after { box-sizing: border-box; }

:root {
  --ground: #F5F7F6;
  --surface: #FFFFFF;
  --surface-sunk: #EDF1EF;
  --line: #D7DFDB;
  --line-strong: #B9C5C0;
  --ink: #14201C;
  --ink-soft: #4A5B55;
  --ink-faint: #7C8C86;
  --accent: #1F6F5C;
  --accent-soft: #E2EFEA;
  --pass: #2E7D46;
  --pass-soft: #DCEFE1;
  --fail: #B3402E;
  --fail-soft: #F6E0DB;
  --unknown: #A9761B;
  --unknown-soft: #F7EBD4;
  --shadow: 0 1px 2px rgba(20, 32, 28, .06), 0 8px 24px -16px rgba(20, 32, 28, .3);
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground: #0D1211;
    --surface: #151D1B;
    --surface-sunk: #101715;
    --line: #263230;
    --line-strong: #374643;
    --ink: #E3EAE7;
    --ink-soft: #A2B0AB;
    --ink-faint: #74827D;
    --accent: #57BFA2;
    --accent-soft: #16302A;
    --pass: #5FC77E;
    --pass-soft: #14301E;
    --fail: #E4735C;
    --fail-soft: #331813;
    --unknown: #D9A441;
    --unknown-soft: #302512;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .8);
  }
}

:root[data-theme="dark"] {
  --ground: #0D1211;
  --surface: #151D1B;
  --surface-sunk: #101715;
  --line: #263230;
  --line-strong: #374643;
  --ink: #E3EAE7;
  --ink-soft: #A2B0AB;
  --ink-faint: #74827D;
  --accent: #57BFA2;
  --accent-soft: #16302A;
  --pass: #5FC77E;
  --pass-soft: #14301E;
  --fail: #E4735C;
  --fail-soft: #331813;
  --unknown: #D9A441;
  --unknown-soft: #302512;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -16px rgba(0, 0, 0, .8);
}

body {
  margin: 0;
  background: var(--ground);
  color: var(--ink);
  font-family: "IBM Plex Sans KR", -apple-system, "Malgun Gothic", sans-serif;
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}

.mono, td.num, .chip, .gate-count, .metric-value {
  font-family: "IBM Plex Mono", ui-monospace, "Consolas", monospace;
  font-variant-numeric: tabular-nums;
}

#app { max-width: 1220px; margin: 0 auto; padding: 28px 20px 72px; }

/* ---------- 상태바 ---------- */
.statusbar {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px 26px;
  padding: 18px 22px; background: var(--surface);
  border: 1px solid var(--line); border-radius: 3px; box-shadow: var(--shadow);
}
.statusbar h1 {
  margin: 0; font-size: 19px; font-weight: 600; letter-spacing: -.01em;
  display: flex; align-items: baseline; gap: 10px;
}
.statusbar h1 .universe {
  font-family: "IBM Plex Mono", monospace; color: var(--accent); font-weight: 600;
}
.stat { display: flex; flex-direction: column; gap: 1px; }
.stat dt {
  font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--ink-faint); font-weight: 500;
}
.stat dd {
  margin: 0; font-family: "IBM Plex Mono", monospace; font-size: 13.5px;
  font-variant-numeric: tabular-nums; color: var(--ink);
}
.spacer { flex: 1 1 auto; }

.regime {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 9px; border-radius: 2px; font-size: 12px; font-weight: 600;
  font-family: "IBM Plex Mono", monospace;
}
.regime[data-v="RISK_ON"]  { background: var(--pass-soft); color: var(--pass); }
.regime[data-v="CAUTION"]  { background: var(--unknown-soft); color: var(--unknown); }
.regime[data-v="RISK_OFF"] { background: var(--fail-soft); color: var(--fail); }

/* ---------- 경고 ---------- */
.warnings { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.warning {
  display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: start;
  padding: 11px 16px 11px 13px; background: var(--surface);
  border: 1px solid var(--line); border-left: 3px solid var(--ink-faint);
  border-radius: 2px; font-size: 13px; color: var(--ink-soft);
}
.warning[data-sev="WARN"]     { border-left-color: var(--unknown); }
.warning[data-sev="CRITICAL"] { border-left-color: var(--fail); }
.warning code {
  font-family: "IBM Plex Mono", monospace; font-size: 11px; letter-spacing: .02em;
  color: var(--ink-faint); white-space: nowrap;
}

/* ---------- 필터 ---------- */
.controls {
  margin: 22px 0 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
}
.filter {
  font: inherit; font-size: 12.5px; padding: 5px 12px; cursor: pointer;
  background: var(--surface); color: var(--ink-soft);
  border: 1px solid var(--line); border-radius: 2px;
  transition: background .12s, color .12s, border-color .12s;
}
.filter:hover { border-color: var(--line-strong); color: var(--ink); }
.filter[aria-pressed="true"] {
  background: var(--accent); border-color: var(--accent); color: var(--surface); font-weight: 600;
}
:root[data-theme="dark"] .filter[aria-pressed="true"],
:root:not([data-theme="light"]) .filter[aria-pressed="true"] { color: #0D1211; }
.search {
  font: inherit; font-size: 13px; padding: 5px 10px; width: 150px;
  font-family: "IBM Plex Mono", monospace;
  background: var(--surface); color: var(--ink);
  border: 1px solid var(--line); border-radius: 2px;
}
.search::placeholder { color: var(--ink-faint); }
.filter:focus-visible, .search:focus-visible, .row:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 1px;
}
.count { font-size: 12.5px; color: var(--ink-faint); font-family: "IBM Plex Mono", monospace; }

/* ---------- 표 ---------- */
.tablewrap {
  overflow-x: auto; background: var(--surface);
  border: 1px solid var(--line); border-radius: 3px; box-shadow: var(--shadow);
}
table { border-collapse: collapse; width: 100%; min-width: 880px; }
thead th {
  position: sticky; top: 0; z-index: 2;
  background: var(--surface-sunk); color: var(--ink-faint);
  font-size: 10.5px; font-weight: 600; letter-spacing: .09em; text-transform: uppercase;
  text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
thead th.num { text-align: right; }
tbody td { padding: 9px 12px; border-bottom: 1px solid var(--line); vertical-align: middle; }
td.num { text-align: right; font-size: 13px; }
tbody tr.row { cursor: pointer; }
tbody tr.row:hover td { background: var(--surface-sunk); }
tbody tr.row[aria-expanded="true"] td { background: var(--accent-soft); }
.ticker { font-family: "IBM Plex Mono", monospace; font-weight: 600; font-size: 14px; }
.ticker .caret { color: var(--ink-faint); font-size: 10px; margin-right: 6px; }
tr.row[aria-expanded="true"] .caret { color: var(--accent); }
.has-buy .ticker { color: var(--pass); }
.stage { font-size: 11.5px; color: var(--ink-soft); font-family: "IBM Plex Mono", monospace; }

/* 판정 칩 */
.chip {
  display: inline-block; min-width: 52px; text-align: center;
  padding: 1px 7px; border-radius: 2px; font-size: 11px; font-weight: 600;
  letter-spacing: .02em; border: 1px solid transparent;
}
.chip[data-v="BUY"]   { background: var(--pass); color: var(--surface); }
:root[data-theme="dark"] .chip[data-v="BUY"],
:root:not([data-theme="light"]) .chip[data-v="BUY"] { color: #0D1211; }
.chip[data-v="WATCH"] { border-color: var(--unknown); color: var(--unknown); }
.chip[data-v="HOLD"]  { border-color: var(--line-strong); color: var(--ink-soft); }
.chip[data-v="AVOID"] { border-color: var(--fail); color: var(--fail); }
.chip[data-v="REJECTED_BY_GATE"] { border-color: var(--line); color: var(--ink-faint); }

/* 게이트를 칸으로 — '8개 중 7개'가 눈에 들어오게 */
.gate { display: flex; align-items: center; gap: 7px; margin-top: 5px; }
.segments { display: flex; gap: 2px; }
.seg { width: 9px; height: 5px; border-radius: 1px; background: var(--line); }
.seg[data-s="pass"] { background: var(--pass); }
.seg[data-s="unknown"] { background: var(--unknown); }
.gate-count { font-size: 11px; color: var(--ink-faint); }
.score { font-size: 11.5px; color: var(--ink-soft); font-family: "IBM Plex Mono", monospace; }
.score.none { color: var(--ink-faint); }

/* ---------- 상세 ---------- */
tr.detail > td { padding: 0; background: var(--surface-sunk); }
.detail-body {
  padding: 20px 22px 24px; border-bottom: 1px solid var(--line);
  display: grid; gap: 20px;
}
.detail-head { display: flex; flex-wrap: wrap; gap: 8px 22px; align-items: baseline; }
.detail-head h2 { margin: 0; font-size: 16px; font-weight: 600; }
.metrics { display: flex; flex-wrap: wrap; gap: 4px 22px; }
.metric { display: flex; align-items: baseline; gap: 7px; font-size: 12.5px; }
.metric-label { color: var(--ink-faint); }
.metric-value { color: var(--ink); }

.strategy {
  background: var(--surface); border: 1px solid var(--line); border-radius: 2px;
  padding: 14px 16px;
}
.strategy + .strategy { margin-top: 10px; }
.strategy-head {
  display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
  padding-bottom: 10px; border-bottom: 1px solid var(--line);
}
.strategy-name { font-weight: 600; font-size: 14px; }
.checks { width: 100%; margin-top: 10px; }
.checks td { padding: 4px 8px 4px 0; border: 0; font-size: 12.5px; vertical-align: baseline; }
.checks td:first-child { width: 46px; }
.checks td.num { color: var(--ink-soft); white-space: nowrap; }
.status {
  display: inline-block; min-width: 38px; text-align: center; padding: 0 5px;
  font-family: "IBM Plex Mono", monospace; font-size: 10.5px; font-weight: 600;
  border-radius: 2px; letter-spacing: .03em;
}
.status[data-s="PASS"] { background: var(--pass-soft); color: var(--pass); }
.status[data-s="FAIL"] { background: var(--fail-soft); color: var(--fail); }
.status[data-s="UNAVAILABLE"] { background: var(--unknown-soft); color: var(--unknown); }
.reason { color: var(--ink-soft); }

.components { margin-top: 12px; display: grid; gap: 6px; }
.component { display: grid; grid-template-columns: 130px 90px 1fr; gap: 10px; align-items: center;
  font-size: 12.5px; }
.bar { height: 6px; background: var(--line); border-radius: 1px; overflow: hidden; }
.bar > i { display: block; height: 100%; background: var(--accent); }
.component .detailtext { color: var(--ink-faint); font-size: 11.5px; }

.notes { margin: 10px 0 0; padding-left: 16px; display: grid; gap: 3px; }
.notes li { font-size: 12.5px; color: var(--ink-soft); }

.plan {
  margin-top: 12px; padding: 12px 14px; background: var(--surface-sunk);
  border: 1px solid var(--line); border-radius: 2px;
}
.plan-title {
  font-size: 10.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--ink-faint); font-weight: 600; margin-bottom: 8px;
}
.plan-grid { display: flex; flex-wrap: wrap; gap: 6px 22px; }
.no-detail { font-size: 12.5px; color: var(--ink-soft); }
.no-detail code {
  font-family: "IBM Plex Mono", monospace; font-size: 12px;
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 2px; padding: 1px 6px;
}

/* ---------- 꼬리말 ---------- */
footer {
  margin-top: 26px; padding-top: 16px; border-top: 1px solid var(--line);
  font-size: 12.5px; color: var(--ink-faint); display: grid; gap: 6px;
}
footer strong { color: var(--ink-soft); font-weight: 600; }
.failed { color: var(--unknown); }

@media (max-width: 620px) {
  #app { padding: 16px 12px 48px; }
  .component { grid-template-columns: 100px 60px 1fr; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

_SCRIPT = r"""
(function () {
  const DATA = JSON.parse(document.getElementById("payload").textContent);
  const wl = DATA.watchlist;
  const details = DATA.details || {};
  const app = document.getElementById("app");

  const VERDICTS = ["BUY", "WATCH", "AVOID", "REJECTED_BY_GATE"];
  const SHORT = { BUY: "BUY", WATCH: "WATCH", HOLD: "HOLD", AVOID: "AVOID",
                  REJECTED_BY_GATE: "GATE" };
  const state = { verdicts: new Set(), query: "" };

  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (v, d = 2) => (v === null || v === undefined ? "n/a" : v.toFixed(d));
  const money = (v) => (v === null || v === undefined ? "n/a"
    : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

  // 전략 이름 목록은 데이터에서 뽑는다 — 전략이 늘어도 화면 코드를 고치지 않는다.
  const names = [];
  wl.entries.forEach((e) => e.strategies.forEach((s) => {
    if (!names.includes(s.strategy_name)) names.push(s.strategy_name);
  }));

  function segments(s) {
    const empty = s.gate_total - s.gate_pass_count - s.gate_unavailable_count;
    let out = "";
    for (let i = 0; i < s.gate_pass_count; i++) out += '<i class="seg" data-s="pass"></i>';
    for (let i = 0; i < s.gate_unavailable_count; i++)
      out += '<i class="seg" data-s="unknown"></i>';
    for (let i = 0; i < Math.max(0, empty); i++) out += '<i class="seg"></i>';
    return out;
  }

  function cell(s) {
    if (!s) return '<td><span class="score none">—</span></td>';
    const score = s.score_pct === null || s.score_pct === undefined
      ? '<span class="score none">채점 안 함</span>'
      : '<span class="score">' + s.score_pct.toFixed(0) + "%</span>";
    return '<td><span class="chip" data-v="' + s.verdict + '">' + SHORT[s.verdict] +
      '</span><div class="gate"><span class="segments">' + segments(s) +
      '</span><span class="gate-count">' + s.gate_pass_count + "/" + s.gate_total +
      "</span>" + score + "</div></td>";
  }

  function nearestPivot(entry) {
    const ds = entry.strategies.map((s) => s.to_pivot_pct)
      .filter((v) => v !== null && v !== undefined);
    if (!ds.length) return "n/a";
    const near = ds.reduce((a, b) => (Math.abs(a) <= Math.abs(b) ? a : b));
    return (near >= 0 ? "+" : "") + near.toFixed(1) + "%";
  }

  function checksTable(verdict) {
    const rows = verdict.gate.checks.map((c) => {
      const measured = c.actual === null || c.actual === undefined ? "n/a" : num(c.actual);
      const criterion = c.comparator === "BOOL" || c.threshold === null ||
        c.threshold === undefined ? "—" : ({ GTE: "≥", GT: ">", LTE: "≤", LT: "<", EQ: "=",
          BETWEEN: "~", BOOL: "" }[c.comparator] + " " + num(c.threshold));
      const short = c.shortfall_pct === null || c.shortfall_pct === undefined
        ? "" : ' <span class="reason">(' + c.shortfall_pct.toFixed(1) + "% 미달)</span>";
      return '<tr><td><span class="status" data-s="' + c.status + '">' + c.status.slice(0, 4) +
        '</span></td><td>' + esc(c.label) + '</td><td class="num">' + measured +
        '</td><td class="num">' + criterion + '</td><td class="reason">' + esc(c.reason) +
        short + "</td></tr>";
    }).join("");
    return '<table class="checks"><tbody>' + rows + "</tbody></table>";
  }

  function componentBars(verdict) {
    if (verdict.score === null || verdict.score === undefined) {
      return '<p class="no-detail">게이트 탈락 — 채점하지 않았다 (0점이 아니다).</p>';
    }
    const bars = verdict.components.map((c) => {
      const pct = c.max > 0 ? Math.max(0, Math.min(100, (c.earned / c.max) * 100)) : 0;
      return '<div class="component"><span>' + esc(c.label) +
        '</span><span class="bar"><i style="width:' + pct.toFixed(1) + '%"></i></span>' +
        '<span class="detailtext">' + c.earned.toFixed(1) + "/" + c.max.toFixed(0) + " · " +
        esc(c.detail) + "</span></div>";
    }).join("");
    return '<div class="components">' + bars + "</div>";
  }

  function planCard(name, plan) {
    if (!plan) return "";
    const sizing = plan.shares === null || plan.shares === undefined
      ? "주수 n/a — 계좌 평가금액이 없으면 사이징하지 않는다"
      : plan.shares.toLocaleString() + "주 · 포지션 " + money(plan.position_value) +
        " · 리스크 " + money(plan.risk_amount);
    const targets = plan.r_levels.map((l) => l.multiple + "R " + money(l.price)).join("  ");
    return '<div class="plan"><div class="plan-title">' + esc(name) + ' 리스크 플랜</div>' +
      '<div class="plan-grid">' +
      '<span class="metric"><span class="metric-label">진입</span><span class="metric-value">' +
      money(plan.entry) + '</span></span>' +
      '<span class="metric"><span class="metric-label">손절</span><span class="metric-value">' +
      money(plan.stop) + " (-" + plan.stop_pct.toFixed(2) + '%)</span></span>' +
      '<span class="metric"><span class="metric-label">1R</span><span class="metric-value">' +
      money(plan.r_per_share) + '</span></span>' +
      '<span class="metric"><span class="metric-label">목표</span><span class="metric-value">' +
      esc(targets) + '</span></span></div>' +
      '<div class="metric" style="margin-top:6px"><span class="metric-label">사이징</span>' +
      '<span class="metric-value">' + esc(sizing) + "</span></div></div>";
  }

  function detailPanel(entry) {
    const full = details[entry.ticker];
    const head = '<div class="detail-head"><h2 class="mono">' + esc(entry.ticker) +
      '</h2><div class="metrics">' +
      '<span class="metric"><span class="metric-label">기준일</span>' +
      '<span class="metric-value">' + esc(entry.as_of) + '</span></span>' +
      '<span class="metric"><span class="metric-label">RS</span><span class="metric-value">' +
      (entry.rs_percentile === null ? "n/a" : entry.rs_percentile.toFixed(0)) + '</span></span>' +
      '<span class="metric"><span class="metric-label">Stage</span>' +
      '<span class="metric-value">' + esc(entry.stage) + '</span></span>' +
      '<span class="metric"><span class="metric-label">일치도</span>' +
      '<span class="metric-value">' + esc(entry.agreement) + "</span></span></div></div>";

    if (!full) {
      const rows = entry.strategies.map((s) =>
        '<div class="strategy"><div class="strategy-head">' +
        '<span class="strategy-name">' + esc(s.strategy_name) + '</span>' +
        '<span class="chip" data-v="' + s.verdict + '">' + SHORT[s.verdict] + '</span>' +
        '<span class="gate-count">게이트 ' + s.gate_pass_count + "/" + s.gate_total +
        (s.gate_unavailable_count ? " · 데이터 없음 " + s.gate_unavailable_count : "") +
        '</span><span class="gate-count">' + esc(s.setup_state) + "</span></div></div>"
      ).join("");
      return '<div class="detail-body">' + head + "<div>" + rows + "</div>" +
        '<p class="no-detail">이 종목의 전체 근거는 <code>python main.py ' +
        esc(entry.ticker) + "</code> 로 볼 수 있다. 같은 판정이 나온다.</p></div>";
    }

    const plans = full.risk_plans || {};
    const strategies = full.strategy_verdicts.map((v) =>
      '<div class="strategy"><div class="strategy-head">' +
      '<span class="strategy-name">' + esc(v.strategy_name) + '</span>' +
      '<span class="chip" data-v="' + v.verdict + '">' + SHORT[v.verdict] + '</span>' +
      '<span class="gate-count">게이트 ' + v.gate.pass_count + "/" + v.gate.total +
      (v.gate.unavailable_count ? " · 데이터 없음 " + v.gate.unavailable_count : "") +
      '</span><span class="gate-count">' + esc(v.setup_state) + "</span></div>" +
      checksTable(v) + componentBars(v) +
      '<ul class="notes">' + v.notes.map((n) => "<li>" + esc(n) + "</li>").join("") + "</ul>" +
      planCard(v.strategy_name, plans[v.strategy_name]) + "</div>"
    ).join("");

    return '<div class="detail-body">' + head + "<div>" + strategies + "</div></div>";
  }

  function visible() {
    const q = state.query.trim().toUpperCase();
    return wl.entries.filter((e) => {
      if (q && !e.ticker.toUpperCase().includes(q)) return false;
      if (!state.verdicts.size) return true;
      return e.strategies.some((s) => state.verdicts.has(s.verdict));
    });
  }

  function render() {
    const rows = visible();
    const body = rows.map((e, i) =>
      '<tr class="row' + (e.buy_strategies.length ? " has-buy" : "") +
      '" tabindex="0" role="button" aria-expanded="false" data-i="' + i + '">' +
      '<td class="ticker"><span class="caret">▸</span>' + esc(e.ticker) + "</td>" +
      '<td class="num">' + money(e.price) + "</td>" +
      '<td class="num">' +
        (e.rs_percentile === null ? "n/a" : e.rs_percentile.toFixed(0)) + "</td>" +
      '<td><span class="stage">' + esc(e.stage.replace("STAGE_", "S")) + "</span></td>" +
      names.map((n) => cell(e.strategies.find((s) => s.strategy_name === n))).join("") +
      '<td class="num">' + nearestPivot(e) + "</td></tr>" +
      '<tr class="detail" hidden><td colspan="' + (5 + names.length) + '"></td></tr>'
    ).join("");

    document.getElementById("tbody").innerHTML = body;
    document.getElementById("count").textContent =
      rows.length + "종목 표시 / 스캔 " + wl.requested;

    document.querySelectorAll("tr.row").forEach((tr) => {
      const toggle = () => {
        const open = tr.getAttribute("aria-expanded") === "true";
        const panel = tr.nextElementSibling;
        tr.setAttribute("aria-expanded", String(!open));
        tr.querySelector(".caret").textContent = open ? "▸" : "▾";
        panel.hidden = open;
        if (!open) panel.firstElementChild.innerHTML = detailPanel(rows[Number(tr.dataset.i)]);
      };
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggle(); }
      });
    });
  }

  const warnings = wl.warnings.map((w) =>
    '<div class="warning" data-sev="' + w.severity + '"><code>' + esc(w.code) +
    "</code><span>" + esc(w.message) + "</span></div>").join("");

  const filters = VERDICTS.map((v) =>
    '<button class="filter" type="button" aria-pressed="false" data-v="' + v + '">' +
    SHORT[v] + "</button>").join("");

  app.innerHTML =
    '<header class="statusbar">' +
      '<h1>워치리스트 <span class="universe">' + esc(wl.universe) + "</span></h1>" +
      '<span class="regime" data-v="' + wl.regime + '">시장 ' + esc(wl.regime) + "</span>" +
      '<span class="spacer"></span>' +
      '<div class="stat"><dt>스캔</dt><dd>' + wl.requested + "종목</dd></div>" +
      '<div class="stat"><dt>BUY 종목</dt><dd>' +
        wl.entries.filter((e) => e.buy_strategies.length).length + "</dd></div>" +
      '<div class="stat"><dt>진단 실패</dt><dd>' + wl.failed.length + "</dd></div>" +
      '<div class="stat"><dt>생성</dt><dd>' + esc(wl.generated_at.slice(0, 16).replace("T", " ")) +
      "</dd></div>" +
    "</header>" +
    '<div class="warnings">' + warnings + "</div>" +
    '<div class="controls">' + filters +
      '<input class="search" id="search" type="search" placeholder="티커 검색" ' +
      'aria-label="티커 검색">' +
      '<span class="spacer"></span><span class="count" id="count"></span>' +
    "</div>" +
    '<div class="tablewrap"><table><thead><tr>' +
      '<th>티커</th><th class="num">가격</th><th class="num">RS</th><th>Stage</th>' +
      names.map((n) => "<th>" + esc(n) + "</th>").join("") +
      '<th class="num">피벗까지</th>' +
    '</tr></thead><tbody id="tbody"></tbody></table></div>' +
    "<footer>" +
      "<div><strong>정렬</strong> BUY 전략 수 → 게이트 진행률 내림차순. 게이트에 근접한 종목이 " +
      "위에 오는 이유는 내일 조건을 채울 후보이기 때문이다. 정렬은 계약이 정하며 화면이 다시 " +
      "정렬하지 않는다.</div>" +
      "<div><strong>읽는 법</strong> 칸은 게이트 조건 하나다. 초록=통과, 주황=데이터 없음(조건 " +
      "미달과 다르다), 빈칸=미달. 점수는 만점 대비 비율이며 <em>전략 간 비교나 평균은 " +
      "의미가 없다</em> — 척도가 서로 다르다.</div>" +
      (wl.failed.length
        ? '<div class="failed"><strong>진단 실패</strong> ' +
          wl.failed.map((f) => esc(f.ticker) + " (" + esc(f.reason) + ")").join(", ") + "</div>"
        : "") +
      "<div>이 화면은 진단과 근거이지 매매 권유가 아니다. 스키마 " + esc(wl.schema_version) +
      "</div>" +
    "</footer>";

  document.querySelectorAll(".filter").forEach((b) => {
    b.addEventListener("click", () => {
      const v = b.dataset.v;
      if (state.verdicts.has(v)) state.verdicts.delete(v);
      else state.verdicts.add(v);
      b.setAttribute("aria-pressed", String(state.verdicts.has(v)));
      render();
    });
  });
  document.getElementById("search").addEventListener("input", (ev) => {
    state.query = ev.target.value;
    render();
  });

  render();
})();
"""


def watchlist_html(
    report: WatchlistReport,
    details: dict[str, DiagnosisReport] | None = None,
    *,
    title: str | None = None,
) -> str:
    """워치리스트를 자기완결 HTML 한 장으로.

    details에 진단 리포트를 넣으면 그 티커의 행을 펼쳤을 때 게이트 조건·점수 항목·
    리스크 플랜까지 보인다. 없는 티커는 요약만 보여주고 "개별 진단하면 같은 판정이
    나온다"고 안내한다 — 없는 것을 있는 척하지 않는다.
    """
    payload: dict[str, Any] = {
        "watchlist": watchlist_to_payload(report),
        "details": {
            ticker: to_payload(detail) for ticker, detail in (details or {}).items()
        },
    }
    return (
        _TEMPLATE.replace("__TITLE__", title or f"{report.universe} 워치리스트")
        .replace("__STYLE__", _STYLE)
        .replace("__SCRIPT__", _SCRIPT)
        # </script>가 데이터 안에 있으면 태그가 조기 종료된다. JSON 문자열 안에서만
        # 일어날 수 있는 일이라 이스케이프 한 번으로 충분하다.
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    )


def diagnosis_html(report: DiagnosisReport, *, title: str | None = None) -> str:
    """단일 진단을 같은 화면으로 본다 — 종목 하나짜리 워치리스트다.

    별도 템플릿을 만들지 않는 이유: 워치리스트의 한 줄을 펼친 것이 곧 진단 상세이고,
    두 화면이 갈라지면 같은 판정이 두 가지 모습으로 보이게 된다.
    """
    from core.watchlist import build_watchlist

    watchlist = build_watchlist(
        report.ticker, [report], regime=report.regime, warnings=tuple(report.warnings)
    )
    return watchlist_html(
        watchlist, {report.ticker: report}, title=title or f"{report.ticker} 진단"
    )
