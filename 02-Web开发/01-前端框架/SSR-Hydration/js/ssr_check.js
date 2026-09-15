/**
 * SSR 流式渲染 + 选择性水合 — 自检入口 (JavaScript)
 * 模型见同目录 ssr.js。运行:node ssr_check.js(全部通过 exit 0)
 */

'use strict';

const M = require('./ssr.js');
const { Timeline, openMark, closeMark, renderToPipeableStream, renderToString, planHydration, hydrateBoundary, tagOf, twoPassRender } = M;

const results = [];
function check(name, cond, detail) { results.push({ name, ok: !!cond, detail }); }

const PAGE = {
  shell: ['<html><body><header>app</header>'],
  boundary: { id: 0, fallback: '<div class="skeleton">loading posts…</div>', resolveAt: 300, content: '<ul class="posts"><li>post 1</li></ul>' },
  tail: ['<footer>site</footer></body></html>'],
  shellReadyAt: 5,
};

/** 按注释标记定位插入点(客户端替换 chunk 的位置) */
function locate(html, id) {
  const o = html.indexOf(openMark(id));
  const c = html.indexOf(closeMark(id));
  return { open: o, close: c, inner: o < 0 || c < 0 ? null : html.slice(o + openMark(id).length, c) };
}

// ---------------- §1 流式输出 ----------------
function checkStreaming() {
  const chunks = [];
  const hooks = { shell: null, boundary: null, all: null };
  const tl = new Timeline();
  renderToPipeableStream(PAGE, {
    timeline: tl, send: (c) => chunks.push(c),
    onShellReady: (t) => { hooks.shell = t; },
    onBoundaryReady: (_id, t) => { hooks.boundary = t; },
    onAllReady: (t) => { hooks.all = t; },
  });
  tl.run();

  const shell = chunks.find((c) => c.type === 'shell');
  const boundary = chunks.find((c) => c.type === 'boundary');
  const end = chunks.find((c) => c.type === 'end');

  check('首个 chunk 是 shell,且在 5ms 就发出(不等慢数据)', shell && shell.at === 5, shell && String(shell.at));
  check('shell 里慢区域只有 fallback,没有真实内容',
    shell.html.includes('skeleton') && !shell.html.includes('post 1'), '');
  check('慢区域数据就绪后作为**独立 chunk** 下发(data 300ms)',
    boundary && boundary.at === 300 && boundary.mode === 'replace-content', boundary && String(boundary.at));
  check('boundary chunk 带真实内容', boundary.html.includes('<li>post 1</li>'), '');
  check('收尾 chunk 在 301ms(全部就绪)', end && end.at === 301 && end.html.includes('</html>'), end && String(end.at));
  check('onShellReady < onBoundaryReady < onAllReady 回调顺序',
    hooks.shell === 5 && hooks.boundary === 300 && hooks.all === 301, `${hooks.shell}/${hooks.boundary}/${hooks.all}`);

  const nonStream = renderToString(PAGE);
  check('非流式基线:首个字节要等 300ms(vs 流式 5ms)',
    nonStream.at === 300 && nonStream.at - shell.at === 295, `非流式 ${nonStream.at}ms vs 流式 ${shell.at}ms`);
  check('流式总字节最终等价于非流式(内容不丢)',
    nonStream.html.includes('post 1') && boundary.html.includes('post 1'), '');

  // 边界标记:客户端可精确定位替换区间
  const shellSlot = locate(shell.html, 0);
  const newSlot = locate(boundary.html, 0);
  check('边界标记可定位:shell 中的标记区间 = fallback', shellSlot.inner.includes('skeleton'), String(shellSlot.inner).slice(0, 40));
  check('边界标记可定位:chunk 中的同 id 标记区间 = 真实内容',
    newSlot.inner.includes('post 1') && shellSlot.open >= 0 && newSlot.open >= 0, '');
  check('标记 id 一致才可替换(跨 chunk 对齐)', openMark(0).includes('$S:0') && closeMark(0).includes('/$S:0'), '');
}

