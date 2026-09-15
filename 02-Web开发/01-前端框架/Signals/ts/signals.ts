/**
 * Signals (push-pull 细粒度响应式)— TypeScript 类型化版本
 *
 * 与 js/signals.js 等价。类型的关键作用是把「Computed 四状态 / Watcher 三状态」钉成
 * 字面量联合 —— 这正是提案规范化最容易出错的地方(状态机不能靠注释约定)。
 *
 * 本机无 tsc,未实际编译(与仓库其它 ts/ 目录同策略)。权威来源见 js/signals.js 文件头。
 */

// ==================== 状态机类型 ====================

/** 提案原文用 ~clean~ / ~checked~ / ~computing~ / ~dirty~ 书写 */
export type ComputedState = 'clean' | 'checked' | 'computing' | 'dirty';
/** waiting / watching / pending */
export type WatcherState = 'waiting' | 'watching' | 'pending';

export const CLEAN: ComputedState = 'clean';
export const CHECKED: ComputedState = 'checked';
export const COMPUTING: ComputedState = 'computing';
export const DIRTY: ComputedState = 'dirty';
export const W_WAITING: WatcherState = 'waiting';
export const W_WATCHING: WatcherState = 'watching';
export const W_PENDING: WatcherState = 'pending';

export const WATCHED: unique symbol = Symbol('watched');
export const UNWATCHED: unique symbol = Symbol('unwatched');

export interface SignalLike<T> { get(): T }
export type Sink = Computed<unknown> | Watcher;

export interface StateOptions<T> {
  equals?: (a: T, b: T) => boolean;
  [WATCHED]?: () => void;
  [UNWATCHED]?: () => void;
}
export interface ComputedOptions<T> extends StateOptions<T> {}

/** 全局状态:computing 指向当前正在求值的 computed,frozen 表示回调执行中(禁止读写信号) */
export interface GlobalState {
  computing: Computed<unknown> | null;
  frozen: boolean;
  generation: number;
}

export const globalState: GlobalState = { computing: null, frozen: false, generation: 0 };

// ==================== State ====================

export class State<T> implements SignalLike<T> {
  value: T;
  equals: (a: T, b: T) => boolean;
  /** 有序集合:依赖它的 Computed / Watcher */
  sinks = new Set<Sink>();
  callbackWatched: (() => void) | null;
  callbackUnwatched: (() => void) | null;

  constructor(value: T, options: StateOptions<T> = {}) {
    this.value = value;
    this.equals = options.equals ?? Object.is;
    this.callbackWatched = options[WATCHED] ?? null;
    this.callbackUnwatched = options[UNWATCHED] ?? null;
  }

  get(): T {
    if (globalState.frozen) throw new Error('frozen: 回调内不能读写信号');
    if (globalState.computing) globalState.computing.sources.add(this);   // 自动依赖收集
    return this.value;
  }

  /** 值相同 → 返回早退(提案的 "set Signal value" 返回 ~clean~ 时不传播) */
  set(newValue: T): void {
    if (globalState.frozen) throw new Error('frozen: 回调内不能写信号');
    let same: boolean;
    try { same = this.equals(this.value, newValue); } catch { same = false; }
    if (same) return;
    this.value = newValue;
    propagate(this, true);
  }
}

// ==================== Computed ====================

export class Computed<T> implements SignalLike<T> {
  state: ComputedState = DIRTY;            // 初始:从未求值
  value: T | undefined = undefined;
  error: unknown = null;
  hasValue = false;
  sources = new Set<State<unknown> | Computed<unknown>>();
  sinks = new Set<Sink>();
  runs = 0;                                // 自检用:回调实际执行次数
  callbackWatched: (() => void) | null;
  callbackUnwatched: (() => void) | null;

  constructor(
    private readonly callback: (this: Computed<T>) => T,
    private readonly equals: (a: T, b: T) => boolean = Object.is,
    options: ComputedOptions<T> = {},
  ) {
    this.callbackWatched = options[WATCHED] ?? null;
    this.callbackUnwatched = options[UNWATCHED] ?? null;
  }

  get(): T {
    if (globalState.frozen) throw new Error('frozen: 回调内不能读写信号');
    if (this.state === COMPUTING) throw new Error('递归读取 computed(环形依赖)');
    if (this.state === CLEAN) return this.read();
    if (this.state === CHECKED) {
      // 逐个确认直接源 —— 等价于"拓扑排序 + 去重"这一步
      for (const src of [...this.sources]) {
        src.get();
        if (this.state === DIRTY) break;
      }
      if (this.state === CHECKED) this.state = CLEAN;    // 转换 6
    }
    if (this.state === DIRTY) this.recompute();
    return this.read();
  }

  private read(): T {
    if (globalState.computing) globalState.computing.sources.add(this);
    if (this.error) throw this.error;                    // 错误被缓存并重抛
    return this.value as T;
  }

  private recompute(): void {
    const outer = globalState.computing;
    const prevSources = this.sources;
    const hadValue = this.hasValue;
    const prevValue = this.value;
    const prevError = this.error;

    this.state = COMPUTING;                              // 转换 4
    globalState.computing = this as Computed<unknown>;
    this.sources = new Set();
    let value: T | undefined;
    let error: unknown = null;
    try { value = this.callback.call(this as Computed<T>); } catch (e) { error = e; }
    globalState.computing = outer;
    globalState.generation++;

    this.runs++;
    this.error = error;
    if (!error) this.value = value;
    this.hasValue = true;
    this.state = CLEAN;                                  // 转换 5

    for (const src of prevSources) if (!this.sources.has(src)) src.sinks.delete(this as Sink);
    for (const src of this.sources) src.sinks.add(this as Sink);

    const changed = !hadValue || (error ? prevError !== error : !this.equals(prevValue as T, value as T));
    if (changed) {
      for (const sink of this.sinks) {
        if (sink instanceof Computed) {
          if (sink.state === CLEAN || sink.state === CHECKED) sink.state = DIRTY;   // 转换 1(含本实现的修正)
        } else if (sink.state === W_WATCHING) {
          sink.state = W_PENDING;
        }
      }
    } else {
      cleanupChecked(this.sinks);
    }
  }
}

