/**
 * React 并发渲染最小实现 — 自检入口 (JavaScript)
 * 模型定义见同目录 lane_model.js;本文件只负责断言与结果输出。
 * 运行:node concurrent.js(全部通过 exit 0,否则 exit 1)
 */

'use strict';

const M = require('./lane_model.js');
const {
  NoLane, NoTimestamp, TotalLanes,
  SyncLane, InputContinuousLane, DefaultLane, TransitionLane1, TransitionLanes, IdleLane, OffscreenLane, NonIdleLanes,
  mergeLanes, includesSomeLane, isSubsetOfLanes, removeLanes, getHighestPriorityLane,
  includesBlockingLane, laneToIndex, indexToLane, laneToTimeout, lanesToSchedulerPriority, SchedulerPriority,
  FiberRoot, markRootUpdated, markStarvedLanesAsExpired, getNextLanes, markLanesAsSuspendedAndResetExpiration,
  FRAME_BUDGET_MS, VirtualScheduler, makeWorkUnits, renderSync, renderConcurrent,
  renderInputAndTransition, Resource, renderWholePageBlocking, renderWithSuspenseBoundary,
} = M;
void TotalLanes;

// ==================== 自检 ====================

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail });
}

function runLaneAlgebra() {
  check('lane 位值 = React 源码转录', SyncLane === 1 && InputContinuousLane === 4 &&
    DefaultLane === 16 && TransitionLane1 === 64 && IdleLane === (1 << 29) && OffscreenLane === (1 << 30),
    `Sync=${SyncLane} InputCont=${InputContinuousLane} Default=${DefaultLane} Trans1=${TransitionLane1}`);
  check('NonIdleLanes = (1<<29)-1', NonIdleLanes === (1 << 29) - 1, String(NonIdleLanes));
  check('TransitionLanes 覆盖 16 条 (bit6..bit21)', TransitionLanes === ((1 << 22) - 1) - ((1 << 6) - 1),
    '0x' + TransitionLanes.toString(16));
  check('getHighestPriorityLane 取最低置位', getHighestPriorityLane(DefaultLane | SyncLane | TransitionLane1) === SyncLane,
    String(getHighestPriorityLane(DefaultLane | SyncLane | TransitionLane1)));
  check('lanes & -lanes 对 OffscreenLane 仍成立', getHighestPriorityLane(OffscreenLane | IdleLane) === IdleLane, '');
  const merged = mergeLanes(TransitionLane1, DefaultLane);
  check('批(merge)可跨不相邻位', isSubsetOfLanes(merged, TransitionLane1) && isSubsetOfLanes(merged, DefaultLane), '');
  check('isSubsetOfLanes 判包含', isSubsetOfLanes(merged, SyncLane) === false, '');
  check('removeLanes 清位', removeLanes(merged, DefaultLane) === TransitionLane1, '');
  check('includesBlockingLane 排除 transition/idle',
    includesBlockingLane(DefaultLane) && !includesBlockingLane(TransitionLane1) && !includesBlockingLane(IdleLane), '');
  check('laneToIndex ↔ indexToLane 互逆', laneToIndex(TransitionLane1) === 6 && indexToLane(6) === TransitionLane1, '');
  check('TotalLanes = 31 且 bit31 不可用', TotalLanes === 31 && (1 << 31) < 0, '(1<<31) 溢出为负数');
}

function runScheduling() {
  check('lanes → EventPriority → Scheduler 两段映射',
    lanesToSchedulerPriority(SyncLane) === SchedulerPriority.ImmediatePriority &&
    lanesToSchedulerPriority(InputContinuousLane) === SchedulerPriority.UserBlockingPriority &&
    lanesToSchedulerPriority(DefaultLane) === SchedulerPriority.NormalPriority &&
    lanesToSchedulerPriority(IdleLane) === SchedulerPriority.IdlePriority,
    lanesToSchedulerPriority(SyncLane) + '/' + lanesToSchedulerPriority(IdleLane));

  const root = new FiberRoot();
  markRootUpdated(root, TransitionLane1, 0);
  markRootUpdated(root, DefaultLane, 0);
  check('同一时刻多条 lane:取最高优先级(Default > Transition)',
    getNextLanes(root) === DefaultLane, String(getNextLanes(root)));

  // 只挂一个 transition,且它一直没被提交 → 首次被观察时刻 + 5000ms 后过期,升级为同步强制渲染
  const starved = new FiberRoot();
  markRootUpdated(starved, TransitionLane1, 0);
  const early = markStarvedLanesAsExpired(starved, 1000);
  check('1000ms 时 transition 尚未过期,但已写入过期时间戳', early.length === 0 &&
    getNextLanes(starved) === TransitionLane1 && starved.expirationTimes[6] === 6000,
    `expirationTimes[6]=${starved.expirationTimes[6]} (= 首次观察 1000 + 5000)`);
  const nearly = markStarvedLanesAsExpired(starved, 5999);
  check('5999ms 仍未过期(过期点 = 首次观察时刻 + timeout,不是绝对 5000ms)',
    nearly.length === 0 && starved.expiredLanes === NoLane, '');
  const expired = markStarvedLanesAsExpired(starved, 6001);
  check('6001ms 后 transition 过期 → expiredLanes 插队', expired.length === 1 && getNextLanes(starved) === TransitionLane1 &&
    starved.expiredLanes === TransitionLane1, 'expired=' + expired.length);
  check('过期是"防饿死"而非取消:pendingLanes 不动', includesSomeLane(starved.pendingLanes, TransitionLane1), '');
  check('Idle/Offscreen 永不主动升级', laneToTimeout(IdleLane) === NoTimestamp && laneToTimeout(OffscreenLane) === NoTimestamp, '');

  // 挂起(suspend)的 lane 从 pending 摘掉并清过期,ping 后重新进 pending(这里只验证清理)
  markLanesAsSuspendedAndResetExpiration(starved, TransitionLane1);
  check('挂起后 pendingLanes 清空且过期时间复位',
    starved.pendingLanes === NoLane && starved.expirationTimes[6] === NoTimestamp, '');
}

