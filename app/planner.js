/* 智能排线：按「可用时长 + 兴趣 + 出行方式」生成 3 个候选方案
 *
 * 纯数据驱动，不依赖地图 API（无 AK 也能用）；选定方案后再交给 app.js 做真实路线规划。
 * 三个策略刻意做成不同取舍：
 *   A 紧凑少走  —— 最近邻推进，交通时间最短
 *   B 主题深游  —— 优先命中兴趣标签
 *   C 内容丰富  —— 优先诗文最多、且有清代沿革的地点
 */
(function () {
  "use strict";

  var D = window.DJJWL;
  var SPEED_KMH = { walking: 4.2, riding: 12, driving: 22, transit: 18 };
  var DETOUR = 1.30;          // 路网绕行系数（直线 → 实际里程）
  var MODE_LABEL = { walking: "步行", riding: "骑行", driving: "驾车", transit: "公交", mixed: "混合" };

  function $(s) { return document.querySelector(s); }

  function straight(a, b) {   // → 米
    var R = 6371000, dLat = (b.lat - a.lat) * Math.PI / 180, dLng = (b.lng - a.lng) * Math.PI / 180;
    var la1 = a.lat * Math.PI / 180, la2 = b.lat * Math.PI / 180;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  function legMinutes(mode, meters) {
    var v = SPEED_KMH[mode] || 4.2;
    var min = (meters / 1000) / v * 60 * DETOUR;
    if (mode === "transit") min += 8;      // 等车 + 换乘
    return min;
  }

  // 混合模式：按腿长选交通方式（与 app.js 的 legMode 保持一致）
  function effMode(mode, meters) {
    if (mode !== "mixed") return mode;
    if (meters < 1500) return "walking";
    if (meters < 4000) return "riding";
    return "transit";
  }

  function hitTags(p, tags) {
    if (!tags.length) return 0;
    var n = 0;
    for (var i = 0; i < tags.length; i++) if (p.tags.indexOf(tags[i]) >= 0) n++;
    return n;
  }

  /* ---------- 贪心造线 ---------- */
  function build(seed, pool, budgetMin, mode, st) {
    var stops = [seed], used = {}, cur = seed, covered = {};
    used[seed.id] = 1;
    seed.tags.forEach(function (t) { covered[t] = 1; });
    var visit = seed.visit_minutes || 30, travel = 0, straightSum = 0;

    while (stops.length < st.maxStops) {
      // 候选池很小时（例：「园林」只有 4 处）固定半径会走成死胡同，
      // 于是逐级放宽半径先把行程填满，再由打分决定方案好坏
      var caps = [st.maxLegM, Math.round(st.maxLegM * 1.8), Math.round(st.maxLegM * 3)];
      var best = null, bestCost = Infinity;
      for (var ci = 0; ci < caps.length && !best; ci++) {
        for (var i = 0; i < pool.length; i++) {
          var c = pool[i];
          if (used[c.id]) continue;
          var d = straight(cur, c);
          if (d > caps[ci]) continue;
          var m = effMode(mode, d);
          var tm = legMinutes(m, d);
          if (visit + travel + tm + (c.visit_minutes || 30) > budgetMin) continue;

          var cost = d;
          if (st.preferTag) cost -= hitTags(c, st.tags) * 4000;
          if (st.preferDiverse) {
            // 尽量多覆盖还没出现的类型：没选兴趣时也能与「最近邻」方案区分开
            var fresh = 0;
            for (var k = 0; k < c.tags.length; k++) if (!covered[c.tags[k]]) fresh++;
            cost -= fresh * 2500;
          }
          if (st.preferRich) cost -= c.poem_count * 60 + (c.history_note ? 1200 : 0);
          if (cost < bestCost) { bestCost = cost; best = { p: c, d: d, tm: tm }; }
        }
      }
      if (!best) break;
      stops.push(best.p); used[best.p.id] = 1;
      best.p.tags.forEach(function (t) { covered[t] = 1; });
      visit += (best.p.visit_minutes || 30);
      travel += best.tm;
      straightSum += best.d;
      cur = best.p;
    }
    return { stops: stops, visit: visit, travel: travel, straight: straightSum };
  }

  function scoreOf(r, opts) {
    var n = r.stops.length || 1;
    var tagHit = opts.tags.length
      ? r.stops.filter(function (p) { return hitTags(p, opts.tags) > 0; }).length / n
      : 0.5;
    var used = r.travel + r.visit;
    var util = Math.min(1, used / opts.budget);
    var density = Math.min(1, n / 10);
    var traffic = 1 - Math.min(1, r.travel / opts.budget);
    return 0.35 * tagHit + 0.25 * util + 0.20 * density + 0.20 * traffic;
  }

  var STRATEGIES = [
    { key: "near", name: "紧凑少走", tail: "交通时间最短，顺路连成一串", preferTag: false, preferRich: false, preferDiverse: false, maxLegM: 3500, maxStops: 12 },
    { key: "theme", name: "主题深游", tail: "优先命中兴趣标签，并尽量多覆盖不同类型", preferTag: true, preferRich: false, preferDiverse: true, maxLegM: 6000, maxStops: 10 },
    { key: "rich", name: "内容丰富", tail: "优先诗文最多、有清代核访的地点", preferTag: false, preferRich: true, preferDiverse: false, maxLegM: 8000, maxStops: 9 }
  ];

  /* ---------- 对外：生成 3 个方案 ---------- */
  function build3(opts) {
    var all = (D && D.places) || [];
    if (!all.length) return [];

    var tagged = opts.tags.length
      ? all.filter(function (p) { return hitTags(p, opts.tags) > 0; })
      : all.slice();
    if (tagged.length < 2) tagged = all.slice();

    var out = [], seen = {};
    runStrategies(tagged, opts, out, seen, false);
    // 兴趣筛选可能把可选地点压得很小（例：「佛塔」只剩 5 处），
    // 凑不满 3 个方案时就放宽兴趣再补，并在卡片上标注出来
    if (out.length < 3 && tagged.length !== all.length) {
      runStrategies(all, opts, out, seen, true);
    }
    return out;
  }

  function runStrategies(pool, opts, out, seen, relaxed) {
    var seeds = pool.slice().sort(function (a, b) {
      return (hitTags(b, opts.tags) - hitTags(a, opts.tags)) || (b.poem_count - a.poem_count);
    }).slice(0, 6);

    STRATEGIES.forEach(function (st) {
      if (out.length >= 3) return;
      // 每档策略只用一次：否则放宽兴趣后的补跑会重复出「紧凑少走」等同名方案
      if (out.some(function (o) { return o.key === st.key; })) return;
      st.tags = opts.tags;
      // 对多个种子分别造线，取「分数最高、且与已有方案不同」的那条：
      // 否则两个策略可能退化成同一条线，3 个方案会变成 2 个
      var cands = seeds.map(function (sd) {
        var r = build(sd, pool, opts.budget, opts.mode, st);
        return { r: r, s: scoreOf(r, opts) - (r.stops.length < 2 ? 0.3 : 0) };
      }).sort(function (a, b) { return b.s - a.s; });

      var pick = null;
      for (var i = 0; i < cands.length; i++) {
        if (!cands[i].r.stops.length) continue;
        // 至少 2 站，否则宁可少给一个方案——单点「方案」没有行程价值
        if (cands[i].r.stops.length < 2 && out.length > 0) continue;
        var sig = cands[i].r.stops.map(function (p) { return p.id; }).sort().join(",");
        if (!seen[sig]) { seen[sig] = 1; pick = cands[i]; break; }
      }
      if (!pick) return;
      var bestR = pick.r, bestScore = pick.s;

      out.push({
        key: st.key,
        name: st.name,
        tail: st.tail,
        relaxed: relaxed,
        stops: bestR.stops,
        visit: bestR.visit,
        travel: bestR.travel,
        straight: bestR.straight,
        total: bestR.visit + bestR.travel,
        score: Math.round(bestScore * 100)
      });
    });
  }

  /* ---------- 渲染 ---------- */
  function fmtHM(min) {
    var m = Math.round(min);
    return m >= 60 ? (Math.floor(m / 60) + " 小时 " + (m % 60 ? (m % 60) + " 分" : "")).trim() : m + " 分";
  }

  function render(out) {
    var box = $("#p3Out");
    if (!box) return;
    if (!out.length) { box.innerHTML = "<p class='muted small'>没有匹配的方案，换个兴趣或加长时间试试。</p>"; return; }

    box.innerHTML = out.map(function (o, i) {
      var chain = o.stops.map(function (p, k) { return (k + 1) + ". " + p.ming_name; }).join(" → ");
      return "<div class='p3-card' data-i='" + i + "'>" +
        "<div class='p3-head'><b>方案 " + "ABC"[i] + " · " + o.name + "</b>" +
        "<span class='p3-score'>匹配 " + o.score + "%</span></div>" +
        "<div class='p3-meta'>" + o.tail + (o.relaxed ? "　<span class='badge'>含少量非兴趣点</span>" : "") + "</div>" +
        "<div class='p3-chain'>" + chain + "</div>" +
        "<div class='p3-meta'>" + o.stops.length + " 处 ｜ 直线 " + (o.straight / 1000).toFixed(1) +
        " km ｜ 交通约 " + fmtHM(o.travel) + " ｜ 参观 " + fmtHM(o.visit) +
        " ｜ <b>合计 ≈ " + fmtHM(o.total) + "</b></div>" +
        "<button class='btn' data-apply='" + i + "'>选用此方案</button>" +
        "</div>";
    }).join("") +
      "<p class='muted small'>" + (out.length < 3 ? "可选地点有限，仅能给出 " + out.length + " 个方案；" : "") +
      "里程/耗时按直线距离 ×1.3 路网系数估算；选用后在「行程」里点『生成路线』可得百度返回的真实数值。</p>";

    Array.prototype.forEach.call(box.querySelectorAll("[data-apply]"), function (b) {
      b.onclick = function () { apply(out[Number(b.getAttribute("data-apply"))]); };
    });
  }

  function apply(o) {
    if (!D || !D.applyRoute) return;
    D.applyRoute(o.stops.map(function (p) { return { ming_name: p.ming_name }; }),
      o.name + "（智能排线）",
      o.stops.length + " 处 · 直线 " + (o.straight / 1000).toFixed(1) + " km · 预计合计约 " + fmtHM(o.total) + "（不含用餐）");

    var mode = ($("#p3Mode") && $("#p3Mode").value) || "walking";
    if (D.setRouteMode) D.setRouteMode(mode);
    var ms = $("#mode");
    if (ms) { ms.value = "route"; ms.dispatchEvent(new Event("change")); }   // 切到行程模式
    var rp = $("#routePanel");
    if (rp && rp.scrollIntoView) rp.scrollIntoView({ block: "nearest" });
  }

  /* ---------- 面板初始化 ---------- */
  function init() {
    var tagSel = $("#p3Tag");
    if (tagSel) {
      var tags = {};
      ((D && D.places) || []).forEach(function (p) { p.tags.forEach(function (t) { tags[t] = 1; }); });
      Object.keys(tags).sort().forEach(function (t) {
        var o = document.createElement("option");
        o.value = t; o.textContent = t;
        tagSel.appendChild(o);
      });
    }
    var btn = $("#p3Btn");
    if (btn) btn.onclick = function () {
      var dur = Number(($("#p3Dur") || {}).value || 420);
      var mode = ($("#p3Mode") || {}).value || "walking";
      var tag = (tagSel && tagSel.value) || "";
      var meals = Math.max(0, Math.floor(dur / 420)) * 60;      // 每整天扣 1 小时用餐
      var out = build3({
        budget: Math.max(90, dur - meals),
        mode: mode,
        tags: tag ? [tag] : []
      });
      render(out);
    };
  }

  if (window.DJJWL && D.ready) D.ready(init); else window.addEventListener("load", init);

  window.DJJWL_PLANNER = { build3: build3, render: render };
})();
