/**
 * Signals / push-pull 细粒度响应式 — 自检入口 (JavaScript)
 * 模型见同目录 signals.js。运行:node signals_check.js(全部通过 exit 0)
 */

'use strict';

const S = require('./signals.js');
const { Signal, State, Computed, subtle, config, EagerGraph, CLEAN, CHECKED, DIRTY, COMPUTING, W_WAITING, W_WATCHING, W_PENDING } = S;

const results = [];
function check(name, cond, detail) { results.push({ name, ok: !!cond, detail }); }

// ---------------- §1 核心语义 ----------------
function checkSemantics() {
  // 1. 惰性:声明不求值
  const a = new State(2);
  const b = new Computed(() => a.get() * 2);
  check('惰性求值:声明 computed 不执行回调', b.runs === 0, `runs=${b.runs}`);
  check('初次 get 才求值', b.get() === 4 && b.runs === 1, `runs=${b.runs}`);
  check('记忆化:重复读不重算', b.get() === 4 && b.get() === 4 && b.runs === 1, `runs=${b.runs}`);
  check('依赖值不变 → 不重算', (a.set(2), b.get() === 4 && b.runs === 1), `runs=${b.runs}`);
  check('依赖值变化 → 重算一次', (a.set(3), b.get() === 6 && b.runs === 2), `runs=${b.runs}`);

  // 2. 自定义 equals
  const objState = new State({ v: 1 }, { equals: (x, y) => x.v === y.v });
  const objComputed = new Computed(() => objState.get().v + 1);
  objComputed.get();
  objState.set({ v: 1 });
  check('自定义 equals:语义相等则不传播', objComputed.runs === 1, `runs=${objComputed.runs}`);
  objState.set({ v: 2 });
  check('自定义 equals:不等则重算', objComputed.get() === 3 && objComputed.runs === 2, `runs=${objComputed.runs}`);

  // 3. lossy:连续两次写,第一次被"丢失"
  const ls = new State(1);
  const observed = [];
  const lc = new Computed(() => { const v = ls.get() * 10; observed.push(v); return v; });
  lc.get();
  ls.set(2);
  ls.set(3);
  const lossyFinal = lc.get();
  check('lossy:中间写入不触发求值(第一次写被丢弃)', observed.join(',') === '10,30', observed.join(','));
  check('lossy:最终值取最后一次写', lossyFinal === 30 && observed.length === 2, `observed=${observed.join(',')}`);

  // 4. untrack:读了但不建依赖
  const us = new State(1);
  const uc = new Computed(() => subtle.untrack(() => us.get()) + 100);
  uc.get();
  us.set(5);
  check('untrack:读取不计入依赖 → 不失效', uc.runs === 1 && uc.get() === 101, `runs=${uc.runs}`);
  check('untrack 恢复外层 computing 上下文', subtle.currentComputed() === null, '');

  // 5. 依赖顺序可观察
  const o1 = new State(1), o2 = new State(2);
  const order = [];
  const oc = new Computed(() => { order.push('o2'); const x = o2.get(); order.push('o1'); const y = o1.get(); return x + y; });
  oc.get();
  const sources = subtle.introspectSources(oc);
  check('sources 按读顺序存储(读顺序可观察)', sources[0] === o2 && sources[1] === o1, sources.map((s) => (s === o1 ? 'o1' : 'o2')).join(','));
  check('hasSources / hasSinks 可用', subtle.hasSources(oc) === true && subtle.hasSinks(o1) === true && subtle.hasSources(o1) === false, '');
  check('introspectSinks 能反向查到依赖者', subtle.introspectSinks(o1).includes(oc), '');

  // 6. 错误缓存
  const es = new State(0);
  const boom = new Error('boom');
  const ec = new Computed(() => { if (es.get() === 0) throw boom; return 'ok'; });
  let e1 = null, e2 = null;
  try { ec.get(); } catch (e) { e1 = e; }
  try { ec.get(); } catch (e) { e2 = e; }
  check('错误被缓存:回调只跑一次,读到就重抛同一对象', ec.runs === 1 && e1 === boom && e2 === boom, `runs=${ec.runs}`);
  es.set(1);
  check('依赖变化后错误被清除并重算', ec.get() === 'ok' && ec.runs === 2, `runs=${ec.runs}`);

  // 7. 动态依赖:分支切换后旧源不再触发
  const branch = new State(true);
  const left = new State('L');
  const right = new State('R');
  const dyn = new Computed(() => (branch.get() ? left.get() : right.get()));
  dyn.get();
  check('动态依赖:初始依赖 left', subtle.introspectSources(dyn).includes(left), '');
  branch.set(false);
  check('动态依赖:切换后取 right 的值', dyn.get() === 'R', '');
  const runsAfterSwitch = dyn.runs;
  left.set('L2');
  check('动态依赖:不再被走的旧分支不触发重算', dyn.runs === runsAfterSwitch, `runs=${dyn.runs}`);
  right.set('R2');
  check('动态依赖:新分支会触发重算', dyn.get() === 'R2' && dyn.runs === runsAfterSwitch + 1, `runs=${dyn.runs}`);
}

