/**
 * SSR 流式渲染 + 选择性水合 (JavaScript)
 *   服务端 renderToPipeableStream 语义 → 客户端 hydrateRoot 语义 → 水合不匹配处理
 *
 * 权威来源(实际读过):
 *   - https://react.dev/reference/react-dom/server/renderToReadableStream — onShellReady / allReady、
 *     流式下发 Suspense 边界、客户端 hydrateRoot(document, <App assetMap={...}/>) 必须与服务端同参
 *   - https://react.dev/reference/react-dom/client/hydrateRoot — 水合不匹配的常见原因、onRecoverableError、
 *     suppressHydrationWarning(仅一层、不修文本)、两遍渲染(isClient + useEffect)
 *   - https://react.dev/blog/2022/03/29/react-v18 — 新的流式渲染 API(renderToPipeableStream / renderToReadableStream)、
 *     选择性水合(用户点击未水合区域 → React 优先水合它)
 *   - https://www.patterns.dev/posts/streaming-ssr — 边界注释标记($RC 指令)、水合"分块进行 + 用户输入优先"、
 *     流式下 Error Boundary 与 Suspense Boundary 必须成对、onAllReady 给爬虫
 *
 * 模型说明:DOM 用可序列化对象、时间用虚拟时间线,保证可复现;不改动任何真实 DOM。
 * 运行入口见同目录 ssr_check.js
 */

'use strict';

// ==================== 虚拟时间线 ====================

class Timeline {
  constructor() { this.now = 0; this.events = []; }
  at(t, fn) { this.events.push({ t, fn }); return this; }
  run() {
    // 同刻事件按注册顺序执行(与 Node 事件循环一致)
    const sorted = [...this.events].sort((a, b) => a.t - b.t || a.seq - b.seq);
    sorted.forEach((e, i) => { e.seq = e.seq ?? i; });
    for (const e of sorted) { this.now = e.t; e.fn(e.t); }
    return this;
  }
}

// ==================== §1 服务端:流式渲染 ====================

/** 一个 Suspense 边界的标记格式:<!--$S:id-->…<!--/$S:id-->(React 内部同款注释标记) */
const openMark = (id) => `<!--$S:${id}-->`;
const closeMark = (id) => `<!--/$S:${id}-->`;

/**
 * 等价于 renderToPipeableStream(<App/>, { onShellReady, onAllReady }):
 *   shell 先 flush(慢区域先放 fallback + 边界标记),各边界数据就绪后作为独立 chunk 追加下发。
 */
function renderToPipeableStream(page, opts) {
  const tl = opts.timeline;
  const send = opts.send;
  const state = { shellAt: null, allReadyAt: null, chunks: [] };

  // shell:慢区域此时只有 fallback(这就是"更早可见"的来源)
  tl.at(page.shellReadyAt, (t) => {
    state.shellAt = t;
    const placeholder = openMark(page.boundary.id) + page.boundary.fallback + closeMark(page.boundary.id);
    const chunk = { type: 'shell', at: t, html: page.shell.join('') + placeholder };
    state.chunks.push(chunk);
    send(chunk);
    if (opts.onShellReady) opts.onShellReady(t);
  });

  // 边界数据就绪 → 单独一个 chunk 带"替换指令"(真实 React 用 $RC 标记 body 内联脚本)
  tl.at(page.boundary.resolveAt, (t) => {
    const chunk = {
      type: 'boundary', at: t, id: page.boundary.id, mode: 'replace-content',
      html: openMark(page.boundary.id) + page.boundary.content + closeMark(page.boundary.id),
    };
    state.chunks.push(chunk);
    send(chunk);
    if (opts.onBoundaryReady) opts.onBoundaryReady(page.boundary.id, t);
  });

  // 全部就绪 + 收尾(爬虫场景要等 allReady)
  tl.at(page.boundary.resolveAt + 1, (t) => {
    state.allReadyAt = t;
    const chunk = { type: 'end', at: t, html: page.tail.join('') };
    state.chunks.push(chunk);
    send(chunk);
    if (opts.onAllReady) opts.onAllReady(t);
  });

  return state;
}

/** 非流式基线:renderToString —— 必须等所有数据就绪才能吐出第一个字节 */
function renderToString(page) {
  return {
    type: 'shell', at: page.boundary.resolveAt,
    html: page.shell.join('') + page.boundary.content + page.tail.join(''),
  };
}

// ==================== §2 客户端:选择性水合 ====================

