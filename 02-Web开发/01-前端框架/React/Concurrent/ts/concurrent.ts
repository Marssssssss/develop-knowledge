/**
 * React 并发渲染最小实现 — TypeScript 类型化版本
 *
 * 与 js/lane_model.js 等价,补齐整形别名(Lane / Lanes / EventPriority)与可辨识联合,
 * 并把"抛 thenable"建模为 SuspenseThrown。零依赖、不引入 React。
 *
 * 本机无 tsc,未实际编译;类型仅作静态说明用(与仓库其它 ts/ 目录同策略)。
 * 权威来源见 js/lane_model.js 文件头。
 */

// ==================== 类型 ====================

/** 单条 lane:2 的幂,恰好 1 个 bit 置位 */
export type Lane = number;
/** 一组 lane 的位掩码,可以是多条 lane 的按位或 */
export type Lanes = number;
/** 过期时间戳:0 表示立即同步,NoTimestamp 表示永不过期 */
export type Timestamp = number;

export const enum EventPriority { Discrete = 0, Continuous = 1, Default = 2, Idle = 3 }

export type SchedulerPriorityLevel =
  | 'ImmediatePriority' | 'UserBlockingPriority' | 'NormalPriority' | 'LowPriority' | 'IdlePriority';

export interface WorkUnit { id: string; costMs: number }

export interface RenderResult { processed: number; aborted: boolean; remaining: number }

export interface InterruptLog {
  committed: string[];
  discardedUnits: number;
  transitionRenders: number;
  shownIntermediate: boolean;
}

export interface SuspenseResult {
  shell: string[];
  fallback: string | null;
  content: string | null;
}

// ==================== §1 Lane 位掩码代数 ====================

export const NoLane: Lanes = 0;
export const NoTimestamp: Timestamp = -1;
export const TotalLanes = 31;

export const SyncLane: Lane = 0b0000000000000000000000000000001;             // bit0  离散事件
export const InputContinuousLane: Lane = 0b0000000000000000000000000000100;  // bit2  连续事件
export const DefaultLane: Lane = 0b0000000000000000000000000010000;          // bit4  普通 setState
export const TransitionLane1: Lane = 0b0000000000000000000000001000000;      // bit6  useTransition
export const TransitionLanes: Lanes = 0b0000000001111111111111111000000;     // bit6..bit21,16 条
export const IdleLane: Lane = 0b0100000000000000000000000000000;             // bit29
export const OffscreenLane: Lane = 0b1000000000000000000000000000000;        // bit30
export const NonIdleLanes: Lanes = 0b0011111111111111111111111111111;

export const mergeLanes = (a: Lanes, b: Lanes): Lanes => a | b;
export const includesSomeLane = (set: Lanes, subset: Lanes): boolean => (set & subset) !== NoLane;
export const isSubsetOfLanes = (set: Lanes, subset: Lanes): boolean => (set & subset) === subset;
export const removeLanes = (set: Lanes, subset: Lanes): Lanes => set & ~subset;
/** 补码性质 O(1) 取最低置位 = 最高优先级 */
export const getHighestPriorityLane = (lanes: Lanes): Lane => lanes & -lanes;
export const includesNonIdleWork = (lanes: Lanes): boolean => (lanes & NonIdleLanes) !== NoLane;
export const includesBlockingLane = (lanes: Lanes): boolean =>
  (lanes & (SyncLane | InputContinuousLane | DefaultLane)) !== NoLane;
export const isTransitionLane = (lane: Lane): boolean => (lane & TransitionLanes) !== NoLane;
export const laneToIndex = (lane: Lane): number => 31 - Math.clz32(lane);
export const indexToLane = (i: number): Lane => 1 << i;

// ==================== §2 优先级映射 + 过期升级 ====================

/**
 * 过期启发式(社区精读转录自 ReactFiberLane.js 的 getTimeout 语义):
 * Sync 立即、连续输入 250ms、其余 5000ms、Idle/Offscreen 永不过期。
 * 用途不是"到点就跑",而是给低优更新一个最迟完成时间,超时升级为同步以防饿死。
 */
export function laneToTimeout(lane: Lane): Timestamp {
  if (lane === SyncLane) return 0;
  if (lane === InputContinuousLane) return 250;
  if ((lane & (IdleLane | OffscreenLane)) !== NoLane) return NoTimestamp;
  return 5000;
}