/** 直接 sink 是否连 checked 一起置 dirty(默认 true;false = 提案原文的宽松语义) */
export const config = { directCheckedToDirty: true };

/** 推:直接 sink → dirty,间接 sink → checked,Watcher → pending;然后同步 notify(frozen=true) */
export function propagate(source: State<unknown> | Computed<unknown>, direct: boolean): void {
  const watchers: Watcher[] = [];
  const visit = (node: State<unknown> | Computed<unknown>, isDirect: boolean): void => {
    for (const sink of node.sinks) {
      if (sink instanceof Computed) {
        if (isDirect) {
          const canDirty = config.directCheckedToDirty ? sink.state !== DIRTY : sink.state === CLEAN;
          if (canDirty) sink.state = DIRTY;
        } else if (sink.state === CLEAN) {
          sink.state = CHECKED;                          // 转换 3
        }
        visit(sink as Computed<unknown>, false);
      } else {
        if (sink.state === W_WATCHING) sink.state = W_PENDING;
        if (!watchers.includes(sink)) watchers.push(sink);
      }
    }
  };
  visit(source, direct);

  const errors: unknown[] = [];
  for (const w of watchers) {
    globalState.frozen = true;
    try { w.notify(); } catch (e) { errors.push(e); } finally { globalState.frozen = false; }
    w.state = W_WAITING;
  }
  if (errors.length === 1) throw errors[0];
  if (errors.length > 1) throw new AggregateError(errors);
}

/** 确认不陈旧的 checked sink 回到 clean,并递归向上(转换 6 的连带清理) */
export function cleanupChecked(sinks: Iterable<Sink>): void {
  for (const sink of sinks) {
    if (!(sink instanceof Computed) || sink.state !== CHECKED) continue;
    const allClean = [...sink.sources].every((s) => !(s instanceof Computed) || s.state === CLEAN);
    if (allClean) { sink.state = CLEAN; cleanupChecked(sink.sinks); }
  }
}

// ==================== Watcher ====================

export class Watcher {
  state: WatcherState = W_WAITING;
  signals = new Set<State<unknown> | Computed<unknown>>();

  constructor(readonly notify: (this: Watcher) => void) {}

  watch(...signals: (State<unknown> | Computed<unknown>)[]): void {
    for (const s of signals) {
      if (this.signals.has(s)) continue;
      this.signals.add(s);
      const wasFirstSink = s.sinks.size === 0;
      s.sinks.add(this);
      if (wasFirstSink) markWatched(s);                 // 第一个 sink → 递归向上传播 + watched 回调
    }
    if (this.state === W_WAITING) this.state = W_WATCHING;   // 转换 1
  }

  unwatch(...signals: (State<unknown> | Computed<unknown>)[]): void {
    for (const s of signals) {
      if (!this.signals.delete(s)) continue;
      s.sinks.delete(this);
      if (s.sinks.size === 0) markUnwatched(s);
    }
    if (this.signals.size === 0) this.state = W_WAITING;      // 转换 2
  }

  /** 返回处于 dirty / checked 的 Computed 子集(State 不在其中 —— State 的当前值永远是最新的) */
  getPending(): Computed<unknown>[] {
    const out: Computed<unknown>[] = [];
    for (const s of this.signals) {
      if (s instanceof Computed && (s.state === DIRTY || s.state === CHECKED)) out.push(s);
    }
    return out;
  }
}

function markWatched(signal: State<unknown> | Computed<unknown>): void {
  const cb = (signal as { callbackWatched?: (() => void) | null }).callbackWatched;
  if (cb) { globalState.frozen = true; try { cb(); } finally { globalState.frozen = false; } }
  const sources = signal instanceof Computed ? signal.sources : [];
  for (const src of sources) if (src.sinks.size === 0) markWatched(src);
}

function markUnwatched(signal: State<unknown> | Computed<unknown>): void {
  const cb = (signal as { callbackUnwatched?: (() => void) | null }).callbackUnwatched;
  if (cb) { globalState.frozen = true; try { cb(); } finally { globalState.frozen = false; } }
  const sources = signal instanceof Computed ? signal.sources : [];
  for (const src of sources) if (src.sinks.size === 0) markUnwatched(src);
}

// ==================== subtle 命名空间(对应 Signal.subtle) ====================

export const subtle = {
  /** 禁用依赖追踪地执行回调(恢复时即使抛错也要还原 computing) */
  untrack<T>(cb: () => T): T {
    const outer = globalState.computing;
    globalState.computing = null;
    try { return cb(); } finally { globalState.computing = outer; }
  },
  currentComputed: (): Computed<unknown> | null => globalState.computing,
  introspectSources: (s: Computed<unknown>): (State<unknown> | Computed<unknown>)[] => [...s.sources],
  introspectSinks: (s: State<unknown> | Computed<unknown>): Sink[] => [...s.sinks],
  hasSinks: (s: State<unknown> | Computed<unknown>): boolean => s.sinks.size > 0,
  hasSources: (s: Computed<unknown>): boolean => s.sources.size > 0,
  Watcher,
  watched: WATCHED,
  unwatched: UNWATCHED,
};

export const Signal = { State, Computed, subtle };
