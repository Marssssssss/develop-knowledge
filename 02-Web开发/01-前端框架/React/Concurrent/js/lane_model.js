/**
 * React 并发渲染最小实现 — 模型层 (JavaScript)
 *   §1 Lane 位掩码代数   §2 优先级映射 + 过期升级(防饿死)
 *   §3 时间切片(render 可中断)  §4 transition 被打断 → 丢弃陈旧渲染
 *   §5 Suspense 边界:throw thenable → fallback → 重试
 * 权威来源(实际读过): react.dev/blog/2022/03/29/react-v18、react.dev/reference/react/useTransition、
 *   react.dev/reference/react-dom/client/hydrateRoot;Lane 位值与过期启发式转录自社区对
 *   React 源码 ReactFiberLane.js 的精读(非官方,常量仅供演示)。
 * 自检与运行入口见同目录 concurrent.js(require 本文件)。
 */

'use strict';

// ==================== §1 Lane 位掩码代数 ====================

const NoLane = 0;
const TotalLanes = 31;                                     // lane 总数,位 0..30(第 31 位留作符号位)
const SyncLane = 0b0000000000000000000000000000001;          // bit0  离散事件 click/keydown
const InputContinuousLane = 0b0000000000000000000000000000100; // bit2  连续事件 drag/scroll
const DefaultLane = 0b0000000000000000000000000010000;       // bit4  网络请求 / 普通 setState
const TransitionLane1 = 0b0000000000000000000000001000000;   // bit6  useTransition
const TransitionLanes = 0b0000000001111111111111111000000;   // bit6..bit21 共 16 条
const IdleLane = 0b0100000000000000000000000000000;          // bit29 空闲
const OffscreenLane = 0b1000000000000000000000000000000;     // bit30 离屏/预渲染
const NonIdleLanes = 0b0011111111111111111111111111111;      // 除 Idle/Offscreen 之外的全部
const NoTimestamp = -1;

const mergeLanes = (a, b) => a | b;
const includesSomeLane = (a, b) => (a & b) !== NoLane;
const isSubsetOfLanes = (set, subset) => (set & subset) === subset;
const removeLanes = (set, subset) => set & ~subset;
/** 取最低置位 = 最高优先级:`lanes & -lanes`(补码性质,O(1) 单指令) */
const getHighestPriorityLane = (lanes) => lanes & -lanes;
const includesNonIdleWork = (lanes) => (lanes & NonIdleLanes) !== NoLane;
const includesBlockingLane = (lanes) =>
  (lanes & (SyncLane | InputContinuousLane | DefaultLane)) !== NoLane;
const isTransitionLane = (lane) => (lane & TransitionLanes) !== NoLane;
/** 单条 lane → 位下标(31 - clz32 等价于 floor(log2)) */
const laneToIndex = (lane) => 31 - Math.clz32(lane);
const indexToLane = (i) => 1 << i;

// ==================== §2 优先级映射 + 过期升级 ====================

const EventPriority = { Discrete: 0, Continuous: 1, Default: 2, Idle: 3 };
const SchedulerPriority = {
  ImmediatePriority: 'ImmediatePriority',
  UserBlockingPriority: 'UserBlockingPriority',
  NormalPriority: 'NormalPriority',
  LowPriority: 'LowPriority',
  IdlePriority: 'IdlePriority',
};

/**
 * 过期启发式(社区精读转录):Sync 立即过期、连续输入 250ms、其余 5000ms。
 * 目的不是"到点就跑",而是给低优更新一个**最迟完成时间**,超时即升级为同步,防饿死。
 */
function laneToTimeout(lane) {
  if (lane === SyncLane) return 0;
  if (lane === InputContinuousLane) return 250;
  if ((lane & (IdleLane | OffscreenLane)) !== NoLane) return NoTimestamp; // 永不主动升级
  return 5000;
}

/** 第一段:lanes → EventPriority(取最高优先级那条 lane 决定) */
function lanesToEventPriority(lanes) {
  const lane = getHighestPriorityLane(lanes);
  if (includesSomeLane(lane, SyncLane)) return EventPriority.Discrete;
  if (includesSomeLane(lane, InputContinuousLane)) return EventPriority.Continuous;
  if (includesSomeLane(lane, DefaultLane)) return EventPriority.Default;
  return EventPriority.Idle;
}