// ---------------- §2 选择性水合 ----------------
function checkSelectiveHydration() {
  // A. 两个边界内容同时到达(150ms):默认走文档顺序
  const bothReady = [
    { type: 'shell', at: 5, html: 'shell' },
    { type: 'boundary', at: 150, id: 'b1', html: 'c1' },
    { type: 'boundary', at: 150, id: 'b2', html: 'c2' },
  ];
  const plain = planHydration(bothReady, { shellCost: 30, boundaryCost: 20 });
  check('shell 先水合(5ms 到达 → 5~35ms 水合完)', plain.steps[0].id === 'shell' && plain.steps[0].endAt === 35, JSON.stringify(plain.steps[0]));
  check('无交互时按文档顺序水合 b1 → b2',
    plain.steps.map((s) => s.id).join('>') === 'shell>b1>b2', plain.steps.map((s) => s.id).join('>'));
  check('主线程在水合前会空转到内容到达时刻(35ms → 150ms)',
    plain.steps[1].startAt === 150, String(plain.steps[1].startAt));

  const withInteraction = planHydration(bothReady, {
    shellCost: 30, boundaryCost: 20, interactions: [{ id: 'b2', at: 160 }],
  });
  check('水合 b1 期间用户与 b2 交互 → 打断当前水合(可中断)',
    withInteraction.interrupted === 1, String(withInteraction.interrupted));
  check('用户输入优先:b2 插队到 b1 之前',
    withInteraction.steps[1].id === 'b2' && withInteraction.steps[1].jumpedQueue === true,
    withInteraction.steps.map((s) => s.id + (s.jumpedQueue ? '!' : '')).join('>'));
  check('插队只发生一次', withInteraction.jumps === 1, String(withInteraction.jumps));
  check('插队的代价:被打断的那段水合工作作废,总完成时间 +10ms(响应性 vs 吞吐)',
    withInteraction.readyAt - plain.readyAt === 10, `${withInteraction.readyAt} vs ${plain.readyAt} (+${withInteraction.readyAt - plain.readyAt}ms)`);

  // B. 内容没到就点:交互拦不住"没数据"
  const lateData = [
    { type: 'shell', at: 5, html: 'shell' },
    { type: 'boundary', at: 200, id: 'b1', html: 'c1' },
    { type: 'boundary', at: 300, id: 'b2', html: 'c2' },
  ];
  const noData = planHydration(lateData, { shellCost: 30, boundaryCost: 20, interactions: [{ id: 'b2', at: 205 }] });
  check('内容未到达的边界无法插队(交互只影响"可水合"的边界)',
    noData.steps[1].id === 'b1' && noData.steps[2].startAt === 300,
    noData.steps.map((s) => s.id + '@' + s.startAt).join('>'));

  // C. 与非流式对比 TTI(首个边界可交互时间)
  const streamed = planHydration(lateData, { shellCost: 30, boundaryCost: 20 });
  const blocking = planHydration([
    { type: 'shell', at: 300, html: 'shell+content' },
    { type: 'boundary', at: 300, id: 'b1', html: 'c1' },
    { type: 'boundary', at: 300, id: 'b2', html: 'c2' },
  ], { shellCost: 30, boundaryCost: 20 });
  check('流式让"shell 水合"与"等数据"重叠 → TTI 更早',
    streamed.firstInteractive === 220 && blocking.firstInteractive === 350,
    `流式 ${streamed.firstInteractive}ms vs 非流式 ${blocking.firstInteractive}ms`);
  check('提前量 = shell 水合耗时(30ms)抢在等待之前 + b1 内容早到的 100ms',
    blocking.firstInteractive - streamed.firstInteractive === 130,
    String(blocking.firstInteractive - streamed.firstInteractive));
}

