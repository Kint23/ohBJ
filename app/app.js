/* 帝京寻踪 · 前端逻辑
 * 依赖：百度地图 JSAPI GL（BMapGL）
 * 数据：data/places.json（30 地，BD-09）、data/routes.json（行程分档）
 */
(function () {
  "use strict";

  var CFG = window.APP_CONFIG || {};
  var $ = function (s) { return document.querySelector(s); };

  var S = {
    places: [], meta: null, volumes: [], routes: null,
    filter: { q: "", volume: "", tag: "", cluster: "" },
    script: "trad",
    selectedId: null,
    route: null,
    labels: {}, markers: {}, layers: []
  };

  var map = null;
  var localSearch = null;

  /* 对外 API：供 app/guide.js（AI 导游）调用 */
  var DJJWL = {
    _loaded: false,
    _queue: [],
    ready: function (fn) { if (DJJWL._loaded) fn(DJJWL); else DJJWL._queue.push(fn); },
    hasMap: function () { return !!map; },
    // 地图加载是一次异步过程，分三种结果，供 AI 导游等模块正确提示
    mapState: "pending",          // pending | ready | failed
    _mapCbs: [],
    onMapResult: function (fn) {
      if (DJJWL.mapState !== "pending") fn(DJJWL.mapState);
      else DJJWL._mapCbs.push(fn);
    }
  };
  function settleMap(state) {
    if (DJJWL.mapState !== "pending") return;
    DJJWL.mapState = state;
    var cbs = DJJWL._mapCbs; DJJWL._mapCbs = [];
    cbs.forEach(function (fn) { try { fn(state); } catch (e) { console.error(e); } });
  }
  window.DJJWL = DJJWL;

  /* ---------------- 启动 ---------------- */
  // 先渲染数据界面（不依赖地图 API），再异步加载地图
  boot();

  function loadMapApi() {
    return new Promise(function (resolve) {
      if (window.BMapGL) return resolve(true);
      // 未配置 AK 时不要发请求：百度 JSAPI 在 AK 无效时会弹阻塞式 alert，拖死整页
      if (!CFG.ak || !String(CFG.ak).trim()) return resolve(false);
      var settled = false;
      var finish = function (ok) { if (!settled) { settled = true; resolve(ok); } };
      window.onBMapReady(function () { finish(true); });
      var s = document.createElement("script");
      s.async = true;
      s.src = "https://api.map.baidu.com/api?type=webgl&v=1.0&ak=" +
        encodeURIComponent(CFG.ak || "") + "&callback=__bmapReady";
      s.onerror = function () { finish(false); };
      document.head.appendChild(s);
      window.setTimeout(function () { finish(!!window.BMapGL); }, 9000);
    });
  }

  function boot() {
    Promise.all([
      // cache: "no-cache" = 每次向服务器校验（通常 304，很便宜），
      // 避免更新了 data/*.json 之后浏览器还在用旧副本
      fetch("data/places.json", { cache: "no-cache" }).then(function (r) { return r.json(); }),
      fetch("data/routes.json", { cache: "no-cache" }).then(function (r) { return r.json(); })
    ]).then(function (res) {
      S.places = res[0].places;
      S.meta = res[0].meta;
      S.volumes = res[0].volumes;
      S.routes = res[1];
      renderStats();
      renderChips();
      renderList();
      bindUi();

      // 数据就绪 → 唤醒等待中的模块（如 AI 导游）
      DJJWL.places = S.places;
      DJJWL.routes = S.routes;
      DJJWL.meta = S.meta;
      DJJWL.volumes = S.volumes;
      DJJWL._loaded = true;
      while (DJJWL._queue.length) { try { DJJWL._queue.shift()(DJJWL); } catch (e) { console.error(e); } }

      loadMapApi().then(function (ok) {
        if (ok && window.BMapGL) {
          initMap();
          settleMap("ready");
        } else {
          settleMap("failed");
          showNotice("地图底图未加载。两个常见原因：① app/config.js 的 ak 未填；" +
            "② ak 不是【浏览器端】类型（服务端 AK 会报「APP不存在，AK有误」），" +
            "或未把 localhost/正式域名加入 Referer 白名单。" +
            "文字浏览、原文、今译、行程、AI 导游不受影响。");
        }
      });
    }).catch(function (e) {
      showNotice("数据加载失败：" + e.message +
        " —— 请用本地服务器打开（如 python -m http.server 8080），不要直接双击 index.html。");
    });
  }

  /* ---------------- 地图 ---------------- */
  function initMap() {
    map = new BMapGL.Map("map");
    map.centerAndZoom(new BMapGL.Point(CFG.center[0], CFG.center[1]), CFG.zoom || 12);
    map.enableScrollWheelZoom(true);
    map.addControl(new BMapGL.ScaleControl());
    // 公交路线规划需要地图当前城市
    if (typeof map.setCurrentCity === "function") map.setCurrentCity("北京");
    localSearch = new BMapGL.LocalSearch(map, { pageCapacity: 5 });

    S.places.forEach(function (p) {
      var pt = new BMapGL.Point(p.lng, p.lat);

      if (p.coord_precision === "area") {
        var circle = new BMapGL.Circle(pt, 380, {
          strokeColor: "#2f6f9e", strokeWeight: 1, strokeOpacity: .8,
          fillColor: "#2f6f9e", fillOpacity: .08
        });
        map.addOverlay(circle);
      }

      var marker = new BMapGL.Marker(pt);
      marker.addEventListener("click", function () { select(p.id); });
      map.addOverlay(marker);
      S.markers[p.id] = marker;

      var label = new BMapGL.Label(p.ming_name, { position: pt, offset: new BMapGL.Size(14, -34) });
      label.setStyle(cfgLabelStyle(p));
      label.setTitle(p.ming_name + " → " + p.modern_name);
      label.addEventListener("click", function () { select(p.id); });
      map.addOverlay(label);
      S.labels[p.id] = label;
    });
  }

  function cfgLabelStyle(p) {
    return {
      fontSize: "12px", color: "#3a3126", background: "rgba(255,253,248,.92)",
      border: "1px solid " + (p.coord_precision === "area" ? "#2f6f9e" : "#ddd0b6"),
      borderRadius: "10px", padding: "1px 6px", whiteSpace: "nowrap", cursor: "pointer"
    };
  }

  /* ---------------- 统计 / 筛选控件 ---------------- */
  function renderStats() {
    $("#statPlaces").textContent = S.places.length;
    $("#statPoems").textContent = S.places.reduce(function (a, p) { return a + p.poem_count; }, 0);
    $("#statNotes").textContent = S.places.filter(function (p) { return p.history_note; }).length;
    $("#count").textContent = "（共 " + S.places.length + " 处）";
  }

  function chip(text, on, onclick) {
    var b = document.createElement("span");
    b.className = "chip" + (on ? " on" : "");
    b.textContent = text;
    b.onclick = onclick;
    return b;
  }

  function renderChips() {
    var vf = $("#volumeFilter");
    vf.innerHTML = "";
    S.volumes.forEach(function (v) {
      if (v.scope === "京外") return; // 京外卷不在本版范围
      vf.appendChild(chip(v.id.replace("卷", "") + "·" + v.name, S.filter.volume === v.id, function () {
        S.filter.volume = S.filter.volume === v.id ? "" : v.id;
        renderChips(); renderList();
      }));
    });

    var tf = $("#tagFilter");
    tf.innerHTML = "";
    var tags = {};
    S.places.forEach(function (p) { p.tags.forEach(function (t) { tags[t] = 1; }); });
    Object.keys(tags).sort().forEach(function (t) {
      tf.appendChild(chip(t, S.filter.tag === t, function () {
        S.filter.tag = S.filter.tag === t ? "" : t;
        renderChips(); renderList();
      }));
    });

    var cf = $("#clusterFilter");
    cf.innerHTML = "";
    var clusters = S.places.map(function (p) { return p.cluster; })
      .filter(function (v, i, a) { return a.indexOf(v) === i; }).sort();
    clusters.forEach(function (c) {
      cf.appendChild(chip(c, S.filter.cluster === c, function () {
        S.filter.cluster = S.filter.cluster === c ? "" : c;
        renderChips(); renderList();
      }));
    });
  }

  function matched() {
    var q = S.filter.q.trim().toLowerCase();
    return S.places.filter(function (p) {
      if (S.filter.volume && p.volume !== S.filter.volume) return false;
      if (S.filter.tag && p.tags.indexOf(S.filter.tag) < 0) return false;
      if (S.filter.cluster && p.cluster !== S.filter.cluster) return false;
      if (!q) return true;
      var hay = [p.ming_name, p.ming_name_simp, p.modern_name, p.address,
        p.poem_titles.join(" "), p.poems.map(function (x) { return x.author; }).join(" "),
        p.tags.join(" ")].join(" ");
      return hay.toLowerCase().indexOf(q) >= 0;
    });
  }

  function renderList() {
    var list = $("#placeList");
    list.innerHTML = "";
    var arr = matched();
    $("#count").textContent = "（" + arr.length + "/" + S.places.length + "）";

    arr.forEach(function (p) {
      var li = document.createElement("li");
      if (p.id === S.selectedId) li.className = "on";
      li.innerHTML =
        '<div class="nm"><i class="dot ' + (p.coord_precision === "area" ? "area" : p.confidence) + '"></i>' +
        '<b>' + p.ming_name + '</b></div>' +
        '<div class="now">→ ' + p.modern_name + '</div>' +
        '<div class="mt">' + p.volume + '·' + p.volume_name + ' ｜ 诗 ' + p.poem_count + ' 首 ｜ 约 ' + p.visit_minutes + ' 分钟</div>';
      li.onclick = function () { select(p.id, true); };
      list.appendChild(li);
    });
  }

  /* ---------------- 选中与详情 ---------------- */
  function select(id, panTo) {
    var p = S.places.filter(function (x) { return x.id === id; })[0];
    if (!p) return;

    if (map && S.selectedId && S.labels[S.selectedId]) {
      S.labels[S.selectedId].setStyle(cfgLabelStyle(S.places.filter(function (x) { return x.id === S.selectedId; })[0]));
    }
    S.selectedId = id;
    if (map && S.labels[id]) S.labels[id].setStyle({
      fontSize: "12px", color: "#fff", background: "#9c2b25", border: "1px solid #9c2b25",
      borderRadius: "10px", padding: "1px 6px", whiteSpace: "nowrap", cursor: "pointer"
    });

    if (map) {
      if (panTo) map.panTo(new BMapGL.Point(p.lng, p.lat));
      map.openInfoWindow(new BMapGL.InfoWindow(infoHtml(p), { width: 280, offset: new BMapGL.Size(0, -18) }),
        new BMapGL.Point(p.lng, p.lat));
    }

    $("#detail").innerHTML = detailHtml(p);
    bindDetail(p);
    renderList();
  }

  function infoHtml(p) {
    return '<div class="iw"><b>' + p.ming_name + '</b><br/>' +
      '<span class="now">' + p.modern_name + '</span><br/>' +
      esc(p.address) + '<br/>' +
      '<span style="color:#8c8271">' + p.volume + '·' + p.volume_name + ' · 诗 ' + p.poem_count + ' 首</span>' +
      '</div>';
  }

  function detailHtml(p) {
    var excerpt = S.script === "trad" ? p.excerpt : p.excerpt_simp;
    var conf = { high: "坐标可信度高", medium: "坐标待核", low: "坐标存疑" }[p.confidence] || p.confidence;

    var html = "";
    html += '<h2 class="d-title">' + p.ming_name + '</h2>';
    html += '<p class="d-now">' + p.modern_name + '</p>';
    html += '<p class="d-addr">' + esc(p.address) + ' ｜ 建议 ' + p.visit_minutes + ' 分钟</p>';

    html += '<div class="tag-badges">';
    html += '<span class="badge">' + p.volume + '·' + p.volume_name + '</span>';
    html += '<span class="badge conf-' + p.confidence + '">' + conf + '</span>';
    if (p.coord_source === "verified-by-ak") html += '<span class="badge verified">坐标已用百度 WebAPI 核验</span>';
    else html += '<span class="badge">坐标为人工估计</span>';
    if (p.coord_precision === "area") html += '<span class="badge">范围地名</span>';
    p.tags.forEach(function (t) { html += '<span class="badge">' + t + '</span>'; });
    html += '</div>';

    html += '<div class="linkbar">' +
      '<a href="' + navUrl(p, "walking") + '" target="_blank" rel="noopener">步行</a>' +
      '<a href="' + navUrl(p, "riding") + '" target="_blank" rel="noopener">骑行</a>' +
      '<a href="' + navUrl(p, "driving") + '" target="_blank" rel="noopener">驾车</a>' +
      '<a href="' + navUrl(p, "transit") + '" target="_blank" rel="noopener">公交</a>' +
      '<button data-near="餐厅">周边餐饮</button>' +
      '<button data-near="公共厕所">周边厕所</button>' +
      '</div>';
    html += '<div class="nearby" id="nearby"></div>';

    if (p.vernacular) {
      html += '<div class="d-sec"><h3>白话今译 <span class="muted small">AI 辅助译文</span></h3>' +
        '<div class="vern">' + esc(p.vernacular) + '</div></div>';
    }

    html += '<div class="d-sec"><h3>原书记载 · 《帝京景物略》' +
      '<button id="scriptToggle">' + (S.script === "trad" ? "转简体" : "看原文") + '</button></h3>' +
      '<div class="quote">' + esc(excerpt) + '…</div></div>';

    if (p.poems.length) {
      html += '<div class="d-sec"><h3>关联诗篇 <span class="muted small">共 ' + p.poem_count + ' 首，列 ' + p.poems.length + ' 首</span></h3>';
      p.poems.forEach(function (poem) {
        html += '<details class="poem"><summary>' +
          '<span class="who">' + [poem.dynasty, poem.origin, poem.author].filter(Boolean).join("·") + '</span>' +
          '《' + poem.title + '》</summary>' +
          '<div class="body">' + esc(poem.text) + '</div></details>';
      });
      html += '</div>';
    }

    if (p.history_note) {
      html += '<div class="d-sec"><h3>清人实地核访 <span class="muted small">' + esc(p.history_source) + '</span></h3>';
      if (p.history_vernacular) {
        html += '<div class="vern"><span class="muted small">白话今译 · AI 辅助译文</span><br/>' +
          esc(p.history_vernacular) + '</div>';
      }
      html += '<div class="quote">' + esc(p.history_note) + '…</div></div>';
    }

    html += '<div class="d-sec"><h3>数据</h3><p class="small muted">' +
      'BD-09 坐标 ' + p.lng.toFixed(5) + ', ' + p.lat.toFixed(5) +
      ' ｜ 原文 ' + p.text_len + ' 字 ｜ 关联地名 ' + (p.mentions.length ? p.mentions.join("、") : "无") +
      '</p></div>';

    return html;
  }

  function bindDetail(p) {
    var t = $("#scriptToggle");
    if (t) t.onclick = function () { S.script = S.script === "trad" ? "simp" : "trad"; select(p.id); };

    Array.prototype.forEach.call(document.querySelectorAll("[data-near]"), function (b) {
      b.onclick = function () { nearby(p, b.getAttribute("data-near")); };
    });
  }

  function nearby(p, keyword) {
    var box = $("#nearby");
    if (!localSearch) {
      box.innerHTML = '<a href="https://map.baidu.com/search/' + encodeURIComponent(keyword) +
        '/@' + p.lng + ',' + p.lat + ',18z" target="_blank" rel="noopener">在百度地图中查看周边' + esc(keyword) + '</a>';
      return;
    }
    box.innerHTML = "正在检索 " + keyword + "…";
    localSearch.setSearchCompleteCallback(function (res) {
      if (!res || !res.getCurrentNumPois || !res.getCurrentNumPois()) {
        box.innerHTML = "1 公里内未检索到" + keyword + "。";
        return;
      }
      var html = "<ul>";
      for (var i = 0; i < Math.min(res.getCurrentNumPois(), 5); i++) {
        var poi = res.getPoi(i);
        html += "<li>" + (i + 1) + ". " + esc(poi.title) +
          (poi.address ? " — " + esc(poi.address) : "") + "</li>";
      }
      box.innerHTML = html + "</ul>";
    });
    localSearch.searchNearby(keyword, new BMapGL.Point(p.lng, p.lat), 1000);
  }

  function navUrl(p, mode) {
    var dest = "latlng:" + p.lat + "," + p.lng + "|name:" + encodeURIComponent(p.modern_name);
    return "https://api.map.baidu.com/direction?destination=" + dest +
      "&mode=" + mode + "&region=" + encodeURIComponent("北京") +
      "&output=html&src=webapp.djjwl.ancientmap";
  }

  /* ---------------- 行程模式 ---------------- */
  function bindUi() {
    $("#q").addEventListener("input", function (e) {
      S.filter.q = e.target.value; renderList();
    });
    $("#mode").addEventListener("change", function (e) {
      var on = e.target.value === "route";
      $("#routePanel").hidden = !on;
      if (!on) clearRoute();
      else renderRoutes();
    });
    $("#planBtn").addEventListener("click", function () {
      if (S.route) planRoute(S.route.stops);
    });
    // 切换出行方式 → 若已有行程则自动重新规划
    var rm = $("#routeMode");
    if (rm) rm.addEventListener("change", function () {
      if (S.route) planRoute(S.route.stops);
    });
  }

  function renderRoutes() {
    var box = $("#routeList");
    box.innerHTML = "";

    S.routes.itineraries.forEach(function (it) {
      var d = document.createElement("div");
      d.className = "route-item";
      d.innerHTML = "<b>" + it.name + "</b><span>" +
        it.stops_total + " 处 · 约 " + it.est_km + " km · " + it.days + " 天</span>";
      d.onclick = function () {
        var stops = [];
        it.plan.forEach(function (day) {
          if (day.reuse) {
            var src = findDay(day.reuse);
            if (src) stops = stops.concat(src.stops);
          } else if (day.stops) {
            stops = stops.concat(day.stops);
          }
        });
        setRoute(d, it.name, stops, it.notes);
      };
      box.appendChild(d);
    });

    S.routes.walk_circles.forEach(function (c) {
      var d = document.createElement("div");
      d.className = "route-item";
      d.innerHTML = "<b>半日 · " + c.name + "</b><span>" +
        c.stops.length + " 处 · 约 " + c.est_km + " km · " + c.hours + " 小时</span>";
      d.onclick = function () {
        setRoute(d, "半日 · " + c.name + "（" + c.theme + "）",
          c.stops.map(function (n) { return { ming_name: n }; }), "");
      };
      box.appendChild(d);
    });
  }

  function findDay(ref) {
    var parts = String(ref).split(".");              // 例： "d2.day1"
    var it = S.routes.itineraries.filter(function (x) { return x.id === parts[0]; })[0];
    if (!it) return null;
    var day = Number(String(parts[1]).replace("day", ""));
    return it.plan.filter(function (d) { return d.day === day; })[0] || null;
  }

  function setRoute(el, name, rawStops, notes) {
    Array.prototype.forEach.call(document.querySelectorAll(".route-item"), function (n) { n.className = "route-item"; });
    if (el) el.className = "route-item on";
    clearRoute(true);

    var stops = rawStops.map(function (s) {
      var p = byMingName(s.ming_name);
      return p ? Object.assign({ stay: s.stay || p.visit_minutes }, p) : null;
    }).filter(Boolean);

    S.route = { name: name, stops: stops };
    $("#planBtn").disabled = false;
    $("#routeResult").innerHTML = "<b>" + name + "</b>" + (notes ? "<br/>" + esc(notes) : "");

    // 序号徽标 + 连线（仅在有地图时）
    if (map) {
      var pts = [];
      stops.forEach(function (p, i) {
        var pt = new BMapGL.Point(p.lng, p.lat);
        pts.push(pt);
        var badge = new BMapGL.Label(String(i + 1), { position: pt, offset: new BMapGL.Size(-26, -12) });
        badge.setStyle({
          fontSize: "12px", fontWeight: "700", color: "#fff", background: "#9c2b25",
          border: "2px solid #fff", borderRadius: "50%", width: "18px", height: "18px",
          lineHeight: "16px", textAlign: "center"
        });
        map.addOverlay(badge); S.layers.push(badge);
      });
      var line = new BMapGL.Polyline(pts, {
        strokeColor: "#9c2b25", strokeWeight: 3, strokeOpacity: .75, strokeStyle: "dashed"
      });
      map.addOverlay(line); S.layers.push(line);
      if (pts.length) map.setViewport(pts);
    }

    var rows = stops.map(function (p, i) {
      return "<tr><td>" + (i + 1) + "</td><td>" + p.ming_name + "</td><td>" +
        (p.stay || "–") + " 分</td><td>直线约 " + (i ? km(stops[i - 1], p) : "–") + "</td></tr>";
    }).join("");
    $("#routeResult").innerHTML +=
        "<table><tr><td>#</td><td>地名</td><td>停留</td><td>前一段(直线)</td></tr>" + rows + "</table>" +
      "<p class='muted small'>上方为直线距离近似；选好出行方式后点『生成路线』，用百度路线规划计算真实里程与耗时。</p>";
  }

  /* ---------------- 路线规划（多出行方式） ---------------- */
  var ROUTE_CLASS = { walking: "WalkingRoute", riding: "RidingRoute", driving: "DrivingRoute", transit: "TransitRoute" };
  var MODE_LABEL = { walking: "步行", riding: "骑行", driving: "驾车", transit: "公交", mixed: "混合" };

  // 混合模式：按相邻两点直线距离自动挑交通方式
  function legMode(distM) {
    if (distM < 1500) return "walking";
    if (distM < 4000) return "riding";
    return "transit";
  }

  function straightMeters(a, b) {
    var R = 6371000, dLat = (b.lat - a.lat) * Math.PI / 180, dLng = (b.lng - a.lng) * Math.PI / 180;
    var la1 = a.lat * Math.PI / 180, la2 = b.lat * Math.PI / 180;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // BMapGL 的 getDistance()/getDuration() 在不同版本可能返回 number、
  // 带单位的字符串（如 "1.5公里"）、或格式化字符串（getDistance(true)），一律解析成数值。
  function parseDist(v) {                 // → 米
    if (v == null) return null;
    if (typeof v === "number") return (isFinite(v) && v > 0) ? v : null;
    var s = String(v), m = s.match(/([\d.]+)/);
    if (!m) return null;
    var n = Number(m[1]);
    if (!isFinite(n) || n <= 0) return null;
    if (/公里|千米|km|KM/.test(s)) n *= 1000;
    return n;
  }

  function parseDur(v) {                  // → 秒
    if (v == null) return null;
    if (typeof v === "number") return (isFinite(v) && v > 0) ? v : null;
    var s = String(v);
    // 兼容 "35分钟" / "1小时20分钟" / "1小时" 等组合形式
    var h = s.match(/([\d.]+)\s*(?:小时|时)/);
    var mi = s.match(/([\d.]+)\s*(?:分钟|分)/);
    var sec = 0;
    if (h) sec += Number(h[1]) * 3600;
    if (mi) sec += Number(mi[1]) * 60;
    if (!sec) {
      var n = s.match(/([\d.]+)/);
      if (n) sec = Number(n[1]);          // 纯数字字符串按秒处理
    }
    return (isFinite(sec) && sec > 0) ? Math.round(sec) : null;
  }

  // 百度未返回耗时时的估算（城市内均速，含红绿灯/等车）
  var SPEED_KMH = { walking: 4.2, riding: 12, driving: 22, transit: 18 };
  function estimateSeconds(mode, meters) {
    var v = SPEED_KMH[mode] || 4.2;
    var sec = meters / 1000 / v * 3600;
    if (mode === "transit") sec += 8 * 60;   // 等车 + 换乘
    return Math.round(sec);
  }

  // 可重复调用的结果块（重新规划时替换而不是叠加）
  function outBlock(html) {
    var old = $("#routePlanOut");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var div = document.createElement("div");
    div.id = "routePlanOut";
    div.innerHTML = html;
    $("#routeResult").appendChild(div);
  }

  function planRoute(stops) {
    if (!stops || stops.length < 2) return;
    var mode = ($("#routeMode") && $("#routeMode").value) || "walking";
    if (!map) {
      outBlock("<p class='muted small'>地图未加载，暂时无法调用路线规划；上表为直线距离近似。</p>");
      return;
    }
    $("#planBtn").disabled = true;

    var legs = [], totalM = 0, totalS = 0, i = 0;
    var segs = stops.length - 1;

    function progress() { $("#planBtn").textContent = "正在规划 " + i + "/" + segs + " …"; }
    progress();

    function next() {
      if (i >= segs) {
        var walkM = legs.reduce(function (a, l) { return a + (l.mode === "walking" && l.m ? l.m : 0); }, 0);
        var rows = legs.map(function (l, k) {
          return "<tr><td>" + (k + 1) + "</td><td>" + stops[k].ming_name + " → " + stops[k + 1].ming_name +
            "</td><td>" + (MODE_LABEL[l.mode] || "?") + "</td><td>" +
            (l.failed ? "失败" : (isFinite(l.m) ? Math.round(l.m) + " m" : "–")) + "</td><td>" +
            (l.s && isFinite(l.s) ? (l.est ? "≈" : "") + Math.round(l.s / 60) + " 分" : "–") + "</td></tr>";
        }).join("");
        var estLegs = legs.filter(function (l) { return l.est; }).length;
        // 公交/绕行线路的里程可能远大于直线距离，这不是错误，但要向用户说明
        var odd = [];
        legs.forEach(function (l, k) {
          var sl = straightMeters(stops[k], stops[k + 1]);
          if (l.m && sl && l.m / sl > 2.5) odd.push(k + 1);
        });
        var oddTxt = odd.length
          ? "<p class='muted small'>第 " + odd.join("、") + " 段里程明显大于直线距离（公交走向或绕行所致，非错误）；实际以百度地图导航为准。</p>"
          : "";
        var kmTxt = isFinite(totalM) ? (totalM / 1000).toFixed(2) + " km" : "里程未获取";
        var timeTxt = (totalS && isFinite(totalS))
          ? "，纯交通耗时约 " + (estLegs ? "≈" : "") + Math.round(totalS / 60) + " 分钟" : "";
        var walkTxt = (mode === "mixed" && isFinite(walkM)) ? "；其中步行 " + (walkM / 1000).toFixed(2) + " km" : "";
        var noteTxt = estLegs
          ? "<p class='muted small'>其中 " + estLegs + " 段为估算（百度未返回该线路的数值，用直线距离与经验速度推算）。</p>" : "";
        outBlock("<p><b>" + (MODE_LABEL[mode] || mode) + "路线：</b>共 " + legs.length + " 段，" +
          kmTxt + timeTxt + "（不含参观停留）" + walkTxt + "</p>" +
          "<table><tr><td>#</td><td>路段</td><td>方式</td><td>距离</td><td>耗时</td></tr>" + rows + "</table>" +
          noteTxt + oddTxt);
        $("#planBtn").disabled = false;
        $("#planBtn").textContent = "重新规划路线";
        return;
      }

      var a = new BMapGL.Point(stops[i].lng, stops[i].lat);
      var b = new BMapGL.Point(stops[i + 1].lng, stops[i + 1].lat);
      var useMode = (mode === "mixed") ? legMode(straightMeters(stops[i], stops[i + 1])) : mode;
      var Cls = window.BMapGL ? BMapGL[ROUTE_CLASS[useMode]] : null;
      if (!Cls) {
        legs.push({ mode: useMode, m: straightMeters(stops[i], stops[i + 1]), failed: true });
        i++; progress(); next(); return;
      }

      var route = new Cls(map, {
        renderOptions: { map: map, autoViewport: false, selectFirstResult: true },
        onSearchComplete: function (res) {
          var m = null, sec = null;
          try {
            var plan = (res && res.getPlan) ? res.getPlan(0) : null;
            if (plan && typeof plan.getDistance === "function") {
              m = parseDist(plan.getDistance());
              if (!m) m = parseDist(plan.getDistance(true));      // 带单位字符串
            }
            if (plan && !sec && typeof plan.getDuration === "function") {
              sec = parseDur(plan.getDuration());
              if (!sec) sec = parseDur(plan.getDuration(true));
            }
            if (!m && res && typeof res.getDistance === "function") m = parseDist(res.getDistance());
          } catch (e) { /* 个别线路类型缺字段，忽略后走兼底 */ }

          var est = false;
          if (!m) { m = straightMeters(stops[i], stops[i + 1]); est = true; }
          if (!sec) { sec = estimateSeconds(useMode, m); est = true; }
          legs.push({ mode: useMode, m: m, s: sec, est: est });
          totalM += m;
          totalS += sec;
          i++; progress(); next();
        }
      });
      route.search(a, b);
    }
    next();
  }

  function clearRoute(silent) {
    if (map) S.layers.forEach(function (l) { map.removeOverlay(l); });
    S.layers = [];
    S.route = null;
    $("#planBtn").disabled = true;
    if (!silent) $("#routeResult").innerHTML = "";
  }

  /* ---------------- 工具 ---------------- */
  function byMingName(n) {
    return S.places.filter(function (p) { return p.ming_name === n; })[0] || null;
  }
  function km(a, b) {
    var R = 6371, dLat = (b.lat - a.lat) * Math.PI / 180, dLng = (b.lng - a.lng) * Math.PI / 180;
    var la1 = a.lat * Math.PI / 180, la2 = b.lat * Math.PI / 180;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return (2 * R * Math.asin(Math.sqrt(h))).toFixed(2) + " km";
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function showNotice(msg) {
    var n = $("#mapNotice");
    n.textContent = msg;
    n.hidden = false;
  }

  /* ---------------- 对外 API（AI 导游用） ---------------- */
  DJJWL.findPlace = byMingName;
  DJJWL.select = select;
  DJJWL.navUrl = navUrl;
  DJJWL.getMap = function () { return map; };      // 供坐标核验等模块复用同一张地图
  DJJWL.planRoute = planRoute;
  DJJWL.planWalk = planRoute;   // 向后兼容旧调用名
  DJJWL.setRouteMode = function (m) {
    var sel = $("#routeMode");
    if (sel && m) sel.value = m;
    return sel ? sel.value : null;
  };
  DJJWL.getRouteMode = function () { var sel = $("#routeMode"); return sel ? sel.value : "walking"; };

  DJJWL.focusByName = function (name) {
    var p = byMingName(name);
    if (p) select(p.id, true);
    return p;
  };

  // 程序式应用行程（不依赖侧栏 DOM）
  DJJWL.applyRoute = function (stops, name, notes) {
    S.filter.q = "";
    setRoute(null, name, stops, notes || "");
    var sel = $("#mode");
    if (sel && sel.value !== "route") {
      sel.value = "route";
      $("#routePanel").hidden = false;
      renderRoutes();
    }
    return S.route;
  };

  // 周边检索（异步回呼）
  DJJWL.nearbySearch = function (p, keyword, cb) {
    if (!localSearch) return cb(null);
    localSearch.setSearchCompleteCallback(function (res) {
      var out = [];
      if (res && res.getCurrentNumPois) {
        for (var i = 0; i < res.getCurrentNumPois(); i++) {
          var poi = res.getPoi(i);
          out.push({ title: poi.title, address: poi.address || "" });
        }
      }
      cb(out);
    });
    localSearch.searchNearby(keyword, new BMapGL.Point(p.lng, p.lat), 1000);
  };

  DJJWL.stats = function () {
    return {
      places: S.places.length,
      poems: S.places.reduce(function (a, p) { return a + p.poem_count; }, 0),
      notes: S.places.filter(function (p) { return p.history_note; }).length,
      mapReady: DJJWL.mapState === "ready",
      mapState: DJJWL.mapState
    };
  };
})();
