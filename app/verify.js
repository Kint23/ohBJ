/* 数据质量 · 坐标核验（用浏览器端 AK 调 JSAPI，无需服务端应用）
 *
 * 30 条坐标目前是人工估计（±300m）。这里用百度地图的地点检索/地理编码逐个核验，
 * 导出 coords_verified.json —— 覆盖到 data/ 后跑 `uv run tools/build_places.py` 即可生效。
 *
 * 面板由本文件动态注入侧栏，因此不必改动 index.html。
 */
(function () {
  "use strict";

  var D = window.DJJWL;
  if (!D || !D.ready) return;

  function $(s) { return document.querySelector(s); }

  function haversine(lng1, lat1, lng2, lat2) {
    var R = 6371000, p1 = lat1 * Math.PI / 180, p2 = lat2 * Math.PI / 180;
    var dp = p2 - p1, dl = (lng2 - lng1) * Math.PI / 180;
    var a = Math.sin(dp / 2) * Math.sin(dp / 2) +
      Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2);
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  /* ---------- 单个地点：地点检索优先，失败再试地理编码；都超时就放弃 ---------- */
  // modern_name 里带括号的补充说明（如「马驹桥（碧霞元君庙旧址一带）」）会让
  // 地点检索跑偏，所以先抽干净名字再搜
  function queryOf(p) {
    var q = String(p.modern_name || "").replace(/[（(][^）)]*[）)]/g, "").trim();
    return q || p.modern_name;
  }

  function resolveOne(p, cb) {
    var settled = false;
    function finish(lng, lat, how) {
      if (settled) return;
      settled = true;
      cb(lng, lat, how);
    }
    var timer = setTimeout(function () { finish(null, null, "超时"); }, 6000);
    var map = D.getMap && D.getMap();
    if (!map || !window.BMapGL) { clearTimeout(timer); finish(null, null, "无地图"); return; }

    var fell = false;
    function tryGeocoder() {
      if (settled || fell) return;
      fell = true;
      if (!BMapGL.Geocoder) { clearTimeout(timer); finish(null, null, "无地理编码"); return; }
      try {
        new BMapGL.Geocoder().getPoint(p.address || p.modern_name, function (pt) {
          clearTimeout(timer);
          if (pt && pt.lng && pt.lat) finish(pt.lng, pt.lat, "地理编码");
          else finish(null, null, "未解析");
        }, "北京");
      } catch (e) {
        clearTimeout(timer); finish(null, null, "异常");
      }
    }

    if (!BMapGL.LocalSearch) { tryGeocoder(); return; }
    try {
      var s = new BMapGL.LocalSearch(map, {
        pageCapacity: 1,
        onSearchComplete: function (res) {
          if (settled) return;
          var pt = null;
          try {
            if (res && res.getCurrentNumPois && res.getCurrentNumPois() > 0) {
              var poi = res.getPoi(0);
              pt = poi.point || poi.latLng || null;
            }
          } catch (e) { /* 忽略，走回退 */ }
          if (pt && pt.lng) { clearTimeout(timer); finish(pt.lng, pt.lat, "地点检索"); }
          else { clearTimeout(timer); finish(null, null, "未命中"); }
        }
      });
      s.search(queryOf(p));
    } catch (e) {
      clearTimeout(timer);
      finish(null, null, "异常");
    }
  }

  /* ---------- 面板 ---------- */
  function init() {
    var sidebar = document.querySelector(".sidebar");
    if (!sidebar) return;

    var sec = document.createElement("section");
    sec.className = "panel";
    sec.innerHTML =
      "<h2>数据质量 · 坐标核验</h2>" +
      "<p class='muted small'>30 条坐标为人工估计（±300m）。核验用百度地点检索逐个比对，" +
      "导出结果后跑一次构建即可升级为「已核验」。</p>" +
      "<button id='vfBtn' class='btn'>核验 30 个坐标</button>" +
      "<div id='vfOut'></div>";
    sidebar.appendChild(sec);

    var btn = $("#vfBtn");
    var out = $("#vfOut");
    btn.onclick = function () {
      var places = (D.places || []).slice();
      if (!places.length) { out.innerHTML = "<p class='muted small'>地点数据未就绪。</p>"; return; }
      if (!D.hasMap || !D.hasMap()) {
        out.innerHTML = "<p class='muted small'>地图未加载，无法核验（需浏览器端 AK）。</p>";
        return;
      }
      btn.disabled = true;
      var results = [], i = 0;

      function step() {
        if (i >= places.length) { report(results); return; }
        var p = places[i];
        btn.textContent = "核验中 " + (i + 1) + "/" + places.length + " …";
        resolveOne(p, function (lng, lat, how) {
          if (lng != null) {
            results.push({
              id: p.id, ming_name: p.ming_name, modern_name: p.modern_name,
              lng: lng, lat: lat, distance_m: Math.round(haversine(p.lng, p.lat, lng, lat)),
              matched_by: how
            });
          } else {
            results.push({ id: p.id, ming_name: p.ming_name, modern_name: p.modern_name,
              lng: null, lat: null, distance_m: null, matched_by: how });
          }
          i++;
          setTimeout(step, 150);          // 轻微限速，照顾配额
        });
      }
      step();
    };

    function report(results) {
      window.__coordsVerified = results;     // 便于导出/排查，不影响页面
      btn.disabled = false;
      btn.textContent = "重新核验";
      var ok = results.filter(function (r) { return r.lng != null; });
      var near = ok.filter(function (r) { return r.distance_m < 300; }).length;
      var mid = ok.filter(function (r) { return r.distance_m >= 300 && r.distance_m < 1000; }).length;
      var far = ok.filter(function (r) { return r.distance_m >= 1000; }).length;

      var rows = results.map(function (r) {
        var d = r.distance_m;
        var cls = d == null ? "" : (d < 300 ? "conf-high" : d < 1000 ? "conf-medium" : "conf-low");
        return "<tr><td>" + r.ming_name + "</td><td>" +
          (d == null ? "—" : d + " m") + " <span class='badge " + cls + "'>" +
          (d == null ? r.matched_by : (d < 300 ? "符合" : d < 1000 ? "略有偏差" : "偏差较大")) +
          "</span></td></tr>";
      }).join("");

      out.innerHTML =
        "<p class='small'>命中 " + ok.length + "/" + results.length +
        "；<b>" + near + "</b> 条 &lt;300m，" + mid + " 条 300–1000m，" + far + " 条 &gt;1km。</p>" +
        (ok.length ? "<button id='vfDl' class='btn primary'>下载 coords_verified.json</button>" : "") +
        "<table class='small'><tr><td><b>地点</b></td><td><b>与现坐标偏差</b></td></tr>" + rows + "</table>" +
        "<p class='muted small'>把下载的文件存为 <code>data/coords_verified.json</code>，" +
        "再跑 <code>uv run tools/build_places.py</code>，" +
        "偏差 &lt;3km 的会自动写回并标记为「坐标已用 WebAPI 核验」。</p>";

      var dl = $("#vfDl");
      if (dl) dl.onclick = function () {
        var doc = { places: ok.map(function (r) {
          return { id: r.id, ming_name: r.ming_name, modern_name: r.modern_name,
                   lng: r.lng, lat: r.lat, distance_m: r.distance_m };
        }) };
        var blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "coords_verified.json";
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      };
    }
  }

  D.ready(init);
})();