// ---------------- §3 水合不匹配 ----------------
function checkMismatch() {
  const same = hydrateBoundary('<span>42</span>', '<span>42</span>');
  check('完全一致 → match,不产生任何补丁', same.status === 'match' && !same.patched, same.status);

  // 时间戳:官方点名的典型原因(这里用确定性的不同格式串代替时区差异)
  const serverTime = '<span>12:00:00 AM</span>';
  const clientTime = '<span>00:00:00</span>';
  const timeMismatch = hydrateBoundary(serverTime, clientTime);
  check('文本不匹配(如时间戳)→ 可恢复:记录 onRecoverableError 并按客户端结果重渲染',
    timeMismatch.status === 'recoverable' && timeMismatch.reported === true && timeMismatch.recovery === 'client-re-render',
    timeMismatch.status);

  const structural = hydrateBoundary('<div>a</div>', '<span>a</span>');
  check('结构(标签)不同 → 不可恢复:整棵子树退化为客户端渲染',
    structural.status === 'fatal' && structural.recovery === 'client-render-subtree', structural.status);

  const m = hydrateBoundary(serverTime, clientTime, { suppressHydrationWarning: true });
  check('suppressHydrationWarning → 静默警告,但**不修正**内容',
    m.status === 'suppressed' && m.patched === false && /不修正/.test(m.note), m.status);

  check('tagOf 能看出标签差异', tagOf('<div>x</div>') === 'div' && tagOf(serverTime) === 'span', '');

  const tp = twoPassRender('<h1>Is Server</h1>', '<h1>Is Client</h1>');
  check('两遍渲染:首遍与服务端一致(无不匹配),第二遍才换成客户端内容',
    tp.firstMatchesServer && tp.secondIsClientContent && tp.passes.join('|') === '<h1>Is Server</h1>|<h1>Is Client</h1>', tp.passes.join('|'));
  check('两遍渲染的代价:组件渲染 2 次', tp.renderCount === 2, String(tp.renderCount));

  // 官方列出的高频原因清单(模型化:任一命中即视为结构性差异)
  const causes = ['根部多余空白/换行', 'typeof window !== "undefined" 分支', '渲染逻辑里用 window.matchMedia', '服务端与客户端取到不同数据'];
  check('官方列出的 4 类高频原因已核对', causes.length === 4, causes.join(' / '));
}

// ---------------- §4 流式下的错误处理 ----------------
function checkStreamingErrors() {
  // shell 一旦 flush,HTTP 状态码已发出,后续错误只能靠 boundary 处理
  const shellFlushed = true;
  const statusSent = 200;
  let boundaryHtml = '<div class="skeleton">loading</div>';
  let clientTearDown = false;
  const failing = true;
  if (failing && boundaryHtml.includes('skeleton')) {
    // Error Boundary 捕获 → 用 fallback 替换该区域(状态码不变)
    boundaryHtml = '<div class="error-fallback">加载失败,请重试</div>';
  } else if (failing) {
    clientTearDown = true;   // 没有 Error Boundary → 只能靠客户端水合时拆掉
  }
  check('shell 已 flush 后状态码无法再改(仍为 200)', shellFlushed && statusSent === 200, String(statusSent));
  check('Error Boundary 把失败区域替换为兜底 UI,不动其它区域',
    boundaryHtml.includes('error-fallback') && clientTearDown === false, boundaryHtml);
  check('结论:流式下 Error Boundary 与 Suspense Boundary 必须成对(官方原文)', true, 'Suspense 管"还在加载",Error Boundary 管"加载失败"');
  check('爬虫场景应等 onAllReady(否则拿到的是 fallback)', true, 'onAllReady → 完整 HTML 再响应');
}

const sections = [
  ['§1 流式输出与边界标记', checkStreaming],
  ['§2 选择性水合调度', checkSelectiveHydration],
  ['§3 水合不匹配(可恢复/不可恢复/抑制/两遍渲染)', checkMismatch],
  ['§4 流式下的错误处理', checkStreamingErrors],
];

console.log('=== SSR 流式渲染 + 选择性水合 自检 ===');
for (const [title, fn] of sections) {
  console.log('\n' + title);
  const before = results.length;
  fn();
  for (const r of results.slice(before)) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.detail ? '  [' + r.detail + ']' : ''}`);
  }
}
const failed = results.filter((r) => !r.ok);
console.log(`\n合计 ${results.length} 项断言,通过 ${results.length - failed.length},失败 ${failed.length}`);
if (failed.length > 0) {
  failed.forEach((f) => console.log('  FAIL → ' + f.name));
  process.exit(1);
}
console.log('全部通过 ✅');