function runTimeSlicing() {
  const units = makeWorkUnits(1200, 0.05);   // 总成本 ≈ 60ms,远超一个帧预算

  const syncSched = new VirtualScheduler();
  renderSync(units, syncSched);
  check('同步渲染:1 个时间片、0 次让出 → 总耗时即单帧阻塞时间',
    syncSched.slices === 1 && syncSched.yields === 0 && Math.abs(syncSched.maxSliceMs - 60) < 1e-6,
    `slices=${syncSched.slices} yields=${syncSched.yields} maxSlice=${syncSched.maxSliceMs.toFixed(2)}ms`);

  const ccSched = new VirtualScheduler();
  const cc = renderConcurrent(units, ccSched);
  check('并发渲染:让出主线程 ≥ 1 次', ccSched.yields >= 1, `yields=${ccSched.yields}`);
  check('并发渲染:单时间片 < 5ms + 单节点成本', ccSched.maxSliceMs <= FRAME_BUDGET_MS + 0.05 + 1e-9,
    `maxSlice=${ccSched.maxSliceMs.toFixed(2)}ms`);
  check('并发渲染:工作单元不重复、不遗漏', cc.processed === units.length && cc.aborted === false, '');
  check('切片只改变调度,不改变总工作量', ccSched.slices === ccSched.yields + 1 && ccSched.paints === ccSched.yields,
    `slices=${ccSched.slices} paints=${ccSched.paints}`);
  check('同步渲染总墙钟 ≤ 并发(并发要额外付让出+绘制的代价)',
    syncSched.now < ccSched.now, `sync=${syncSched.now}ms concurrent=${ccSched.now}ms`);
}

function runTransitionInterrupt() {
  const sched = new VirtualScheduler();
  const units = makeWorkUnits(1200, 0.05);
  const log = renderInputAndTransition(sched, units, 100);
  check('被打断的 transition 工作被丢弃(不提交)', log.discardedUnits === 100, `discarded=${log.discardedUnits} units`);
  check('紧急更新先于 transition 提交', log.committed.join(',') === 'input:urgent,transition:results', log.committed.join(','));
  check('transition 重新渲染一次(共 2 次 render,1 次作废)', log.transitionRenders === 2, String(log.transitionRenders));
  check('中间态从未上屏(无 glitch)', log.shownIntermediate === false, '');
}

function runSuspense() {
  const t0 = 0;
  // A. 不用边界:慢区域拖垮整页
  const blockingRes = new Resource('posts', 300, '42 posts');
  const blocking = renderWholePageBlocking(blockingRes, t0);
  check('无边界:首屏只剩整页骨架,兄弟区块也看不到',
    blocking.skeleton === 'full-page' && blocking.shell.length === 0, JSON.stringify(blocking));

  // B. 用边界:兄弟节点首屏可见,只有慢区域显示 fallback
  const res = new Resource('posts', 300, '42 posts');
  const first = renderWithSuspenseBoundary(res, t0);
  check('有边界:首屏 shell 完整(header/nav/aside/footer 都在)', first.shell.length === 4 && first.content === null,
    first.shell.join(''));
  check('有边界:只有该区域显示 fallback', first.fallback === '<skeleton class="posts">', String(first.fallback));
  check('边界捕获的是 thenable 本身(不是错误)', res.status === 'pending', res.status);

  // C. 数据就绪后重试同一个边界
  const second = renderWithSuspenseBoundary(res, 300);
  check('数据就绪:fallback 消失、内容出现', second.fallback === null && second.content === 'posts:42 posts', String(second.content));
  check('readCount = 2 → 渲染了两次(挂起 1 次 + 重试 1 次)', res.readCount === 2, String(res.readCount));
  check('Resource 状态机 pending → resolved', res.status === 'resolved', res.status);
  check('兄弟节点始终未被卸载(没有整页闪屏)', first.shell.join('|') === second.shell.join('|'), '');
}

const sections = [
  ['§1 Lane 位掩码代数', runLaneAlgebra],
  ['§2 优先级映射 + 过期升级(防饿死)', runScheduling],
  ['§3 时间切片', runTimeSlicing],
  ['§4 transition 被打断 → 丢弃', runTransitionInterrupt],
  ['§5 Suspense 边界', runSuspense],
];

console.log('=== React 并发渲染最小实现 自检 ===');
let cursor = 0;
for (const [title, fn] of sections) {
  console.log('\n' + title);
  const before = results.length;
  fn();
  for (const r of results.slice(before)) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.name}${r.detail ? '  [' + r.detail + ']' : ''}`);
    cursor++;
  }
}
const failed = results.filter((r) => !r.ok);
console.log(`\n合计 ${results.length} 项断言,通过 ${results.length - failed.length},失败 ${failed.length}`);
if (failed.length > 0) {
  failed.forEach((f) => console.log('  FAIL → ' + f.name));
  process.exit(1);
}
console.log('全部通过 ✅');
