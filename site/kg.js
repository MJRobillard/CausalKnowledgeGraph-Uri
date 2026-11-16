/* Knowledge-graph explorer for the walkthrough page. Data: data/kg.js (window.KG), built by scripts/build_site_data.py. */
(function () {
  'use strict';
  var KG = window.KG;
  if (!KG) return;
  var NS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; });
  }
  function fmt(n) {
    if (n == null || isNaN(n)) return '';
    var a = Math.abs(n);
    return (a >= 100 ? Math.round(n).toLocaleString('en-US') : (+n.toFixed(3)).toLocaleString('en-US'));
  }
  function wrap(text, max) {           // split a label into lines of at most `max` characters
    var words = text.split(' '), lines = [''];
    words.forEach(function (w) {
      var cur = lines[lines.length - 1];
      if (cur && (cur + ' ' + w).length > max) lines.push(w); else lines[lines.length - 1] = cur ? cur + ' ' + w : w;
    });
    return lines;
  }
  function tipper(stage, tip) {
    return {
      show: function (evt, html) {
        tip.innerHTML = html; tip.style.opacity = 1;
        var r = stage.getBoundingClientRect();
        var x = evt.clientX - r.left + stage.scrollLeft + 14, y = evt.clientY - r.top + 14;
        var maxX = stage.scrollLeft + stage.clientWidth - tip.offsetWidth - 8;
        tip.style.left = Math.max(8, Math.min(x, maxX)) + 'px';
        tip.style.top = y + 'px';
      },
      hide: function () { tip.style.opacity = 0; }
    };
  }

  var docsById = {}, seriesById = {};
  KG.evidence.docs.forEach(function (d) { docsById[d.id] = d; });
  KG.evidence.series.forEach(function (s) { seriesById[s.id] = s; });

  /* ---------------------------------------------------------------- headline counts */
  var c = KG.evidence.counts, bk = c.by_kind;
  document.getElementById('kg-stats').innerHTML =
    '<span><b>' + c.nodes.toLocaleString('en-US') + '</b> nodes</span>' +
    '<span><b>' + c.edges.toLocaleString('en-US') + '</b> edges</span>' +
    '<span><b>' + (bk.QuantClaim || 0).toLocaleString('en-US') + '</b> quantitative claims</span>' +
    '<span><b>' + (bk.Event || 0).toLocaleString('en-US') + '</b> events</span>' +
    '<span><b>' + (bk.Entity || 0).toLocaleString('en-US') + '</b> entities</span>' +
    '<span><b>' + (bk.Series || 0) + '</b> measured series</span>';

  /* ---------------------------------------------------------------- view 1: evidence tiers */
  (function evidenceView() {
    var stage = document.getElementById('ev-stage'), panel = document.getElementById('ev-panel');
    var tip = tipper(stage, document.getElementById('ev-tip'));
    var W = 1080, LX = 214, LANE_H = 74;
    var lanes = [
      { tier: '3', y: 8, name: 'Tier 3 · News', sub: 'prior trust 0.3' },
      { tier: '2', y: 90, name: 'Tier 2 · Independent analysis', sub: 'prior trust 0.7' },
      { tier: '1', y: 172, name: 'Tier 1 · First-party', sub: 'ERCOT; flagged interested party' },
      { tier: '0b', y: 318, name: 'Tier 0b · Measured', sub: 'outcomes: ERCOT and EIA data', measured: true },
      { tier: '0a', y: 400, name: 'Tier 0a · Weather', sub: 'exogenous: nothing causes it', measured: true }
    ];
    var H = 482;
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'kg-svg', role: 'img',
      'aria-label': 'Documents in trust tiers 3, 2 and 1 above measured series in tiers 0b and 0a, with links for the 29 claims checked against measurements: 24 agree and 5 disagree.' });
    stage.insertBefore(svg, stage.firstChild);

    lanes.forEach(function (L) {
      el('rect', { x: 0, y: L.y, width: W, height: LANE_H, fill: L.measured ? 'var(--lane-measured)' : 'var(--lane)', rx: 0 }, svg);
      el('text', { x: 14, y: L.y + 31, class: 'lane-label' }, svg).textContent = L.name;
      el('text', { x: 14, y: L.y + 48, class: 'lane-sub' }, svg).textContent = L.sub;
    });
    var gy = 172 + LANE_H + 30;
    el('text', { x: 14, y: gy, class: 'lane-label' }, svg).textContent = 'Grounding ↓';
    el('text', { x: 14, y: gy + 17, class: 'lane-sub' }, svg).textContent = 'claims matched to a series';
    el('text', { x: 14, y: gy + 31, class: 'lane-sub' }, svg).textContent = 'by unit, region and time';

    var linkLayer = el('g', {}, svg), nodeLayer = el('g', {}, svg);
    var pos = {};
    function place(items, lane, w) {
      var n = items.length, span = W - LX - 16, gap = span / n;
      items.forEach(function (it, i) {
        var cx = LX + gap * (i + 0.5);
        pos[it.id] = { x: cx - w / 2, y: lane.y + 12, w: w, h: LANE_H - 24, cx: cx };
      });
    }
    lanes.forEach(function (L) {
      var docs = KG.evidence.docs.filter(function (d) { return d.tier === L.tier; });
      var ser = KG.evidence.series.filter(function (s) { return s.tier === L.tier; });
      if (docs.length) place(docs, L, Math.min(170, (W - LX - 16) / docs.length - 12));
      if (ser.length) place(ser, L, Math.min(170, (W - LX - 16) / ser.length - 12));
    });

    // aggregate the checked claims into links: document -> series group, split by verdict
    var groups = {};
    KG.evidence.anchors.forEach(function (a) {
      var k = a.doc + '|' + a.series + '|' + (a.agree ? 1 : 0);
      (groups[k] = groups[k] || { doc: a.doc, series: a.series, agree: a.agree, items: [] }).items.push(a);
    });
    var links = Object.keys(groups).map(function (k) { return groups[k]; });
    // spread link ends along each box so links don't stack
    function spread(key, getId) {
      var by = {};
      links.forEach(function (l) { (by[getId(l)] = by[getId(l)] || []).push(l); });
      Object.keys(by).forEach(function (id) {
        var ls = by[id].sort(function (a, b) {
          var pa = pos[key === 'sx' ? a.series : a.doc].cx, pb = pos[key === 'sx' ? b.series : b.doc].cx;
          return pa - pb || (b.agree - a.agree);
        });
        var p = pos[id], n = ls.length, usable = p.w * 0.7;
        ls.forEach(function (l, i) { l[key] = p.cx + (n === 1 ? 0 : -usable / 2 + usable * i / (n - 1)); });
      });
    }
    spread('sx', function (l) { return l.doc; });
    spread('tx', function (l) { return l.series; });

    var linkEls = [];
    links.forEach(function (l) {
      var s = pos[l.doc], t = pos[l.series];
      var y1 = s.y + s.h, y2 = t.y, my = (y1 + y2) / 2;
      var d = 'M' + l.sx + ',' + y1 + ' C' + l.sx + ',' + my + ' ' + l.tx + ',' + my + ' ' + l.tx + ',' + y2;
      var n = l.items.length;
      var g = el('g', {}, linkLayer);
      var path = el('path', { d: d, class: 'link', stroke: l.agree ? 'var(--good)' : 'var(--bad)',
        'stroke-width': 1.6 + Math.sqrt(n) * 1.4 }, g);
      var hit = el('path', { d: d, class: 'hit', tabindex: 0, role: 'button',
        'aria-label': docsById[l.doc].label + ' to ' + seriesById[l.series].label + ': ' + n + (l.agree ? ' agree' : ' disagree') }, g);
      if (n > 1) {
        var bx = (l.sx + l.tx) / 2, by2 = my;
        el('text', { x: bx, y: by2 + 4, 'text-anchor': 'middle', class: 'badge' }, g).textContent = (l.agree ? '✓' : '✗') + n;
      }
      l.el = g; linkEls.push(l);
      function msg() { return '<b>' + esc(docsById[l.doc].label) + '</b> → ' + esc(seriesById[l.series].label) + '<br>' + n + ' claim' + (n > 1 ? 's' : '') + (l.agree ? ' agree ✓' : ' disagree ✗'); }
      hit.addEventListener('mousemove', function (e) { tip.show(e, msg()); });
      hit.addEventListener('mouseleave', tip.hide);
      hit.addEventListener('click', function () { showLink(l); });
      hit.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showLink(l); } });
    });

    var nodeEls = {};
    function drawNode(item, isDoc) {
      var p = pos[item.id];
      var g = el('g', { class: 'node' + (isDoc ? '' : ' measured') + (item.held_out ? ' held' : ''), tabindex: 0, role: 'button',
        'aria-label': item.label }, nodeLayer);
      el('rect', { x: p.x, y: p.y, width: p.w, height: p.h, rx: 8 }, g);
      var l1 = wrap(item.label, Math.floor(p.w / 7.2));
      var sub = isDoc ? (item.held_out ? 'held out: answer key' : item.items.toLocaleString('en-US') + ' items · trust ' + item.reliability.toFixed(2))
                      : item.detail;
      var lines = l1.slice(0, 2), total = lines.length + 1, top = p.y + p.h / 2 - (total - 1) * 7.5;
      lines.forEach(function (t, i) { el('text', { x: p.cx, y: top + i * 15 + 4, 'text-anchor': 'middle', class: 't1' }, g).textContent = t; });
      var subLines = wrap(sub, Math.floor(p.w / 6.2)).slice(0, 1);
      el('text', { x: p.cx, y: top + lines.length * 15 + 3, 'text-anchor': 'middle', class: 't2' }, g).textContent = subLines[0];
      nodeEls[item.id] = g;
      g.addEventListener('click', function () { isDoc ? showDoc(item) : showSeries(item); });
      g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); isDoc ? showDoc(item) : showSeries(item); } });
      g.addEventListener('mouseenter', function () { focus(item.id); });
      g.addEventListener('mouseleave', function () { focus(null); });
    }
    KG.evidence.docs.forEach(function (d) { drawNode(d, true); });
    KG.evidence.series.forEach(function (s) { drawNode(s, false); });

    function focus(id) {
      linkEls.forEach(function (l) { l.el.classList.toggle('dim', !!id && l.doc !== id && l.series !== id); });
    }
    function select(id) {
      Object.keys(nodeEls).forEach(function (k) { nodeEls[k].classList.toggle('sel', k === id); });
    }
    function claimList(items) {
      return '<ul>' + items.map(function (a) {
        var claimed = a.value != null ? fmt(a.value) + (a.unit ? ' ' + esc(a.unit) : '') : esc(a.metric);
        return '<li class="' + (a.agree ? 'ok' : 'no') + '"><span class="q">“' + esc(a.quote) + '”</span><br><span class="v">' +
          (a.agree ? '✓ agrees' : '✗ disagrees') + ' · ' + esc(docsById[a.doc].label) + ' · claimed ' + claimed +
          ' · measured ' + fmt(a.observed) + ' (' + esc(a.series_id.replace(/_/g, ' ')) + ', ' + esc(a.observed_at) + ')</span></li>';
      }).join('') + '</ul>';
    }
    function showLink(l) {
      select(null);
      panel.innerHTML = '<h4>' + esc(docsById[l.doc].label) + ' → ' + esc(seriesById[l.series].label) + '</h4>' +
        '<span class="pill">' + l.items.length + (l.agree ? ' agree' : ' disagree') + '</span>' + claimList(l.items);
    }
    function showDoc(d) {
      select(d.id);
      var items = KG.evidence.anchors.filter(function (a) { return a.doc === d.id; });
      var ok = items.filter(function (a) { return a.agree; }).length;
      var head = '<h4>' + esc(d.label) + '</h4><span class="pill">Tier ' + d.tier + '</span>' +
        (d.flags.length ? d.flags.map(function (f) { return '<span class="pill">' + esc(f.replace(/_/g, ' ')) + '</span>'; }).join('') : '');
      if (d.held_out) {
        panel.innerHTML = head + '<p class="muted">Held out as an answer key: never shown to the extractor. Its timed events are used to score how well the pipeline recovers the timeline.</p>';
        return;
      }
      panel.innerHTML = head + '<p class="muted">' + d.items.toLocaleString('en-US') + ' claims and events extracted. ' +
        (items.length ? items.length + ' could be checked against measured data: ' + ok + ' agree, ' + (items.length - ok) + ' disagree. ' : 'None could be checked against measured data (no matching series, scope or time). ') +
        'Trust ' + d.reliability.toFixed(2) + ' is a Beta posterior that starts at the tier prior (' + d.prior + ') and moves with each check.</p>' +
        (items.length ? claimList(items) : '');
    }
    function showSeries(s) {
      select(s.id);
      var items = KG.evidence.anchors.filter(function (a) { return a.series === s.id; });
      panel.innerHTML = '<h4>' + esc(s.label) + '</h4><span class="pill">Tier ' + s.tier + '</span><span class="pill">' + esc(s.detail) + '</span>' +
        (items.length ? '<p class="muted">' + items.length + ' document claims were checked against this series.</p>' + claimList(items)
          : '<p class="muted">No document claim matched this series by unit, region and time. It is used by the tests and the grid replay instead' +
            (s.id === 'unit_outages' ? ' (it is how the 1,769 MW of coal frequency trips was found)' : '') + '.</p>');
    }
    panel.innerHTML = '<p class="muted">Select a document, a series or a link. Thicker links carry more claims; the number is how many.</p>';

    // table view
    document.getElementById('ev-table').innerHTML = '<table><thead><tr><th>Source</th><th>Tier</th><th>Claim</th><th>Claimed</th><th>Measured</th><th>Verdict</th></tr></thead><tbody>' +
      KG.evidence.anchors.map(function (a) {
        var d = docsById[a.doc];
        return '<tr><td>' + esc(d.label) + '</td><td>' + d.tier + '</td><td>' + esc(a.quote) + '</td><td class="num">' +
          (a.value != null ? fmt(a.value) + ' ' + esc(a.unit || '') : esc(a.metric)) + '</td><td class="num">' + fmt(a.observed) +
          ' · ' + esc(a.observed_at) + '</td><td>' + (a.agree ? '✓ agrees' : '✗ disagrees') + '</td></tr>';
      }).join('') + '</tbody></table>';
  })();

  /* ---------------------------------------------------------------- view 2: causal layer */
  (function causalView() {
    var stage = document.getElementById('ca-stage'), panel = document.getElementById('ca-panel');
    var tip = tipper(stage, document.getElementById('ca-tip'));
    var W = 1080, H = 610, NW = 150, NH = 46;
    var P = {   // gas supply chain upper left; the power chain runs below it from the cold to the consequences
      'PowerLoss': [290, 64], 'GasSupplyDisruption:processing': [290, 144], 'GasSupplyDisruption:pipeline': [290, 224],
      'GasSupplyDisruption:production': [470, 104], 'GasSupplyDisruption:delivery': [470, 194], 'GasSupplyDisruption': [470, 300],
      'WeatherCondition': [90, 380], 'WeatherCondition:warming': [90, 560],
      'GenerationOutage:gas': [650, 210], 'GenerationOutage': [650, 340], 'GenerationOutage:coal': [650, 440], 'GenerationOutage:wind': [650, 530],
      'GridEmergency': [850, 270], 'LoadShed': [850, 430],
      'FacilityShutdown': [1000, 64], 'MarketAction': [1000, 150], 'InstitutionalAction': [1000, 236], 'Restoration': [1000, 560]
    };
    // hand-routed edges: load shed -> power loss at gas facilities closes the gas-power loop; drawn over the top so it
    // can't be mistaken for an edge out of the gas-chain nodes it would otherwise cross
    var ROUTES = {
      'LoadShed>PowerLoss': { d: 'M775,418 Q750,418 750,396 L750,54 Q750,32 728,32 L312,32 Q290,32 290,38',
                              p1: [775, 418], p2: [290, 38], m: [520, 32], label: 'feedback loop' }
    };
    var nodes = KG.causal.nodes, nodeById = {};
    nodes.forEach(function (n) { nodeById[n.id] = n; });
    var edges = KG.causal.edges;

    function kind(e) {
      if (e.status === 'class_test') return 'classtest';
      if (e.status === 'accounting') return 'acct';
      if (e.grade === 'G3' || e.grade === 'G2') return 'pass';
      if (e.status === 'untestable') return 'untestable';
      if (e.result && /not supported/.test(e.result)) return 'fail';
      return 'neutral';
    }
    var STYLE = {
      pass: { stroke: 'var(--good)' }, classtest: { stroke: 'var(--good)', dash: '6 5' },
      fail: { stroke: 'var(--serious)' }, neutral: { stroke: 'var(--ink-3)' },
      untestable: { stroke: 'var(--ink-3)', dash: '2 5' }, acct: { stroke: 'var(--acct)' }
    };
    var STATUS_TEXT = {
      tested_direct: 'Tested against measured data', tested_proxy: 'Tested via a proxy or chart read',
      untestable: 'Untestable: no public series measures one end', accounting: 'Accounting identity (no causal grade)',
      class_test: 'Passed a test on measured data; no document family asserts it'
    };

    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, class: 'kg-svg', role: 'img',
      'aria-label': 'Causal layer: concepts such as cold weather, gas supply disruption, generation outages, grid emergencies and load shed, linked by causal edges coloured by test result.' });
    stage.insertBefore(svg, stage.firstChild);
    var defs = el('defs', {}, svg);
    Object.keys(STYLE).forEach(function (k) {
      var m = el('marker', { id: 'arr-' + k, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 5.5, markerHeight: 5.5, orient: 'auto-start-reverse' }, defs);
      el('path', { d: 'M0,0 L10,5 L0,10 z', fill: STYLE[k].stroke }, m);
    });
    [['Gas supply chain', 380, 16], ['Weather', 90, 330], ['Generation', 650, 170], ['Grid', 850, 230], ['Consequences', 1000, 16]].forEach(function (c) {
      el('text', { x: c[1], y: c[2], 'text-anchor': 'middle', class: 'lane-sub', 'font-weight': 600 }, svg).textContent = c[0].toUpperCase();
    });
    var edgeLayer = el('g', {}, svg), nodeLayer = el('g', {}, svg);

    function clip(cx, cy, tx, ty) {          // point where the segment centre->target leaves the node box
      var dx = tx - cx, dy = ty - cy, hw = NW / 2 + 3, hh = NH / 2 + 3;
      var s = Math.min(Math.abs(dx) > 0 ? hw / Math.abs(dx) : Infinity, Math.abs(dy) > 0 ? hh / Math.abs(dy) : Infinity);
      return [cx + dx * s, cy + dy * s];
    }
    var edgeEls = [];
    edges.forEach(function (e) {
      var a = P[e.src], b = P[e.dst];
      if (!a || !b) return;
      var k = kind(e), st = STYLE[k];
      var dx = b[0] - a[0], dy = b[1] - a[1], len = Math.sqrt(dx * dx + dy * dy) || 1;
      var bend = Math.min(70, 0.16 * len + 14);
      var mx = (a[0] + b[0]) / 2 - dy / len * bend, my = (a[1] + b[1]) / 2 + dx / len * bend;
      var p1 = clip(a[0], a[1], mx, my), p2 = clip(b[0], b[1], mx, my);
      var d = 'M' + p1[0] + ',' + p1[1] + ' Q' + mx + ',' + my + ' ' + p2[0] + ',' + p2[1];
      var route = ROUTES[e.src + '>' + e.dst];
      if (route) { d = route.d; p1 = route.p1; p2 = route.p2; mx = route.m[0]; my = route.m[1]; }
      var g = el('g', {}, edgeLayer);
      el('path', { d: d, class: 'link', stroke: st.stroke, 'stroke-dasharray': st.dash, 'marker-end': 'url(#arr-' + k + ')',
        'stroke-width': (1.4 + Math.min(4, Math.sqrt(Math.max(1, e.support)) * 0.8)).toFixed(2) }, g);
      var hit = el('path', { d: d, class: 'hit', tabindex: 0, role: 'button',
        'aria-label': nodeById[e.src].label + ' to ' + nodeById[e.dst].label + ', ' + (e.grade || 'no grade') }, g);
      var lx = 0.25 * p1[0] + 0.5 * mx + 0.25 * p2[0], ly = 0.25 * p1[1] + 0.5 * my + 0.25 * p2[1];
      if (route) { lx = route.m[0]; ly = route.m[1]; }
      if (k === 'pass' || k === 'classtest' || e.contested || route) {
        el('text', { x: lx, y: ly + 4, 'text-anchor': 'middle', class: 'badge' }, g).textContent =
          route ? route.label : e.contested ? 'contested' : (e.grade + ' ✓');
      }
      var rec = { e: e, g: g, k: k };
      edgeEls.push(rec);
      function msg() {
        return '<b>' + esc(nodeById[e.src].label) + ' → ' + esc(nodeById[e.dst].label) + '</b><br>' + (e.grade || 'no grade') +
          ' · ' + esc(STATUS_TEXT[e.status] || e.status) + (e.support ? '<br>' + e.support + ' supporting relation' + (e.support > 1 ? 's' : '') : '');
      }
      hit.addEventListener('mousemove', function (ev) { tip.show(ev, msg()); });
      hit.addEventListener('mouseleave', tip.hide);
      hit.addEventListener('click', function () { showEdge(e); });
      hit.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); showEdge(e); } });
    });

    var nodeEls = {};
    nodes.forEach(function (n) {
      var p = P[n.id];
      if (!p) return;
      var g = el('g', { class: 'cnode m-' + n.measured, tabindex: 0, role: 'button', 'aria-label': n.label }, nodeLayer);
      el('rect', { x: p[0] - NW / 2, y: p[1] - NH / 2, width: NW, height: NH, rx: 9 }, g);
      var lines = wrap(n.label, 20);
      lines.forEach(function (t, i) {
        el('text', { x: p[0], y: p[1] + 4 + (i - (lines.length - 1) / 2) * 14, 'text-anchor': 'middle' }, g).textContent = t;
      });
      nodeEls[n.id] = g;
      g.addEventListener('mouseenter', function () { hover(n.id); });
      g.addEventListener('mouseleave', function () { hover(null); });
      g.addEventListener('click', function () { showNode(n); });
      g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); showNode(n); } });
    });

    var mode = 'head';
    function visible(r) { return mode === 'all' || r.e.headline; }
    function apply() {
      edgeEls.forEach(function (r) { r.g.style.display = visible(r) ? '' : 'none'; });
    }
    function hover(id) {
      edgeEls.forEach(function (r) { r.g.classList.toggle('dim', !!id && r.e.src !== id && r.e.dst !== id); });
    }
    function sel(id) { Object.keys(nodeEls).forEach(function (k) { nodeEls[k].classList.toggle('sel', k === id); }); }
    function tiers(bt) {
      var ks = Object.keys(bt || {}).sort();
      return ks.length ? ks.map(function (t) { return 'Tier ' + t + ': ' + bt[t]; }).join(' · ') : 'none';
    }
    function showEdge(e) {
      sel(null);
      panel.innerHTML = '<h4>' + esc(nodeById[e.src].label) + ' → ' + esc(nodeById[e.dst].label) + '</h4>' +
        '<span class="pill">' + (e.grade || 'no grade') + '</span><span class="pill">' + esc(STATUS_TEXT[e.status] || e.status) + '</span>' +
        (e.contested ? '<span class="pill">contested</span>' : '') +
        (e.claim ? '<p>' + esc(e.claim) + '</p>' : '') +
        '<p class="v">Supporting relations: ' + e.support + ' (' + tiers(e.by_tier) + ') · contrary: ' + e.contrary +
        (e.regions ? ' · region: ' + esc(e.regions.join(', ')) : '') + '</p>' +
        (e.result ? '<p class="v">Test: ' + esc(e.result.replace(/_/g, ' ')) + '</p>' : '');
    }
    function showNode(n) {
      sel(n.id);
      var es = edges.filter(function (e) { return e.src === n.id || e.dst === n.id; });
      var how = n.measured === 'direct' ? 'Measured directly: ' + esc(n.series.replace(/_/g, ' ').replace('/', ' / ')) :
        n.measured === 'text_derived' ? 'Measured only through FERC Figure 35, read off the chart' : 'No public series measures it, so edges into or out of it are untestable';
      panel.innerHTML = '<h4>' + esc(n.label) + '</h4><p class="muted">' + how + '.</p><ul>' + es.map(function (e) {
        var k = kind(e);
        return '<li class="' + (k === 'pass' || k === 'classtest' ? 'ok' : k === 'fail' ? 'no' : '') + '">' + esc(nodeById[e.src].label) + ' → ' +
          esc(nodeById[e.dst].label) + ' <span class="v">· ' + (e.grade || 'no grade') + ' · ' + esc(STATUS_TEXT[e.status] || e.status) + '</span></li>';
      }).join('') + '</ul>';
    }
    var bH = document.getElementById('f-head'), bA = document.getElementById('f-all');
    var nHead = edges.filter(function (e) { return e.headline; }).length;
    bH.textContent = 'Main edges (' + nHead + ')';
    bA.textContent = 'All edges (' + edges.length + ')';
    function setMode(m) {
      mode = m; bH.setAttribute('aria-pressed', m === 'head'); bA.setAttribute('aria-pressed', m === 'all'); apply();
    }
    bH.addEventListener('click', function () { setMode('head'); });
    bA.addEventListener('click', function () { setMode('all'); });
    apply();
    var first = edges.filter(function (e) { return e.src === 'GridEmergency' && e.dst === 'GenerationOutage:coal'; })[0];
    if (first) showEdge(first);

    document.getElementById('ca-table').innerHTML = '<table><thead><tr><th>Cause → effect</th><th>Grade</th><th>Status</th><th>Support</th><th>Test</th></tr></thead><tbody>' +
      edges.slice().sort(function (a, b) { return (b.grade || '').localeCompare(a.grade || '') || b.support - a.support; }).map(function (e) {
        return '<tr><td>' + esc(nodeById[e.src].label) + ' → ' + esc(nodeById[e.dst].label) + '</td><td>' + (e.grade || '—') + '</td><td>' +
          esc(STATUS_TEXT[e.status] || e.status) + (e.contested ? ' (contested)' : '') + '</td><td class="num">' + e.support + '</td><td>' +
          esc((e.result || '').replace(/_/g, ' ')) + '</td></tr>';
      }).join('') + '</tbody></table>';
  })();

  /* ---------------------------------------------------------------- tabs */
  var tabs = [['tab-ev', 'view-ev'], ['tab-ca', 'view-ca']];
  tabs.forEach(function (t) {
    document.getElementById(t[0]).addEventListener('click', function () {
      tabs.forEach(function (u) {
        var on = u[0] === t[0];
        document.getElementById(u[0]).setAttribute('aria-selected', on);
        document.getElementById(u[1]).hidden = !on;
      });
    });
  });
})();