/** 第二段:EventPriority → Scheduler 优先级(React 与 Scheduler 两套优先级不互通,需两次转换) */
function eventPriorityToSchedulerPriority(p) {
  switch (p) {
    case EventPriority.Discrete: return SchedulerPriority.ImmediatePriority;
    case EventPriority.Continuous: return SchedulerPriority.UserBlockingPriority;
    case EventPriority.Default: return SchedulerPriority.NormalPriority;
    default: return SchedulerPriority.IdlePriority;
  }
}
const lanesToSchedulerPriority = (lanes) => eventPriorityToSchedulerPriority(lanesToEventPriority(lanes));

class FiberRoot {
  constructor() {
    this.pendingLanes = NoLane;
    this.expiredLanes = NoLane;
    this.entangledLanes = NoLane;
    this.expirationTimes = new Array(TotalLanes).fill(NoTimestamp);
    this.eventTimes = new Array(TotalLanes).fill(NoTimestamp);
  }
}

function markRootUpdated(root, updateLane, eventTime) {
  root.pendingLanes = mergeLanes(root.pendingLanes, updateLane);
  root.eventTimes[laneToIndex(updateLane)] = eventTime;
}

/** 每轮调度开头调用:给没有过期时间的 lane 补一个,把已经超时的 lane 收进 expiredLanes */
function markStarvedLanesAsExpired(root, currentTime) {
  let lanes = root.pendingLanes;
  const expired = [];
  while (lanes > NoLane) {
    const index = 31 - Math.clz32(lanes);      // 从高位开始扫(pickArbitraryLaneIndex)
    const lane = indexToLane(index);
    const expirationTime = root.expirationTimes[index];
    if (expirationTime === NoTimestamp) {
      const timeout = laneToTimeout(lane);
      if (timeout !== NoTimestamp) root.expirationTimes[index] = currentTime + timeout;
    } else if (expirationTime <= currentTime) {
      root.expiredLanes = mergeLanes(root.expiredLanes, lane);
      expired.push(lane);
    }
    lanes = removeLanes(lanes, lane);
  }
  return expired;
}

/** 选下一批要渲染的 lanes:expiredLanes 优先于一切(强制同步,保证最终一致) */
function getNextLanes(root) {
  const pendingLanes = root.pendingLanes;
  if (pendingLanes === NoLane) return NoLane;
  if (root.expiredLanes !== NoLane) return root.expiredLanes;
  const nonIdle = pendingLanes & NonIdleLanes;
  if (nonIdle !== NoLane) return getHighestPriorityLane(nonIdle);
  return getHighestPriorityLane(pendingLanes);
}

function markLanesAsSuspendedAndResetExpiration(root, lanes) {
  root.pendingLanes = removeLanes(root.pendingLanes, lanes);
  let rest = lanes;
  while (rest > NoLane) {
    const index = laneToIndex(getHighestPriorityLane(rest));
    root.expirationTimes[index] = NoTimestamp;
    rest = removeLanes(rest, indexToLane(index));
  }
}

// ==================== §3 时间切片:render 可中断 ====================

const FRAME_BUDGET_MS = 5;   // React Scheduler 的默认帧预算

class VirtualScheduler {
  constructor() {
    this.now = 0;
    this.sliceStart = 0;
    this.slices = 0;
    this.yields = 0;         // 让出主线程的次数
    this.maxSliceMs = 0;     // 单个时间片最长占用(直接决定会不会掉帧)
    this.paints = 0;
  }
  shouldYield() { return this.now - this.sliceStart >= FRAME_BUDGET_MS; }
  beginSlice() { this.sliceStart = this.now; this.slices++; }
  endSlice() { this.maxSliceMs = Math.max(this.maxSliceMs, this.now - this.sliceStart); }
  /** 让出主线程 → 浏览器拿到机会绘制一帧(16.7ms @60Hz) */
  paint() { this.yields++; this.paints++; this.now += 16; }
}

/** 合成工作树:每个节点代表一个 Fiber 的 render 成本 */
function makeWorkUnits(count, costMs) {
  const units = [];
  for (let i = 0; i < count; i++) units.push({ id: 'fiber-' + i, costMs });
  return units;
}

function renderSync(units, sched) {
  sched.beginSlice();
  for (const u of units) sched.now += u.costMs;
  sched.endSlice();
  return { processed: units.length };
}

function renderConcurrent(units, sched, opts) {
  const onCheck = (opts && opts.onCheck) || null;
  let processed = 0;
  sched.beginSlice();
  for (let i = 0; i < units.length; i++) {
    if (onCheck && onCheck(i, sched) === 'abort') {
      sched.endSlice();
      return { processed, aborted: true, remaining: units.length - processed };
    }
    if (sched.shouldYield()) {   // 每个工作单元之间检查预算(单节点内部不可中断)
      sched.endSlice();
      sched.paint();
      sched.beginSlice();
    }
    sched.now += units[i].costMs;
    processed++;
  }
  sched.endSlice();
  return { processed, aborted: false, remaining: 0 };
}

