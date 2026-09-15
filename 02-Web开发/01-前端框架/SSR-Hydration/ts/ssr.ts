/**
 * SSR 流式渲染 + 选择性水合 — TypeScript 类型化版本
 *
 * 与 js/ssr.js 等价。类型重点:用可辨识联合把"服务端 chunk 的三种类型"和
 * "水合结果的三态(match / recoverable / fatal)"钉死 —— 这两处正是最容易写错分支的地方。
 *
 * 本机无 tsc,未实际编译(与仓库其它 ts/ 目录同策略)。权威来源见 js/ssr.js 文件头。
 */

// ==================== 服务端 chunk(可辨识联合) ====================

export type Chunk =
  | { type: 'shell'; at: number; html: string }
  | { type: 'boundary'; at: number; id: BoundaryId; mode: 'replace-content'; html: string }
  | { type: 'end'; at: number; html: string };

export type BoundaryId = string | number;

export interface BoundarySpec {
  id: BoundaryId;
  /** shell flush 时先下发的占位内容 */
  fallback: string;
  /** 数据就绪时刻(虚拟时间) */
  resolveAt: number;
  content: string;
}

export interface Page {
  shell: string[];
  boundary: BoundarySpec;
  tail: string[];
  shellReadyAt: number;
}

export interface StreamOptions {
  send: (chunk: Chunk) => void;
  onShellReady?: (at: number) => void;
  onBoundaryReady?: (id: BoundaryId, at: number) => void;
  onAllReady?: (at: number) => void;
}

/** React 内部同款注释标记:<!--$S:id-->…<!--/$S:id-->;真实实现还用 $RC 内联脚本指示替换 body */
export const openMark = (id: BoundaryId): string => `<!--$S:${id}-->`;
export const closeMark = (id: BoundaryId): string => `<!--/$S:${id}-->`;

// ==================== 虚拟时间线 ====================

export class Timeline {
  now = 0;
  private events: { t: number; seq: number; fn: (t: number) => void }[] = [];
  private seq = 0;

  at(t: number, fn: (t: number) => void): this {
    this.events.push({ t, seq: this.seq++, fn });
    return this;
  }

  run(): this {
    const sorted = [...this.events].sort((a, b) => a.t - b.t || a.seq - b.seq);
    for (const e of sorted) { this.now = e.t; e.fn(e.t); }
    return this;
  }
}

// ==================== 服务端渲染 ====================

/**
 * 等价于 renderToPipeableStream(<App/>, { onShellReady, onAllReady }):
 * shell 先 flush(慢区域先放 fallback),边界数据就绪后作为独立 chunk 追加下发。
 */
export function renderToPipeableStream(page: Page, opts: StreamOptions): (tl: Timeline) => Chunk[] {
  const chunks: Chunk[] = [];
  return (tl: Timeline): Chunk[] => {
    tl.at(page.shellReadyAt, (t) => {
      const placeholder = openMark(page.boundary.id) + page.boundary.fallback + closeMark(page.boundary.id);
      const chunk: Chunk = { type: 'shell', at: t, html: page.shell.join('') + placeholder };
      chunks.push(chunk); opts.send(chunk); opts.onShellReady?.(t);
    });
    tl.at(page.boundary.resolveAt, (t) => {
      const chunk: Chunk = {
        type: 'boundary', at: t, id: page.boundary.id, mode: 'replace-content',
        html: openMark(page.boundary.id) + page.boundary.content + closeMark(page.boundary.id),
      };
      chunks.push(chunk); opts.send(chunk); opts.onBoundaryReady?.(page.boundary.id, t);
    });
    tl.at(page.boundary.resolveAt + 1, (t) => {
      const chunk: Chunk = { type: 'end', at: t, html: page.tail.join('') };
      chunks.push(chunk); opts.send(chunk); opts.onAllReady?.(t);
    });
    tl.run();
    return chunks;
  };
}

/** 非流式基线:renderToString 必须等所有数据就绪才能吐第一个字节 */
export function renderToString(page: Page): Chunk {
  return { type: 'shell', at: page.boundary.resolveAt, html: page.shell.join('') + page.boundary.content + page.tail.join('') };
}

// ==================== 客户端选择性水合 ====================

export interface Interaction { id: BoundaryId; at: number }
export interface HydrationStep { id: BoundaryId | 'shell'; startAt: number; endAt: number; jumpedQueue: boolean }
export interface HydrationPlan {
  steps: HydrationStep[];
  readyAt: number;
  firstInteractive: number | null;
  /** 因用户交互而被打断的水合次数(被打断的工作作废,须重做) */
  interrupted: number;
  jumps: number;
}

/**
 * 水合调度(主线程串行 + 可中断):
 *   shell 先水合 → 只有内容已到达的边界可水合 → 用户交互优先且能打断当前水合。
 */