// ---------------- §2 状态机 ----------------
function checkStateMachine() {
  const a = new State(1);
  const b = new Computed(() => a.get() * 2);
  const c = new Computed(() => a.get() * 3);
  const d = new Computed(() => b.get() + c.get());
  check('computed 初始状态是 dirty(从未求值)', b.state === DIRTY, b.state);
  d.get();
  check('求值后状态是 clean', b.state === CLEAN && c.state === CLEAN && d.state === CLEAN, `${b.state}/${c.state}/${d.state}`);

  a.set(2);
  check('直接 sink → dirty,间接 sink → checked', b.state === DIRTY && c.state === DIRTY && d.state === CHECKED,
    `b=${b.state} c=${c.state} d=${d.state}`);
  check('checked 节点确认所有直接源后收敛为 clean', (b.get(), c.get(), d.get() === 10) && d.state === CLEAN, d.state);

  // 环形依赖
  let cycleReported = '';
  const cyc = new Computed(function () { return cycSelf.get(); });
  const cycSelf = cyc;
  try { cyc.get(); } catch (e) { cycleReported = e.message; }
  check('computed 内读自身 → 检测到环形依赖', /环形依赖/.test(cycleReported), cycleReported);

  // checked 的直接源被再改:两种语义的对照
  function staleScenario(directCheckedToDirty) {
    const prev = config.directCheckedToDirty;
    config.directCheckedToDirty = directCheckedToDirty;
    try {
      const x = new State(1);      // 间接源
      const y = new State(1);      // 直接源(读顺序在前)
      const mid = new Computed(() => (x.get() > 0 ? 10 : 0));
      const m = new Computed(() => y.get() + mid.get());
      m.get();                     // 11
      x.set(2);                    // x→mid→m:m 是间接 sink → checked;mid 值不变(仍 10)
      y.set(2);                    // y 是 m 的直接源
      const beforeState = m.state;
      const value = m.get();       // 正确应为 2 + 10 = 12
      return { value, beforeState };
    } finally { config.directCheckedToDirty = prev; }
  }
  const fixed = staleScenario(true);
  check('直接源变更时把 checked 也置 dirty(本实现)→ 读到正确值',
    fixed.value === 12 && fixed.beforeState === DIRTY, `value=${fixed.value} state=${fixed.beforeState}`);
  const literal = staleScenario(false);
  check('提案字面语义(只 clean→dirty)会读到陈旧值 —— 故本实现做了修正',
    literal.value === 11 && literal.beforeState === CHECKED, `value=${literal.value} state=${literal.beforeState}`);
}

// ---------------- §3 glitch-free ----------------
function checkGlitchFree() {
  // 菱形依赖:a → (b, c) → d
  const a = new State(1);
  const evalLog = [];
  const b = new Computed(() => { const v = a.get() * 2; evalLog.push(`b=${v}`); return v; });
  const c = new Computed(() => { const v = a.get() * 3; evalLog.push(`c=${v}`); return v; });
  const seenByD = [];
  const d = new Computed(() => { const v = b.get() + c.get(); seenByD.push(v); return v; });
  d.get();
  check('初始:d = b + c = 2 + 3 = 5', d.get() === 5, String(d.get()));

  a.set(2);
  evalLog.length = 0;
  seenByD.length = 0;
  const runsBefore = { b: b.runs, c: c.runs, d: d.runs };
  const v = d.get();
  check('菱形依赖:一次 pull 得到正确的 d = 4 + 6 = 10', v === 10, String(v));
  check('glitch-free:一次变更后每个节点恰好重算 1 次',
    b.runs - runsBefore.b === 1 && c.runs - runsBefore.c === 1 && d.runs - runsBefore.d === 1,
    `Δb=${b.runs - runsBefore.b} Δc=${c.runs - runsBefore.c} Δd=${d.runs - runsBefore.d}`);
  check('glitch-free:d 从未观察到"新 b + 旧 c"的中间值', !seenByD.includes(7) && seenByD.length === 1,
    '观察到 ' + seenByD.join(','));
  check('拓扑顺序:先求 b、再求 c、最后求 d', evalLog.join(' → ') === 'b=4 → c=6', evalLog.join(' → '));

  // 纯 push 基线:立即重算 → 出现 glitch
  const g = new EagerGraph();
  g.add('a', [], null);
  g.nodes.a.value = 1;
  g.add('b', ['a'], (v2) => v2('a') * 2);
  g.add('c', ['a'], (v2) => v2('a') * 3);
  const eagerSeen = [];
  g.add('d', ['b', 'c'], (v2) => { const r = v2('b') + v2('c'); eagerSeen.push(r); return r; });
  eagerSeen.length = 0;
  for (const k of Object.keys(g.runs)) g.runs[k] = 0;   // 只统计"本次变更引发的重算"
  g.set('a', 2);
  const eagerTotal = g.runs.a + g.runs.b + g.runs.c + g.runs.d;
  check('push 基线:d 被重算 2 次(每次上游变化都立即推一次)', g.runs.d === 2, `runs=${g.runs.d}`);
  check('push 基线:观察到陈旧混合值 4 + 3 = 7(glitch)', eagerSeen.includes(7), '观察到 ' + eagerSeen.join(','));

  const signalTotal = (b.runs - runsBefore.b) + (c.runs - runsBefore.c) + (d.runs - runsBefore.d);
  check('同一变更:pull 路径重算 3 次 < push 基线 4 次,且无陈旧中间值',
    signalTotal === 3 && eagerTotal === 4 && signalTotal < eagerTotal,
    `signals=${signalTotal} vs eager=${eagerTotal}`);
}