// ==================== §4 transition 被打断 ====================

/**
 * 用户输入 → 紧急更新(受控 input 必须同步) + 非紧急更新(结果列表,transition)。
 * 图表/列表渲染到一半时用户又敲了一个字符 → 中断 + 丢弃已完成的部分 → 先提交输入 → 重头再渲染最新结果。
 */
function renderInputAndTransition(sched, transitionUnits, interruptAt) {
  const log = { committed: [], discardedUnits: 0, transitionRenders: 0, shownIntermediate: false };

  // 第 1 次 transition 渲染(会被打断)
  log.transitionRenders++;
  let i = 0;
  sched.beginSlice();
  for (; i < transitionUnits.length; i++) {
    if (i >= interruptAt) break;                          // 紧急更新到达,当前渲染作废
    if (sched.shouldYield()) { sched.endSlice(); sched.paint(); sched.beginSlice(); }
    sched.now += transitionUnits[i].costMs;
  }
  sched.endSlice();
  log.discardedUnits = i;                                 // 已做但不会提交的工作
  if (i >= transitionUnits.length) {
    log.committed.push('transition:results');
    return log;
  }

  // 紧急更新插队并立即提交(input 的值必须同步可见)
  log.committed.push('input:urgent');
  sched.now += 1;

  // 从零重渲染 transition(不接续被丢弃的半成品)
  log.transitionRenders++;
  renderConcurrent(transitionUnits, sched);
  log.committed.push('transition:results');
  log.shownIntermediate = false;                          // 中间态从未上屏
  return log;
}

// ==================== §5 Suspense 边界 ====================

/** 抛出来被 React 捕获的 thenable(真实实现是 pending promise) */
class Resource {
  constructor(key, delayMs, payload) {
    this.key = key;
    this.delayMs = delayMs;
    this.payload = payload;
    this.status = 'pending';
    this.readyAt = -1;
    this.readCount = 0;
  }
  read(now) {
    this.readCount++;
    if (this.status === 'pending') {
      if (this.readyAt < 0) this.readyAt = now + this.delayMs;   // 首次 read 才发起请求
      if (now < this.readyAt) throw this;                        // 未就绪 → 抛出,交给边界
      this.status = 'resolved';
    }
    return this.payload;
  }
}

/** 不用边界:整页被 fallback 替换(React 16 语义的"全屏 loading") */
function renderWholePageBlocking(resource, now) {
  const html = ['<header>', '<nav>'];
  try {
    html.push('<section>posts:' + resource.read(now) + '</section>');
  } catch (thrown) {
    if (thrown instanceof Resource) return { mode: 'blocking', shell: [], skeleton: 'full-page' };
    throw thrown;
  }
  html.push('<aside>', '<footer>');
  return { mode: 'blocking', shell: html, skeleton: null };
}

/** 用 <Suspense> 包住慢区域:边界外的兄弟节点首屏照常渲染 */
function renderWithSuspenseBoundary(resource, now) {
  const out = { mode: 'suspense', shell: [], fallback: null, content: null };
  out.shell.push('<header>', '<nav>');          // 边界之外:首屏可见
  try {
    out.content = 'posts:' + resource.read(now);
  } catch (thrown) {
    if (thrown instanceof Resource) {
      out.fallback = '<skeleton class="posts">';   // 只有这个区域显示占位
      out.shell.push('<aside>', '<footer>');
      return out;
    }
    throw thrown;
  }
  out.shell.push('<aside>', '<footer>');
  return out;
}


module.exports = {
  NoLane, TotalLanes, NoTimestamp,
  SyncLane, InputContinuousLane, DefaultLane, TransitionLane1, TransitionLanes, IdleLane, OffscreenLane, NonIdleLanes,
  mergeLanes, includesSomeLane, isSubsetOfLanes, removeLanes, getHighestPriorityLane,
  includesNonIdleWork, includesBlockingLane, isTransitionLane, laneToIndex, indexToLane,
  EventPriority, SchedulerPriority, laneToTimeout, lanesToEventPriority,
  eventPriorityToSchedulerPriority, lanesToSchedulerPriority,
  FiberRoot, markRootUpdated, markStarvedLanesAsExpired, getNextLanes, markLanesAsSuspendedAndResetExpiration,
  FRAME_BUDGET_MS, VirtualScheduler, makeWorkUnits, renderSync, renderConcurrent,
  renderInputAndTransition, Resource, renderWholePageBlocking, renderWithSuspenseBoundary,
};