export function planHydration(
  chunks: Chunk[],
  opts: { shellCost?: number; boundaryCost?: number; interactions?: Interaction[] } = {},
): HydrationPlan {
  const EPS = 1e-9;
  const shellCost = opts.shellCost ?? 30;
  const boundaryCost = opts.boundaryCost ?? 20;
  const interactions = (opts.interactions ?? []).map((i) => ({ ...i, handled: false }));

  const shell = chunks.find((c) => c.type === 'shell') as Extract<Chunk, { type: 'shell' }>;
  const arrival = new Map<BoundaryId, number>();
  for (const c of chunks) if (c.type === 'boundary') arrival.set(c.id, c.at);
  const order = [...arrival.keys()];

  const remaining = new Set(order);
  const steps: HydrationStep[] = [];
  let now = shell.at;
  let shellHydrated = false;
  let firstInteractive: number | null = null;
  let interrupted = 0;

  while (!shellHydrated || remaining.size > 0) {
    if (!shellHydrated) {
      const endAt = now + shellCost;
      const preempt = interactions.find((i) =>
        !i.handled && remaining.has(i.id) && i.at > now && i.at < endAt && (arrival.get(i.id) ?? Infinity) <= endAt);
      if (preempt) { interrupted++; now = preempt.at; continue; }
      steps.push({ id: 'shell', startAt: now, endAt, jumpedQueue: false });
      now = endAt;
      shellHydrated = true;
      continue;
    }
    if (remaining.size === 0) break;

    const available = order.filter((id) => remaining.has(id) && (arrival.get(id) as number) <= now + EPS);
    if (available.length === 0) {
      now = Math.min(...order.filter((id) => remaining.has(id)).map((id) => arrival.get(id) as number));
      continue;
    }
    const urgent = interactions.find((i) => !i.handled && available.includes(i.id) && i.at <= now + EPS);
    const target = urgent ? urgent.id : available[0];
    if (urgent) urgent.handled = true;

    const startAt = now;
    const endAt = startAt + boundaryCost;
    const preempt = interactions.find((i) =>
      !i.handled && remaining.has(i.id) && i.id !== target &&
      i.at > startAt + EPS && i.at < endAt - EPS && (arrival.get(i.id) as number) <= endAt);
    if (preempt) { interrupted++; now = preempt.at; continue; }

    remaining.delete(target);
    now = endAt;
    steps.push({ id: target, startAt, endAt, jumpedQueue: !!urgent });
    if (firstInteractive === null) firstInteractive = endAt;
  }
  return { steps, readyAt: now, firstInteractive, interrupted, jumps: steps.filter((s) => s.jumpedQueue).length };
}

// ==================== 水合不匹配 ====================

/** 三态结果:match / recoverable(可恢复:记录后按客户端结果重渲染)/ fatal(结构不符,子树退化为客户端渲染) */
export type HydrationResult =
  | { status: 'match'; patched: false }
  | { status: 'recoverable'; patched: true; recovery: 'client-re-render'; reported: true }
  | { status: 'fatal'; patched: true; recovery: 'client-render-subtree' }
  | { status: 'suppressed'; patched: false; note: string };

export const tagOf = (html: string): string =>
  (html.match(/^<([a-zA-Z][\w-]*)/)?.[1]) ?? '?';

export function hydrateBoundary(serverHtml: string, clientHtml: string, options: { suppressHydrationWarning?: boolean } = {}): HydrationResult {
  if (serverHtml === clientHtml) return { status: 'match', patched: false };
  if (options.suppressHydrationWarning) {
    return { status: 'suppressed', patched: false, note: '仅静默警告,不修正内容(官方:只作用一层)' };
  }
  if (tagOf(serverHtml) !== tagOf(clientHtml)) {
    return { status: 'fatal', patched: true, recovery: 'client-render-subtree' };
  }
  return { status: 'recoverable', patched: true, recovery: 'client-re-render', reported: true };
}

/** 两遍渲染:首遍与服务端一致(无不匹配),Effect 内 setIsClient(true) 后第二遍换成客户端内容 */
export function twoPassRender(serverText: string, clientText: string): {
  passes: [string, string]; firstMatchesServer: boolean; secondIsClientContent: boolean; renderCount: number;
} {
  return {
    passes: [serverText, clientText],
    firstMatchesServer: true,
    secondIsClientContent: true,
    renderCount: 2,   // 代价:组件渲染两次,慢网络下可能闪烁
  };
}

/** 官方列出的常见不匹配原因(用于 lint/自查清单) */
export const MISMATCH_CAUSES = [
  '根部多余的空白与换行',
  '渲染逻辑里出现 typeof window !== "undefined" 分支',
  '渲染逻辑里使用浏览器专有 API(window.matchMedia 等)',
  '服务端与客户端渲染了不同数据(时间戳、随机数)',
  'useId 在两端前缀不一致(多 root 时需 identifierPrefix)',
] as const;
