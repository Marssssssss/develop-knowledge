/**
 * Signals 最小实现 — push-pull 细粒度响应式 (JavaScript)
 *   按 TC39 proposal-signals 的算法描述实现:State / Computed / subtle.Watcher + 4 状态机
 *
 * 权威来源(实际读过):
 *   - https://github.com/tc39/proposal-signals — API 表面、push-pull 构造、Computed 的
 *     ~clean~/~checked~/~computing~/~dirty~ 四状态与转换表、Watcher 三状态、glitch-free / lossy 官方 FAQ、失效传播逐步算法
 *   - https://willybrauner.com/journal/signal-the-push-pull-based-algorithm — 全局 STACK 实现自动依赖收集与 dirty flag 的推导
 *   - https://apnahive.com/javascript-signals-the-reactivity-primitive-coming-to-the-web-platform — push-then-pull 定位
 *
 * 一处刻意偏离提案原文(见 README):State.set 时直接 sink 从 checked 也一并置 dirty,否则会读到陈旧值。
 * 运行入口见同目录 signals_check.js
 */

'use strict';

// ==================== 全局状态(提案 §"Signals 全局状态") ====================

const globalState = {
  computing: null,     // 当前正在求值的 computed/effect;null 表示不在求值中
  frozen: false,       // true 时回调正在跑,禁止读写信号(防图结构被改坏)
  generation: 0,       // 递增计数,用于避免环形依赖
};

const CLEAN = 'clean';           // 有值且确定不陈旧
const CHECKED = 'checked';       // 间接源变了,值可能有但需确认
const COMPUTING = 'computing';   // 回调执行中
const DIRTY = 'dirty';           // 值已知陈旧,或从未求值过

const W_WAITING = 'waiting';     // 刚创建 / notify 已跑完,没有在监视任何信号
const W_WATCHING = 'watching';   // 正在监视,且尚无需要 notify 的变化
const W_PENDING = 'pending';     // 依赖变了,但 notify 还没跑

/** 开关:直接 sink 从 checked 是否也置 dirty。默认 true;置 false 即提案原文(会出现读到陈旧值,见 signals_check.js §2) */
const config = { directCheckedToDirty: true };

/** subtle 命名空间用的符号(对应 Signal.subtle.watched / Signal.subtle.unwatched) */
const WATCHED = Symbol('watched');
const UNWATCHED = Symbol('unwatched');

// ==================== State ====================

class State {
  constructor(value, options = {}) {
    this.value = value;
    this.equals = options.equals || Object.is;   // 默认 Object.is
    this.sinks = new Set();                      // 有序:依赖它的 Computed / Watcher
    this[WATCHED] = options[WATCHED] || null;
    this[UNWATCHED] = options[UNWATCHED] || null;
    this.isState = true;
  }

  get() {
    if (globalState.frozen) throw new Error('frozen: 回调内不能读写信号');
    if (globalState.computing) globalState.computing.sources.add(this);   // 自动依赖收集
    return this.value;
  }

  set(newValue) {
    if (globalState.frozen) throw new Error('frozen: 回调内不能写信号');
    let same;
    try { same = this.equals(this.value, newValue); } catch { same = false; }
    if (same) return;                       // "set Signal value" 返回 ~clean~ → 不传播
    this.value = newValue;
    propagate(this);
  }
}

// ==================== Computed ====================

class Computed {
  constructor(callback, options = {}) {
    this.callback = callback;
    this.equals = options.equals || Object.is;
    this.value = undefined;
    this.error = null;
    this.hasValue = false;
    this.state = DIRTY;                     // 初始：从未求值
    this.sources = new Set();                // 有序(读顺序可观察)
    this.sinks = new Set();
    this.runs = 0;                           // 自检用:回调实际执行次数
    this[WATCHED] = options[WATCHED] || null;
    this[UNWATCHED] = options[UNWATCHED] || null;
    this.isState = false;
  }