// ---------------- §4 Watcher ----------------
function checkWatcher() {
  const s = new State(1);
  const c = new Computed(() => s.get() * 10);
  c.get();

  let watchedFired = 0;
  let unwatchedFired = 0;
  const c2 = new Computed(() => s.get() + 1, {
    [subtle.watched]: () => { watchedFired++; },
    [subtle.unwatched]: () => { unwatchedFired++; },
  });
  c2.get();

  const log = [];
  const w = new subtle.Watcher(() => { log.push('notify'); w.getPending(); });
  check('Watcher 初始状态 waiting', w.state === W_WAITING, w.state);
  w.watch(c2);
  check('watch 后 → watching', w.state === W_WATCHING, w.state);
  check('首个 sink 出现 → 触发 watched 回调', watchedFired === 1, String(watchedFired));

  log.push('before-set');
  s.set(2);
  log.push('after-set');
  check('notify 在 set() 内同步执行', log.join(',') === 'before-set,notify,after-set', log.join(','));
  check('notify 跑完后 Watcher → waiting', w.state === W_WAITING, w.state);

  // frozen 语义:notify 内不能读写信号
  const s2 = new State(1);
  let frozenError = '';
  const w2 = new subtle.Watcher(() => { try { s2.get(); } catch (e) { frozenError = e.message; } });
  w2.watch(s2);
  s2.set(2);
  check('notify 内读信号抛错(frozen)', /frozen/.test(frozenError), frozenError);

  // getPending
  const s3 = new State(1);
  const c3 = new Computed(() => s3.get() * 2);
  c3.get();
  const pendingW = new subtle.Watcher(() => {});
  pendingW.watch(c3);
  s3.set(9);
  const pending = pendingW.getPending();
  check('getPending 返回 dirty/checked 的 Computed', pending.length === 1 && pending[0] === c3, String(pending.length));
  c3.get();
  check('重新求值后 getPending 为空', pendingW.getPending().length === 0, '');

  // unwatch
  w.unwatch(c2);
  check('unwatch 后 → waiting,并触发 unwatched 回调',
    w.state === W_WAITING && unwatchedFired === 1 && subtle.hasSinks(c2) === false,
    `state=${w.state} unwatched=${unwatchedFired}`);

  // 多 watcher 异常聚合
  const s4 = new State(1);
  const e1 = new Error('w1'), e2 = new Error('w2');
  const wa = new subtle.Watcher(() => { throw e1; });
  const wb = new subtle.Watcher(() => { throw e2; });
  wa.watch(s4);
  wb.watch(s4);
  let agg = null;
  try { s4.set(2); } catch (e) { agg = e; }
  check('多个 watcher 抛错 → AggregateError 聚合', agg instanceof AggregateError && agg.errors.length === 2, String(agg && agg.name));
  check('异常仍不阻止其余 watcher 执行', log.length > 0, '');
}

const sections = [
  ['§1 核心语义(惰性/记忆化/equals/lossy/untrack/错误/动态依赖)', checkSemantics],
  ['§2 状态机与传播', checkStateMachine],
  ['§3 glitch-free 拓扑求值 vs push 基线', checkGlitchFree],
  ['§4 subtle.Watcher', checkWatcher],
];

console.log('=== Signals (push-pull) 自检 ===');
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
