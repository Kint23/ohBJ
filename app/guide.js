/* 明代北京城 AI 导游
 *
 * 设计：本地意图解析 + 语料驱动回答 + 真实地图工具调用。
 *   - 不依赖任何外部 LLM key，断网也能演示（回答文本全部由 places.json 的
 *     原文 / 诗词 / 今译 / 清代沿革拼装，因此每句都有出处）
 *   - "工具调用"是真的：步行路线规划、周边餐饮/厕所检索都走百度地图 JSAPI，
 *     并把调用轨迹显示在对话里（可展开），便于评审看到执行链路
 *   - 预留 LLM 接入点：见文末 AGENT.llmHook
 *
 * 依赖：window.DJJWL（由 app/app.js 暴露）
 */
(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var AG = { open: false, booted: false, lastPlace: null, log: [] };

  /* ---------- 地名别名：把今天口语的名字映射回明代原目 ---------- */
  var ALIASES = {
    "国子监": "太學石鼓", "孔庙": "太學石鼓", "石鼓": "太學石鼓", "国子监街": "太學石鼓",
    "文天祥": "文丞相祠", "文天祥祠": "文丞相祠", "府学胡同": "文丞相祠",
    "什刹海": "十刹海", "后海": "水關", "前海": "十刹海", "西海": "水關", "荷花市场": "十刹海",
    "火神庙": "火神廟", "火德真君庙": "火神廟",
    "隆福寺": "大隆福寺", "灯市口": "燈市", "灯市": "燈市",
    "于谦": "于少保祠", "于谦祠": "于少保祠",
    "东岳庙": "東嶽廟", "金鱼池": "金魚池",
    "法源寺": "憫忠寺", "悯忠寺": "憫忠寺",
    "报国寺": "報國寺", "天宁寺": "天寧寺", "天宁寺塔": "天寧寺",
    "白云观": "白雲觀", "卢沟桥": "盧溝橋", "马驹桥": "弘仁橋", "弘仁桥": "弘仁橋",
    "白塔寺": "白塔寺", "妙应寺": "白塔寺", "白塔": "白塔寺",
    "历代帝王庙": "帝王廟", "帝王庙": "帝王廟",
    "南堂": "天主堂", "宣武门教堂": "天主堂", "宣武门天主堂": "天主堂", "利玛窦": "天主堂",
    "万松老人塔": "萬松老人塔", "砖塔胡同": "萬松老人塔",
    "城隍庙": "城隍廟市", "都城隍庙": "城隍廟市",
    "高梁桥": "高梁橋", "高梁桥": "高梁橋",
    "万寿寺": "萬壽寺", "五塔寺": "真覺寺", "真觉寺": "真覺寺", "石刻艺术博物馆": "真覺寺",
    "慈寿寺": "慈壽寺", "玲珑塔": "慈壽寺", "八里庄塔": "慈壽寺",
    "香山": "香山寺", "香山寺": "香山寺", "碧云寺": "碧雲寺",
    "卧佛寺": "臥佛寺", "十方普觉寺": "臥佛寺", "植物园": "臥佛寺",
    "潭柘寺": "潭柘寺"
  };

  /* ---------- 类型同义词 → places.json 里的 tags ---------- */
  var TAG_SYN = {
    "寺庙": ["寺院", "佛塔", "坛庙"], "寺院": ["寺院"], "佛寺": ["寺院", "佛塔"],
    "道观": ["道观"], "庙": ["坛庙", "道观", "祠堂"], "塔": ["佛塔"],
    "教堂": ["教堂"], "园林": ["园林"], "公园": ["园林", "山林"],
    "水": ["河湖"], "湖": ["河湖"], "河": ["河湖", "桥梁"],
    "碑": ["碑刻", "石刻"], "书法": ["碑刻", "石刻"], "石刻": ["石刻"],
    "忠臣": ["忠烈", "祠堂"], "祠堂": ["祠堂"], "名人": ["祠堂", "忠烈"],
    "市集": ["市集"], "集市": ["市集"], "商圈": ["市集"],
    "山": ["山林"], "爬山": ["山林"], "桥": ["桥梁"],
    "西学": ["西学东渐"], "西洋": ["西学东渐"],
    "民俗": ["民俗", "岁时"], "节令": ["岁时"], "老北京": ["民俗", "岁时"],
    "学校": ["教育"], "古迹": []
  };

  var MOBILITY_LOW = /(少走|不想走|走不动|腿脚|老人|长辈|轮椅|带父母|轻松|别太长|短一点|半日)/;
  var DURATIONS = [
    { re: /(半[天日]|一上午|一下午|两三[个]?小时|几小时)/, hours: 4, label: "半日" },
    { re: /(一[天日]|1\s*天|整[天日]|全[天日])/, hours: 8, label: "1 日" },
    { re: /(两[天日]|2\s*天|二[天日])/, hours: 16, label: "2 日", itin: "d2" },
    { re: /(三[天日]|3\s*天)/, hours: 24, label: "3 日", itin: "d3" },
    { re: /(五[天日]|5\s*天|四[天日]|4\s*天)/, hours: 40, label: "5 日", itin: "d5" }
  ];

  /* ================= 启动 ================= */
  function boot() {
    if (AG.booted) return;
    AG.booted = true;

    bindUi();
    window.DJJWL.ready(function (api) {
      var st = api.stats();
      greet(st);
      // 地图是异步加载的：只在「确实失败」时提示，避免底图已好却误报
      if (!st.mapReady) {
        window.DJJWL.onMapResult(function (state) {
          if (state === "failed") greetMapPending();
        });
      }
    });
  }

  function bindUi() {
    var t = $("#gxToggle"), c = $("#gxClose"), f = $("#gxForm");
    if (!t) return;
    t.addEventListener("click", function () { toggle(true); });
    c.addEventListener("click", function () { toggle(false); });
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var v = $("#gxInput").value.trim();
      if (!v) return;
      $("#gxInput").value = "";
      ask(v);
    });
    quickChips(["半天想逛寺庙，怎么走？", "太學石鼓有什么典故？", "香山寺附近有厕所吗？",
      "三天怎么安排？", "金鱼池怎么去？", "一共有多少处古迹？"]);
  }

  function toggle(on) {
    AG.open = on;
    $("#gx").hidden = !on;
    $("#gxToggle").hidden = on;
    if (on) setTimeout(function () { $("#gxInput").focus(); }, 30);
  }

  /* ================= 对话渲染 ================= */
  function push(role, html) {
    var box = $("#gxMsg");
    var d = document.createElement("div");
    d.className = "gx-line " + role;
    d.innerHTML = html;
    box.appendChild(d);
    box.scrollTop = box.scrollHeight;
    return d;
  }

  function trace(steps) {
    AG.log.push(steps);
    return '<details class="gx-trace"><summary>执行链路（' + steps.length + ' 步）</summary><ol>' +
      steps.map(function (s) { return "<li>" + esc(s) + "</li>"; }).join("") + "</ol></details>";
  }

  function quickChips(list) {
    var box = $("#gxChips");
    box.innerHTML = "";
    list.slice(0, 6).forEach(function (q) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "gx-chip";
      b.textContent = q;
      b.onclick = function () { ask(q); };
      box.appendChild(b);
    });
  }

  function followups(list) {
    if (!list || !list.length) return;
    var html = '<div class="gx-follow">';
    list.forEach(function (q) {
      html += '<button type="button" class="gx-chip" data-follow="' + esc(q) + '">' + esc(q) + "</button>";
    });
    html += "</div>";
    var node = push("bot", html);
    Array.prototype.forEach.call(node.querySelectorAll("[data-follow]"), function (b) {
      b.onclick = function () { ask(b.getAttribute("data-follow")); };
    });
  }

  function greet(st) {
    push("bot",
      "<b>你好，我是明代北京城的导游。</b><br/>" +
      "我手里是《帝京景物略》（1635 年）拆出的 <b>" + st.places + " 处</b>古迹、" +
      st.poems + " 首诗，以及清人《京城古迹考》里 " + st.notes + " 条实地核访。<br/><br/>" +
      "可以问我：<br/>" +
      "· <b>规划路线</b>——「半天想逛寺庙怎么走」「三天怎么安排」<br/>" +
      "· <b>讲典故</b>——「太學石鼓有什么典故」<br/>" +
      "· <b>找出处</b>——原文、诗词、白话今译都直接取自语料<br/>" +
      "· <b>就地服务</b>——「香山寺附近有厕所吗」「金鱼池怎么去」" +
      trace([
        "载入 data/places.json（" + st.places + " 处 / " + st.poems + " 首）",
        "索引《帝京景物略》原文与诗词、清《京城古迹考》沿革",
        "绑定工具：百度地图步行路线规划 / 本地检索 / 导航 URI"
      ])
    );
  }

  function greetMapPending() {
    push("bot", "⚠️ 顺带一提：地图底图还没加载（多半是 `app/config.js` 的 AK 未填或不是<b>浏览器端</b> AK）。" +
      "文字问答不受影响；但「周边检索」「路线规划（步行/骑行/驾车/公交）」需要底图，填好 AK 刷新后就能用。");
  }

  /* ================= 意图识别 ================= */
  function ask(text) {
    push("user", esc(text));
    var q = text.trim();
    var api = window.DJJWL;

    if (!api || !api._loaded) {
      push("bot", "数据还在加载，请稍等一秒再问一次。");
      return;
    }

    var place = matchPlace(q);
    var tags = matchTags(q);
    var dur = matchDuration(q);
    var lowMobility = MOBILITY_LOW.test(q);

    // 1) 就近服务：餐饮 / 厕所 / 停车
    if (/(厕所|卫生间|洗手间|wc|方便|如厕)/i.test(q)) return svc(q, place, "公共厕所", "厕所");
    if (/(吃|餐|饭|小吃|食堂|美食|咖啡|tea|茶)/.test(q)) return svc(q, place, "餐厅", "餐饮");
    if (/(停车|车位)/.test(q)) return svc(q, place, "停车场", "停车场");

    // 2) 怎么去 / 导航
    if (/(怎么去|怎么走|导航|路线到|多远|位置|在哪|地址)/.test(q) && place) return where(place);

    // 3) 规划行程
    if (/(安排|规划|路线|行程|怎么玩|推荐|带我|一天|半天|两日|三日|几日|几天|一日游|要不要)/.test(q) ||
        tags.length || dur) {
      if (/(安排|规划|路线|行程|怎么玩|推荐|带我|游)/.test(q) || dur || tags.length) {
        return plan(q, tags, dur, lowMobility);
      }
    }

    // 3.5) 指定出行方式（例："改成公交怎么走""骑车去"）
    var modeHit = /(公交|地铁|巴士)/.test(q) ? "transit"
      : /(骑行|骑车|自行车)/.test(q) ? "riding"
        : /(驾车|开车|自驾|打车)/.test(q) ? "driving"
          : /(步行|走路|逗弯|溜达)/.test(q) ? "walking" : null;
    if (modeHit && window.DJJWL.setRouteMode) {
      window.DJJWL.setRouteMode(modeHit);
      push("bot", "出行方式已切到 <b>" +
        ({ walking: "步行", riding: "骑行", driving: "驾车", transit: "公交" }[modeHit]) +
        "</b>。点侧栏<b>「生成路线」</b>就能看到该方式下的真实里程与耗时；已排好的行程会自动重新规划。" +
        (modeHit === "transit" ? "<br/><span class='muted small'>提示：公交结果含线路与步行接驳，实际班次以现场为准。</span>" : ""));
      trace(["识别出行方式 = " + modeHit, "调用 DJJWL.setRouteMode('" + modeHit + "')",
        "下次点「生成路线」将使用 BMapGL." +
        ({ walking: "WalkingRoute", riding: "RidingRoute", driving: "DrivingRoute", transit: "TransitRoute" }[modeHit])]);
      followups(["生成路线看看", "这条线附近有厕所吗？"]);
      return;
    }

    // 4) 典故 / 原文 / 诗
    if (place) {
      if (/(诗|词|谁写|题咏|赋)/.test(q)) return poem(place);
      if (/(原文|记载|怎么记|书里|出处|依据)/.test(q)) return source(place);
      return intro(place);
    }

    // 5) 列举
    if (/(有哪些|列出|列表|多少处|几处|都是什么|全部|一共)/.test(q)) return list(tags);

    // 6) 寒暄 / 兜底
    if (/(你好|hi|hello|在吗|谢谢)/i.test(q)) {
      push("bot", "在的。可以直接问「半天想逛寺庙怎么走」或「太學石鼓有什么典故」。");
      return;
    }
    push("bot", "我没听准。<br/>试试这样问：<br/>· 「半天想逛寺庙，怎么走？」<br/>· 「法源寺有什么典故？」<br/>· 「三天怎么安排？」");
    followups(["半天想逛寺庙，怎么走？", "三天怎么安排？", "法源寺有什么典故？"]);
  }

  /* ================= 意图：解析工具 ================= */
  function matchPlace(q) {
    var api = window.DJJWL, best = null, bestLen = 0;
    // 1) 明代原名 / 简体名 / 今名
    api.places.forEach(function (p) {
      [p.ming_name, p.ming_name_simp, p.modern_name].forEach(function (n) {
        if (n && q.indexOf(n) >= 0 && n.length > bestLen) { best = p; bestLen = n.length; }
      });
    });
    // 2) 别名（今名 → 明名）
    Object.keys(ALIASES).forEach(function (k) {
      if (q.indexOf(k) >= 0 && k.length > bestLen) {
        var p = api.findPlace(ALIASES[k]);
        if (p) { best = p; bestLen = k.length; }
      }
    });
    // 3) 部分匹配（如「东岳」→東嶽廟）
    if (!best) {
      api.places.forEach(function (p) {
        var s = p.ming_name_simp;
        if (q.length >= 2 && s.length >= 2) {
          var head = s.replace(/[寺廟庙观塔桥池堂宫院]$/, "");
          if (head.length >= 2 && q.indexOf(head) >= 0 && head.length > bestLen) { best = p; bestLen = head.length; }
        }
      });
    }
    if (best) AG.lastPlace = best;
    return best;
  }

  function matchTags(q) {
    var out = [];
    Object.keys(TAG_SYN).forEach(function (k) {
      if (q.indexOf(k) >= 0) TAG_SYN[k].forEach(function (t) { if (out.indexOf(t) < 0) out.push(t); });
    });
    return out;
  }

  function matchDuration(q) {
    for (var i = 0; i < DURATIONS.length; i++) if (DURATIONS[i].re.test(q)) return DURATIONS[i];
    return null;
  }

  /* ================= 回答：介绍 / 原文 / 诗 / 定位 ================= */
  function intro(p) {
    var steps = ["检索地名：「" + p.ming_name_simp + "」→ 命中《帝京景物略》" + p.volume + p.volume_name,
      "读取摘录 " + p.text_len + " 字 / 关联诗 " + p.poem_count + " 首"];
    var cite = "《帝京景物略》" + p.volume + "·" + p.ming_name;
    var html = "<b>" + p.ming_name + "</b>（今 " + p.ming_name_simp + " 对应 <b>" + p.modern_name + "</b>）<br/>" +
      p.address + "<br/><br/>" +
      (p.vernacular ? "<div class='gx-quote'>" + p.vernacular + "</div>" : "") +
      "<div class='gx-cite'>依据：" + cite + "（白话为 AI 辅助译文）</div>";
    if (p.history_note) {
      html += "<details class='gx-trace'><summary>清人 300 年后去看它，写了什么？</summary>" +
        "<div class='gx-quote'>" + esc(p.history_note.slice(0, 260)) + "…</div>" +
        "<div class='gx-cite'>" + p.history_source + "</div></details>";
      steps.push("附加《京城古迹考》沿革" + p.history_note.length + "字");
    }
    html += trace(steps);
    push("bot", html);
    window.DJJWL.focusByName(p.ming_name);
    followups(["《" + p.ming_name + "》原文怎么写的？", p.ming_name + "怎么去？",
      "附近有厕所吗？"]);
  }

  function source(p) {
    push("bot", "<b>" + p.ming_name + "</b> 的原文（" + p.excerpt.length + " 字摘录，共 " + p.text_len + " 字）" +
      "<div class='gx-quote gx-trad'>" + esc(p.excerpt) + "…</div>" +
      "<div class='gx-cite'>《帝京景物略》" + p.volume + "·" + p.ming_name + "</div>" +
      trace(["读取 data/places.json → places[" + p.id + "].excerpt（繁体原文，未作改写）"]));
    followups([p.ming_name + "有什么典故？", "谁为" + p.ming_name + "写过诗？"]);
  }

  function poem(p) {
    if (!p.poems.length) {
      push("bot", "《帝京景物略》" + p.volume + "·" + p.ming_name + " 这一目没有收录诗（全书共录诗 1346 首）。");
      return;
    }
    var list = p.poems.slice(0, 3).map(function (x) {
      return "<details class='gx-trace'><summary>" +
        esc([x.dynasty, x.origin, x.author].filter(Boolean).join("·")) + "《" + esc(x.title) + "》</summary>" +
        "<div class='gx-quote'>" + esc(x.text) + "</div></details>";
    }).join("");
    push("bot", "<b>" + p.ming_name + "</b> 关联 " + p.poem_count + " 首诗，先看 3 首：" + list +
      "<div class='gx-cite'>均自《帝京景物略》" + p.volume + "·" + p.ming_name + " 原书著录</div>" +
      trace(["places[" + p.id + "].poems：共 " + p.poem_count + " 首，展示前 3 首全文"]));
    followups([p.ming_name + "原文怎么写的？", p.ming_name + "怎么去？"]);
  }

  function where(p) {
    push("bot", "<b>" + p.ming_name + "</b> → <b>" + p.modern_name + "</b><br/>" + p.address + "<br/>" +
      (p.coord_precision === "area"
        ? "<span class='gx-warn'>这是一处范围地名（地点已湮灭或占地较广），坐标只给大致范围。</span><br/>"
        : "") +
      "<div class='gx-nav'>" +
      '<a href="' + window.DJJWL.navUrl(p, "walking") + '" target="_blank" rel="noopener">步行导航</a>' +
      '<a href="' + window.DJJWL.navUrl(p, "transit") + '" target="_blank" rel="noopener">公交导航</a>' +
      '<a href="' + window.DJJWL.navUrl(p, "riding") + '" target="_blank" rel="noopener">骑行导航</a>' +
      '<a href="' + window.DJJWL.navUrl(p, "driving") + '" target="_blank" rel="noopener">驾车导航</a>' +
      "</div>" +
      trace(["读取坐标（BD-09）：" + p.lng + ", " + p.lat + "，来源 " +
        (p.coord_source === "verified-by-ak" ? "百度 WebAPI 核验" : "人工估计（待核验）"),
        "生成百度地图 URI 导航链接（调起百度地图 App / 网页）"]));
    window.DJJWL.focusByName(p.ming_name);
    followups([p.ming_name + "附近有厕所吗？", p.ming_name + "附近能吃饭吗？"]);
  }

  function svc(q, place, keyword, label) {
    var p = place || AG.lastPlace || window.DJJWL.findPlace("白塔寺");
    if (!p) { push("bot", "先告诉我哪一处古迹附近，比如「香山寺附近有厕所吗」。"); return; }
    if (!window.DJJWL.hasMap()) {
      push("bot", "底图未加载，暂时不能做周边检索。可点这里在百度地图里看：<br/>" +
        '<div class="gx-nav"><a href="https://map.baidu.com/search/' + encodeURIComponent(keyword) +
        "/@" + p.lng + "," + p.lat + ',17z" target="_blank" rel="noopener">查看' + p.ming_name + "周边" + label + "</a></div>");
      return;
    }
    var node = push("bot", "正在以 <b>" + p.ming_name + "</b>（" + p.modern_name + "）为中心检索 1 公里内的" + label + "…");
    window.DJJWL.nearbySearch(p, keyword, function (list) {
      var body;
      if (!list || !list.length) {
        body = "1 公里内没有检索到" + label + "。";
      } else {
        body = "<b>" + p.ming_name + "</b> 周边 1 公里内的" + label + "：<ol class='gx-oli'>" +
          list.slice(0, 5).map(function (x) {
            return "<li>" + esc(x.title) + (x.address ? " <span class='gx-dim'>" + esc(x.address) + "</span>" : "") + "</li>";
          }).join("") + "</ol>";
      }
      node.innerHTML = body + trace([
        "百度地图 LocalSearch.searchNearby('" + keyword + "', " + p.lng + "," + p.lat + ", 1000m)",
        "返回 " + (list ? list.length : 0) + " 条 POI"
      ]);
      $("#gxMsg").scrollTop = $("#gxMsg").scrollHeight;
    });
    window.DJJWL.focusByName(p.ming_name);
  }

  function list(tags) {
    var arr = window.DJJWL.places.filter(function (p) {
      if (!tags.length) return true;
      return tags.some(function (t) { return p.tags.indexOf(t) >= 0; });
    });
    var html = "共 <b>" + arr.length + "</b> 处" +
      (tags.length ? "（类型：" + tags.join("、") + "）" : "（本版收录总数）") + "：<ul class='gx-ul'>" +
      arr.map(function (p) {
        return "<li><b>" + p.ming_name + "</b> → " + p.modern_name +
          " <span class='gx-dim'>" + p.volume + "·诗 " + p.poem_count + " 首</span></li>";
      }).join("") + "</ul>";
    push("bot", html + trace(["按 tags=" + JSON.stringify(tags) + " 过滤 places.json，命中 " + arr.length + " 条"]));
    followups(["这些怎么安排成一天？", "帮我按步行圈分组"]);
  }

  /* ================= 回答：行程规划 ================= */
  function expandItinerary(it) {
    var stops = [];
    it.plan.forEach(function (day) {
      if (day.reuse) {
        var parts = day.reuse.split(".");
        var src = window.DJJWL.routes.itineraries.filter(function (x) { return x.id === parts[0]; })[0];
        if (!src) return;
        var sd = src.plan.filter(function (d) { return "day" + d.day === parts[1]; })[0];
        if (sd && sd.stops) sd.stops.forEach(function (s) { stops.push({ ming_name: s.ming_name, stay: s.stay }); });
      } else if (day.stops) {
        day.stops.forEach(function (s) { stops.push({ ming_name: s.ming_name, stay: s.stay }); });
      }
    });
    return { name: it.name, stops: stops, est_km: it.est_km, notes: it.notes || "" };
  }

  function scoreCluster(c, tags, lowMobility) {
    var s = tags.length ? 0 : 1;
    c.stops.forEach(function (n) {
      var p = window.DJJWL.findPlace(n);
      if (!p) return;
      tags.forEach(function (t) { if (p.tags.indexOf(t) >= 0) s += 2; });
    });
    if (lowMobility) s -= c.est_km * 0.5;
    return s;
  }

  function plan(q, tags, dur, lowMobility) {
    var routes = window.DJJWL.routes;

    // 多日档：直接用策展好的行程
    if (dur && dur.itin) {
      var it = routes.itineraries.filter(function (x) { return x.id === dur.itin; })[0];
      if (it) {
        var ex = expandItinerary(it);
        window.DJJWL.applyRoute(ex.stops, ex.name, ex.notes);
        var byDay = it.plan.map(function (day) {
          var names = [];
          if (day.reuse) {
            var parts = day.reuse.split(".");
            var src = routes.itineraries.filter(function (x) { return x.id === parts[0]; })[0];
            var sd = src && src.plan.filter(function (d) { return "day" + d.day === parts[1]; })[0];
            names = sd ? sd.stops.map(function (s) { return s.ming_name; }) : [];
          } else if (day.stops) {
            names = day.stops.map(function (s) { return s.ming_name; });
          }
          return "<li><b>" + day.title + "</b>（直线约 " + day.est_km + " km）：" + names.join(" → ") + "</li>";
        }).join("");
        push("bot", "按 <b>" + dur.label + "</b> 给你排好了，已在地图上标出编号与连线：<ol class='gx-oli'>" +
          byDay + "</ol>" +
          (ex.notes ? "<div class='gx-quote'>" + esc(ex.notes) + "</div>" : "") +
          "点侧栏的<b>「生成路线」</b>，会用百度地图路线规划算出每段真实里程与耗时（出行方式可在侧栏切换）。" +
          trace(["匹配策展行程 " + it.id + "（" + it.stops_total + " 处 / 直线 " + it.est_km + " km）",
            "跨天展开 reuse 引用 → " + ex.stops.length + " 个站点",
            "调用 DJJWL.applyRoute()：写入地图编号、虚线连线、行程表"]));
        followups(["第 1 天附近能吃饭吗？", "换一个更轻松的方案"]);
        return;
      }
    }

    // 半日 / 1 日 / 多日：交给「智能排线」给出 3 个取舍不同的方案
    var label = dur ? dur.label : "半日";
    var budget = (dur ? dur.hours : 4) * 60;
    var mode = (window.DJJWL.getRouteMode && window.DJJWL.getRouteMode()) || "walking";
    var options = window.DJJWL_PLANNER
      ? window.DJJWL_PLANNER.build3({ budget: budget, mode: mode, tags: tags })
      : [];

    if (lowMobility) {   // 少走路：按交通耗时升序，把最省力的排前面
      options.sort(function (a, b) { return a.travel - b.travel; });
    }
    if (!options.length) {
      push("bot", "没找到合适的组合。换个兴趣标签、加长时间，或把出行方式改成骑行 / 驾车试试。");
      return;
    }

    var items = options.map(function (o, i) {
      return "<li><b>方案 " + "ABC"[i] + " · " + o.name + "</b>（匹配 " + o.score + "%）<br/>" +
        o.stops.map(function (p, k) { return (k + 1) + ". " + p.ming_name; }).join(" → ") +
        "<br/><span class='muted small'>" + o.stops.length + " 处 ｜ 直线 " +
        (o.straight / 1000).toFixed(1) + " km ｜ 交通约 " + Math.round(o.travel) +
        " 分 ｜ 参观 " + Math.round(o.visit) + " 分 ｜ 合计 ≈ " + Math.round(o.total) + " 分</span></li>";
    }).join("");

    var node = push("bot",
      "按 <b>" + label + "</b>" + (tags.length ? "、主题「" + tags.join("、") + "」" : "") +
      (lowMobility ? "、<b>尽量少走路</b>" : "") + "，给你 3 个取舍不同的方案：" +
      "<ol class='gx-oli'>" + items + "</ol>" +
      "点下面的按钮把它画到地图上，再到「行程」点<b>『生成路线』</b>得真实里程。<br/>" +
      "<div class='gx-chips'>" + options.map(function (o, i) {
        return "<button class='gx-chip' data-opt='" + i + "'>选用方案 " + "ABC"[i] + "</button>";
      }).join("") + "</div>" +
      trace([
        "解析：时长=" + label + "（" + budget + " 分），类型=" + (tags.join("/") || "不限") +
        "，出行方式=" + mode + "，少走路=" + (lowMobility ? "是" : "否"),
        "调用 DJJWL_PLANNER.build3()：最近邻 / 标签优先 / 内容优先 三种策略各造线，按签名去重取 3 条",
        "候选池：" + ((window.DJJWL.places || []).length) + " 处（含兴趣过滤与放宽回退）"
      ]));

    Array.prototype.forEach.call(node.querySelectorAll("[data-opt]"), function (b) {
      b.onclick = function () {
        var o = options[Number(b.getAttribute("data-opt"))];
        window.DJJWL.applyRoute(o.stops.map(function (p) { return { ming_name: p.ming_name }; }),
          o.name + "（AI 导游推荐）",
          o.stops.length + " 处 · 直线 " + (o.straight / 1000).toFixed(1) + " km");
        push("bot", "已把<b>方案 " + o.name + "</b>画到地图上（" + o.stops.length + " 处，直线约 " +
          (o.straight / 1000).toFixed(1) + " km）。到侧栏「行程」点<b>『生成路线』</b>就能看到每段真实里程与耗时。");
      };
    });
    followups(["这条线附近有厕所吗？", "改成公交怎么走？"]);
  }

  /* ================= 工具 ================= */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }

  /* 预留：接入大模型 / 百度地图 MCP 时的替换点。
   * 约定：llmHook(text, ctx) 返回 Promise<string|null>；
   * 返回 null 则回落到本地规则引擎。上下文 ctx 含 places / routes / stats，
   * 可交给模型做函数调用，再由本文件的 plan()/intro()/where() 执行。 */
  var AGENT = {
    llmHook: null,
    aliasTable: ALIASES,
    tagSynonyms: TAG_SYN
  };
  window.DJJWLGuide = AGENT;

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