  get() {
    if (globalState.frozen) throw new Error('frozen: 回调内不能读写信号');
    if (this.state === COMPUTING) throw new Error('递归读取 computed(环形依赖)');
    if (this.state === CLEAN) return this._read();
    if (this.state === CHECKED) {
      // 逐个确认直接源:这就是"拓扑排序 + 去重"的那一步
      for (const src of [...this.sources]) {
        src.get();
        if (this.state === DIRTY) break;    // 直接源确认变化 → 不必继续确认
      }
      if (this.state === CHECKED) this.state = CLEAN;   // 转换 6:所有直接源都没变
    }
    if (this.state === DIRTY) this._recompute();
    return this._read();
  }

  _read() {
    if (globalState.computing) globalState.computing.sources.add(this);
    if (this.error) throw this.error;        // 错误被缓存,读到就重抛
    return this.value;
  }

  _recompute() {
    const outerComputing = globalState.computing;
    const prevSources = this.sources;
    const hadValue = this.hasValue;
    const prevValue = this.value;
    const prevError = this.error;

    this.state = COMPUTING;                  // 转换 4
    globalState.computing = this;
    this.sources = new Set();
    let value;
    let error = null;
    try { value = this.callback.call(this); } catch (e) { error = e; }
    globalState.computing = outerComputing;
    globalState.generation++;

    this.runs++;
    this.error = error;
    if (!error) this.value = value;
    this.hasValue = true;
    this.state = CLEAN;                      // 转换 5:回调结束(无论返回还是抛出)

    this._syncSources(prevSources);
    const changed = !hadValue || (error ? prevError !== error : !this.equals(prevValue, value));
    if (changed) {
      for (const sink of this.sinks) {
        if (sink instanceof Computed) {
          if (sink.state === CLEAN || sink.state === CHECKED) sink.state = DIRTY;   // 转换 1
        } else if (sink.state === W_WATCHING) {
          sink.state = W_PENDING;
        }
      }
    } else {
      cleanupChecked(this.sinks);
    }
  }

  /** 依赖可能每次执行都不同(v-if/短路等):重连边,旧的源上要摘掉自己 */
  _syncSources(prevSources) {
    for (const src of prevSources) if (!this.sources.has(src)) src.sinks.delete(this);
    for (const src of this.sources) src.sinks.add(this);
  }
}

// ==================== 失效传播(推) ====================

function propagate(source) {
  const watchers = [];
  // 直接 sink → dirty;间接 sink → checked;Watcher → pending(都只在"更干净"的状态下推进)
  const visit = (node, direct) => {
    for (const sink of node.sinks) {
      if (sink instanceof Computed) {
        if (direct) {
          // 提案原文:previously clean → dirty。这里(默认)把 checked 一并置 dirty,见文件头说明
          const canDirty = config.directCheckedToDirty ? sink.state !== DIRTY : sink.state === CLEAN;
          if (canDirty) sink.state = DIRTY;
        } else if (sink.state === CLEAN) {
          sink.state = CHECKED;              // 转换 3
        }
        visit(sink, false);
      } else {
        if (sink.state === W_WATCHING) sink.state = W_PENDING;
        if (!watchers.includes(sink)) watchers.push(sink);
      }
    }
  };
  visit(source, true);

  // Watcher 的 notify 同步执行(frozen=true),深度优先;异常聚合后抛给调用方
  const errors = [];
  for (const w of watchers) {
    globalState.frozen = true;
    try { w.notify(); } catch (e) { errors.push(e); } finally { globalState.frozen = false; }
    w.state = W_WAITING;
  }
  if (errors.length === 1) throw errors[0];
  if (errors.length > 1) throw new AggregateError(errors);
}