/**
 * 水合调度:主线程串行 + 可中断。
 *   - shell 先水合(它是全页可交互的前提)
 *   - 只有**内容已到达**的边界才能水合;都不可用时主线程空转到最近一个到达时刻
 *   - 用户交互优先:与之交互的边界插队到队首;若当前正在水合的边界被交互打断则让出(可中断)
 */
function planHydration(chunks, opts = {}) {
  const EPS = 1e-9;
  const shellCost = opts.shellCost ?? 30;
  const boundaryCost = opts.boundaryCost ?? 20;
  const interactions = (opts.interactions || []).map((i) => ({ ...i, handled: false }));

  const shell = chunks.find((c) => c.type === 'shell');
  const arrival = {};
  for (const c of chunks) if (c.type === 'boundary') arrival[c.id] = c.at;
  const order = Object.keys(arrival);

  const remaining = new Set(order);
  const steps = [];
  let now = shell.at;                       // 收到 shell 那一刻起才有水合的可能
  let shellHydrated = false;
  let firstInteractive = null;
  let interrupted = 0;

  while (!shellHydrated || remaining.size > 0) {
    if (!shellHydrated) {                   // 阶段一:水合 shell
      const endAt = now + shellCost;
      const preempt = interactions.find((i) => !i.handled && remaining.has(i.id) && i.at > now && i.at < endAt && arrival[i.id] <= endAt);
      if (preempt) { interrupted++; now = preempt.at; continue; }
      steps.push({ id: 'shell', startAt: now, endAt, jumpedQueue: false });
      now = endAt;
      shellHydrated = true;
      continue;
    }
    if (remaining.size === 0) break;

    const available = order.filter((id) => remaining.has(id) && arrival[id] <= now + EPS);
    if (available.length === 0) {           // 没有可水合内容 → 主线程空转到最近一个到达时刻
      now = Math.min(...order.filter((id) => remaining.has(id)).map((id) => arrival[id]));
      continue;
    }
    const urgent = interactions.find((i) => !i.handled && available.includes(i.id) && i.at <= now + EPS);
    const target = urgent ? urgent.id : available[0];
    if (urgent) urgent.handled = true;

    const startAt = now;
    const endAt = startAt + boundaryCost;
    const preempt = interactions.find((i) =>
      !i.handled && remaining.has(i.id) && i.id !== target &&
      i.at > startAt + EPS && i.at < endAt - EPS && arrival[i.id] <= endAt);
    if (preempt) { interrupted++; now = preempt.at; continue; }   // 让出主线程,不提交半成品

    remaining.delete(target);
    now = endAt;
    steps.push({ id: target, startAt, endAt, jumpedQueue: !!urgent });
    if (firstInteractive === null) firstInteractive = endAt;
  }
  return { steps, readyAt: now, firstInteractive, interrupted, jumps: steps.filter((s) => s.jumpedQueue).length };
}

// ==================== §3 水合不匹配(可恢复 / 不可恢复 / 抑制) ====================

const tagOf = (html) => (html.match(/^<([a-zA-Z][\w-]*)/) || [, '?'])[1];

/**
 * 等价于 hydrateRoot 的匹配策略:
 *   结构(标签)不同 → 不可恢复,整棵子树退化为客户端渲染
 *   仅文本/属性不同 → 可恢复,React 记录 onRecoverableError 并按客户端结果重渲染该处
 *   suppressHydrationWarning → 静默(官方:只作用一层,且**不会**修正文本)
 */
function hydrateBoundary(serverHtml, clientHtml, options = {}) {
  if (serverHtml === clientHtml) return { status: 'match', patched: false };
  if (options.suppressHydrationWarning) {
    return { status: 'suppressed', patched: false, note: '仅静默警告,不修正内容' };
  }
  if (tagOf(serverHtml) !== tagOf(clientHtml)) {
    return { status: 'fatal', patched: true, recovery: 'client-render-subtree' };
  }
  return { status: 'recoverable', patched: true, recovery: 'client-re-render', reported: true };
}

/** 两遍渲染:首遍与服务端一致,Effect 后才切换成客户端内容(官方推荐的 isClient 模式) */
function twoPassRender(serverText, clientText) {
  const first = serverText;   // 首遍:与 SSR 输出一致 → 不产生不匹配
  const second = clientText;  // Effect 内 setIsClient(true) 后的同步第二遍
  return {
    passes: [first, second],
    firstMatchesServer: first === serverText,
    secondIsClientContent: second === clientText,
    renderCount: 2,           // 代价:组件渲染两次
  };
}

module.exports = {
  Timeline, openMark, closeMark, renderToPipeableStream, renderToString,
  planHydration, hydrateBoundary, tagOf, twoPassRender,
};
