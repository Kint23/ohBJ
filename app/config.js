/* 百度地图 JSAPI GL 配置
 *
 * 1) 到 https://lbsyun.baidu.com/apiconsole/key 创建「浏览器端」AK
 * 2) 在应用设置里把 Referer 白名单填上你的域名
 *    - 本机调试填 localhost
 *    - 上线填真实域名（如 https://your-name.github.io）
 * 3) 把 AK 填到下面（浏览器端 AK 本来就是公开的，靠 Referer 白名单保护；
 *    服务端 AK 千万不要写在这里，也不要提交到仓库）
 */
window.APP_CONFIG = {
  // 浏览器端 AK（浏览器端 AK 明文写在网页里是设计使然，靠 Referer 白名单保护）
  ak: "ApBmLRG5kTBLLcz6LOq7WSgmh2D8vkFG",

  // 初始视野：北京城中轴
  center: [116.397, 39.909],
  zoom: 12,

  // 演示用默认主题
  title: "帝京寻踪 · 明代北京古今地图"
};

/* ---- JSAPI 加载握手 ----
 * 本文件在 <script src="https://api.map.baidu.com/api?...&callback=__bmapReady"> 之前执行，
 * 因此这里先把回调挂到全局，等地图 API 就绪后再触发 app.js 的启动函数。
 */
(function () {
  var queue = [];
  window.__bmapReadyFired = false;

  // 供 JSAPI 的 callback 参数调用
  window.__bmapReady = function () {
    window.__bmapReadyFired = true;
    var q = queue; queue = [];
    for (var i = 0; i < q.length; i++) { try { q[i](); } catch (e) { console.error(e); } }
  };

  // 供 app.js 注册：API 已就绪则立即执行，否则排队
  window.onBMapReady = function (fn) {
    if (window.__bmapReadyFired && window.BMapGL) fn();
    else queue.push(fn);
  };
})();