/** 转换:确认不陈旧的那部分 checked sink 可以回到 clean,并递归向上 */
function cleanupChecked(sinks) {
  for (const sink of sinks) {
    if (!(sink instanceof Computed) || sink.state !== CHECKED) continue;
    const allClean = [...sink.sources].every((s) => s instanceof Computed ? s.state === CLEAN : true);
    if (allClean) { sink.state = CLEAN; cleanupChecked(sink.sinks); }
  }
}

// ==================== Watcher ====================

class Watcher {
  constructor(notify) {
    if (typeof notify !== 'function') throw new TypeError('Watcher 需要 notify 回调');
    this.notify = notify;
    this.state = W_WAITING;
    this.signals = new Set();
  }

  watch(...signals) {
    for (const s of signals) {
      if (this.signals.has(s)) continue;
      this.signals.add(s);
      const wasFirstSink = s.sinks.size === 0;
      s.sinks.add(this);
      if (wasFirstSink) markWatched(s);      // 第一个 sink → 递归向上传播"被监视"状态
    }
    if (this.state === W_WAITING) this.state = W_WATCHING;   // 转换 1
  }

  unwatch(...signals) {
    for (const s of signals) {
      if (!this.signals.delete(s)) continue;
      s.sinks.delete(this);
      if (s.sinks.size === 0) markUnwatched(s);
    }
    if (this.signals.size === 0) this.state = W_WAITING;     // 转换 2
  }

  /** 返回处于 dirty / checked 的 Computed 子集(State 不在其中) */
  getPending() {
    return [...this.signals].filter((s) => s instanceof Computed && (s.state === DIRTY || s.state === CHECKED));
  }
}

function markWatched(signal) {
  if (signal[WATCHED]) {
    globalState.frozen = true;
    try { signal[WATCHED](); } finally { globalState.frozen = false; }
  }
  for (const src of signal.sources || []) {
    const wasFirstSink = src.sinks.size === 0;
    if (wasFirstSink) markWatched(src);
  }
}

function markUnwatched(signal) {
  if (signal[UNWATCHED]) {
    globalState.frozen = true;
    try { signal[UNWATCHED](); } finally { globalState.frozen = false; }
  }
  for (const src of signal.sources || []) if (src.sinks.size === 0) markUnwatched(src);
}

// ==================== subtle ====================

const subtle = {
  untrack(cb) {
    const outer = globalState.computing;
    globalState.computing = null;
    try { return cb(); } finally { globalState.computing = outer; }
  },
  currentComputed() { return globalState.computing; },
  introspectSources(s) { return [...(s.sources || [])]; },
  introspectSinks(s) { return [...(s.sinks || [])]; },
  hasSinks: (s) => s.sinks.size > 0,
  hasSources: (s) => (s.sources ? s.sources.size > 0 : false),
  Watcher,
  watched: WATCHED,
  unwatched: UNWATCHED,
};

const Signal = { State, Computed, subtle };

// ==================== 对照基线:纯 push 的即时重算(会有 glitch) ====================

class EagerGraph {
  constructor() { this.nodes = {}; this.runs = {}; }
  add(id, deps, compute) {
    const n = { id, deps, compute, value: undefined, sinks: [] };
    this.nodes[id] = n;
    this.runs[id] = 0;
    for (const d of deps) this.nodes[d].sinks.push(id);
    if (compute) { n.value = compute((k) => this.nodes[k].value); this.runs[id]++; }
    return this;
  }
  set(id, v) {
    this.nodes[id].value = v;
    this._push(id);
    return this;
  }
  _push(id) {
    for (const s of this.nodes[id].sinks) {
      const n = this.nodes[s];
      n.value = n.compute((k) => this.nodes[k].value);   // 立即重算(可能读到还没更新的上游)
      this.runs[s]++;
      this._push(s);
    }
  }
}

module.exports = { config, Signal, State, Computed, Watcher, subtle, globalState, EagerGraph, CLEAN, CHECKED, DIRTY, COMPUTING, W_WAITING, W_WATCHING, W_PENDING, propagate };