/** 第一段:lanes → EventPriority */
export function lanesToEventPriority(lanes: Lanes): EventPriority {
  const lane = getHighestPriorityLane(lanes);
  if (includesSomeLane(lane, SyncLane)) return EventPriority.Discrete;
  if (includesSomeLane(lane, InputContinuousLane)) return EventPriority.Continuous;
  if (includesSomeLane(lane, DefaultLane)) return EventPriority.Default;
  return EventPriority.Idle;
}

/** 第二段:EventPriority → Scheduler 优先级(React 与 Scheduler 优先级不互通,需两次转换) */
export function eventPriorityToSchedulerPriority(p: EventPriority): SchedulerPriorityLevel {
  switch (p) {
    case EventPriority.Discrete: return 'ImmediatePriority';
    case EventPriority.Continuous: return 'UserBlockingPriority';
    case EventPriority.Default: return 'NormalPriority';
    default: return 'IdlePriority';
  }
}
export const lanesToSchedulerPriority = (lanes: Lanes): SchedulerPriorityLevel =>
  eventPriorityToSchedulerPriority(lanesToEventPriority(lanes));

export class FiberRoot {
  pendingLanes: Lanes = NoLane;
  expiredLanes: Lanes = NoLane;
  entangledLanes: Lanes = NoLane;
  expirationTimes: Timestamp[] = new Array(TotalLanes).fill(NoTimestamp);
  eventTimes: Timestamp[] = new Array(TotalLanes).fill(NoTimestamp);

  markUpdated(updateLane: Lane, eventTime: Timestamp): void {
    this.pendingLanes = mergeLanes(this.pendingLanes, updateLane);
    this.eventTimes[laneToIndex(updateLane)] = eventTime;
  }

  /** 每轮调度开头调用:补过期时间戳,并把已超时的 lane 收进 expiredLanes */
  markStarvedLanesAsExpired(currentTime: Timestamp): Lane[] {
    let lanes = this.pendingLanes;
    const expired: Lane[] = [];
    while (lanes > NoLane) {
      const index = 31 - Math.clz32(lanes);
      const lane = indexToLane(index);
      const expirationTime = this.expirationTimes[index];
      if (expirationTime === NoTimestamp) {
        const timeout = laneToTimeout(lane);
        if (timeout !== NoTimestamp) this.expirationTimes[index] = currentTime + timeout;
      } else if (expirationTime <= currentTime) {
        this.expiredLanes = mergeLanes(this.expiredLanes, lane);
        expired.push(lane);
      }
      lanes = removeLanes(lanes, lane);
    }
    return expired;
  }

  /** 选下一批 lanes:expiredLanes 优先于一切(强制同步,保证最终一致) */
  getNextLanes(): Lanes {
    if (this.pendingLanes === NoLane) return NoLane;
    if (this.expiredLanes !== NoLane) return this.expiredLanes;
    const nonIdle = this.pendingLanes & NonIdleLanes;
    return getHighestPriorityLane(nonIdle !== NoLane ? nonIdle : this.pendingLanes);
  }

  /** 挂起:从 pending 摘掉并复位过期时间(等价于 getSuspendedExpirationTime) */
  suspend(lanes: Lanes): void {
    this.pendingLanes = removeLanes(this.pendingLanes, lanes);
    let rest = lanes;
    while (rest > NoLane) {
      const lane = getHighestPriorityLane(rest);
      this.expirationTimes[laneToIndex(lane)] = NoTimestamp;
      rest = removeLanes(rest, lane);
    }
  }
}

// ==================== §3 时间切片 ====================

export const FRAME_BUDGET_MS = 5;

export class VirtualScheduler {
  now = 0;
  private sliceStart = 0;
  slices = 0;
  yields = 0;
  maxSliceMs = 0;
  paints = 0;

  shouldYield(): boolean { return this.now - this.sliceStart >= FRAME_BUDGET_MS; }
  beginSlice(): void { this.sliceStart = this.now; this.slices++; }
  endSlice(): void { this.maxSliceMs = Math.max(this.maxSliceMs, this.now - this.sliceStart); }
  paint(): void { this.yields++; this.paints++; this.now += 16; }  // 让出主线程 → 浏览器绘一帧
}

export function makeWorkUnits(count: number, costMs: number): WorkUnit[] {
  return Array.from({ length: count }, (_, i) => ({ id: `fiber-${i}`, costMs }));
}

export function renderSync(units: WorkUnit[], sched: VirtualScheduler): { processed: number } {
  sched.beginSlice();
  for (const u of units) sched.now += u.costMs;
  sched.endSlice();
  return { processed: units.length };
}

export function renderConcurrent(units: WorkUnit[], sched: VirtualScheduler): RenderResult {
  let processed = 0;
  sched.beginSlice();
  for (let i = 0; i < units.length; i++) {
    if (sched.shouldYield()) { sched.endSlice(); sched.paint(); sched.beginSlice(); }
    sched.now += units[i].costMs;
    processed++;
  }
  sched.endSlice();
  return { processed, aborted: false, remaining: 0 };
}

// ==================== §4 transition 被打断 ====================

/** 紧急更新插队 → 未提交的 transition 工作整段丢弃 → 从头重渲染(不接续半成品) */
export function renderInputAndTransition(
  sched: VirtualScheduler, transitionUnits: WorkUnit[], interruptAt: number,
): InterruptLog {
  const log: InterruptLog = { committed: [], discardedUnits: 0, transitionRenders: 0, shownIntermediate: false };

  log.transitionRenders++;
  let i = 0;
  sched.beginSlice();
  for (; i < transitionUnits.length; i++) {
    if (i >= interruptAt) break;
    if (sched.shouldYield()) { sched.endSlice(); sched.paint(); sched.beginSlice(); }
    sched.now += transitionUnits[i].costMs;
  }
  sched.endSlice();
  log.discardedUnits = i;
  if (i >= transitionUnits.length) { log.committed.push('transition:results'); return log; }

  log.committed.push('input:urgent');   // 受控输入必须同步可见
  sched.now += 1;
  log.transitionRenders++;
  renderConcurrent(transitionUnits, sched);
  log.committed.push('transition:results');
  return log;
}

// ==================== §5 Suspense 边界 ====================

/** 未就绪时 read() 抛自身,被边界捕获 → 渲染 fallback(真实实现抛 pending promise) */
export class Resource {
  status: 'pending' | 'resolved' = 'pending';
  readyAt = -1;
  readCount = 0;
  constructor(readonly key: string, readonly delayMs: number, readonly payload: string) {}
  read(now: number): string {
    this.readCount++;
    if (this.status === 'pending') {
      if (this.readyAt < 0) this.readyAt = now + this.delayMs;
      if (now < this.readyAt) throw this;
      this.status = 'resolved';
    }
    return this.payload;
  }
}

export function renderWholePageBlocking(resource: Resource, now: number):
  { mode: 'blocking'; shell: string[]; skeleton: string | null } {
  const shell = ['<header>', '<nav>'];
  try {
    shell.push(`<section>posts:${resource.read(now)}</section>`);
  } catch (thrown) {
    if (thrown instanceof Resource) return { mode: 'blocking', shell: [], skeleton: 'full-page' };
    throw thrown;
  }
  shell.push('<aside>', '<footer>');
  return { mode: 'blocking', shell, skeleton: null };
}

export function renderWithSuspenseBoundary(resource: Resource, now: number): SuspenseResult {
  const out: SuspenseResult = { shell: ['<header>', '<nav>'], fallback: null, content: null };
  try {
    out.content = `posts:${resource.read(now)}`;
  } catch (thrown) {
    if (thrown instanceof Resource) {
      out.fallback = '<skeleton class="posts">';   // 只有该区域显示占位,兄弟节点早已上屏
      out.shell.push('<aside>', '<footer>');
      return out;
    }
    throw thrown;
  }
  out.shell.push('<aside>', '<footer>');
  return out;
}

// ==================== 场景演示(js/ 版有完整断言) ====================

export function main(): void {
  const units = makeWorkUnits(1200, 0.05);

  const sync = new VirtualScheduler();
  renderSync(units, sync);

  const cc = new VirtualScheduler();
  renderConcurrent(units, cc);

  const log = renderInputAndTransition(new VirtualScheduler(), units, 100);

  const res = new Resource('posts', 300, '42 posts');
  const first = renderWithSuspenseBoundary(res, 0);
  const second = renderWithSuspenseBoundary(res, 300);

  console.log(`同步: maxSlice=${sync.maxSliceMs.toFixed(1)}ms yields=${sync.yields}`);
  console.log(`并发: maxSlice=${cc.maxSliceMs.toFixed(2)}ms yields=${cc.yields} slices=${cc.slices}`);
  console.log(`打断: 丢弃 ${log.discardedUnits} 单元,提交顺序 ${log.committed.join(' → ')}`);
  console.log(`Suspense: 首屏 ${first.shell.join('')} fallback=${first.fallback} → 就绪后 ${second.content}`);
}
