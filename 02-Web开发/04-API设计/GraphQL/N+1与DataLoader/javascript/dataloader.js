// dataloader.js —— 复刻 graphql/dataloader 官方 README 里"可判定"的语义。
//
// 口径来源（实读 raw.githubusercontent.com/graphql/dataloader/main/README.md）：
//   * "Batching is not an advanced feature, it's DataLoader's primary feature."
//   * 默认调度：把"单个执行帧（a single tick of the event loop）"内的所有 load 合并，
//     再一次性交给 batchLoadFn。可传 batchScheduleFn 覆盖（100ms 窗口 / 手动 dispatch）。
//   * batchLoadFn 的两条硬约束：
//       - "The Array of values must be the same length as the Array of keys."
//       - "Each index in the Array of values must correspond to the same index in Array of keys."
//     → 后端乱序返回时，batch 函数必须自己重排；缺值用 null 或 Error 实例占位。
//   * 缓存是 per-request 的 memoization，"does not replace Redis, Memcache"；
//     命中缓存的 key **不会**出现在 keys 里，但 Promise 仍等当前批次一起 resolve —— 这样
//     下游依赖 load 才会落在同一 tick，从而继续合并（官方称之为 "optimizations for
//     subsequent dependent loads"）。
//   * cache: false 时 batchLoadFn 会收到**含重复**的 keys，每个 key 对应一次 load 调用。
//   * 错误：batchLoadFn reject → 这批值都不缓存；返回 Error **实例**作为某个值 → 该 Error 会被缓存。
//   * 选项默认值：batch=true、maxBatchSize=Infinity、cache=true、cacheKeyFn=key=>key、
//     cacheMap=new Map()；batch:false 等价于 maxBatchSize:1，cache:false 等价于 cacheMap:null。
//
// 运行：node check.js

/** 默认调度器：一个微任务 = 一次 event loop tick。 */
function defaultScheduler(cb) {
  queueMicrotask(cb);
}

class DataLoader {
  constructor(batchLoadFn, options = {}) {
    if (typeof batchLoadFn !== 'function') {
      throw new TypeError('DataLoader must be constructed with a function');
    }
    const opts = options || {};
    this._batchLoadFn = batchLoadFn;
    this._batch = opts.batch !== false;
    this._maxBatchSize = opts.maxBatchSize === undefined
      ? Infinity
      : opts.maxBatchSize;
    if (this._maxBatchSize === null) this._maxBatchSize = Infinity;
    if (!this._batch) this._maxBatchSize = 1; // 官方：batch:false 等价于 maxBatchSize:1
    this._cacheEnabled = opts.cache !== false;
    this._cacheKeyFn = opts.cacheKeyFn || ((k) => k);
    this._cacheMap = this._cacheEnabled
      ? (opts.cacheMap || new Map())
      : null;
    this._scheduleFn = opts.batchScheduleFn || defaultScheduler;
    this._queue = [];
    this._scheduled = false;

    // 观测字段（非官方 API，供自检断言）
    this.batches = [];   // 每次真实调用 batchLoadFn 的 keys
    this.batchCount = 0;
  }

  load(key) {
    const cacheKey = this._cacheKeyFn(key);
    if (this._cacheMap && this._cacheMap.has(cacheKey)) {
      // 命中缓存：不进 keys，但仍要等当前批次结束才 resolve
      return Promise.resolve(this._cacheMap.get(cacheKey));
    }
    const promise = new Promise((resolve, reject) => {
      this._queue.push({ key, cacheKey, resolve, reject });
      this._maybeSchedule();
    });
    // 官方实现在 load() 时就把 promise 写进缓存，因此**同一 tick 内**的重复 key
    // 也会被去重；失败时再由 _failBatch 删掉。
    if (this._cacheMap) this._cacheMap.set(cacheKey, promise);
    return promise;
  }

  loadMany(keys) {
    return Promise.all(keys.map((k) => this.load(k)));
  }

  /** prime：预填缓存（官方 API） */
  prime(key, value) {
    if (!this._cacheMap) return this;
    const cacheKey = this._cacheKeyFn(key);
    if (!this._cacheMap.has(cacheKey)) this._cacheMap.set(cacheKey, value);
    return this;
  }

  clear(key) {
    if (this._cacheMap) this._cacheMap.delete(this._cacheKeyFn(key));
    return this;
  }

  clearAll() {
    if (this._cacheMap) this._cacheMap.clear();
    return this;
  }

  _maybeSchedule() {
    if (this._scheduled) return;
    this._scheduled = true;
    this._scheduleFn(() => this._dispatch());
  }

  _dispatch() {
    this._scheduled = false;
    if (this._queue.length === 0) return;
    const queue = this._queue;
    this._queue = [];

    const size = Number.isFinite(this._maxBatchSize)
      ? Math.max(1, Math.floor(this._maxBatchSize))
      : queue.length;
    for (let i = 0; i < queue.length; i += size) {
      const slice = queue.slice(i, i + size);
      this._runBatch(slice);
    }
  }

  _runBatch(slice) {
    const keys = slice.map((s) => s.key);
    this.batchCount += 1;
    this.batches.push(keys);
    let result;
    try {
      result = this._batchLoadFn(keys);
    } catch (err) {
      this._failBatch(slice, err);
      return;
    }
    Promise.resolve(result).then(
      (values) => {
        if (!Array.isArray(values) || values.length !== keys.length) {
          const err = new TypeError(
            'batchLoadFn must return an Array of the same length as the Array of keys'
          );
          this._failBatch(slice, err);
          return;
        }
        slice.forEach((s, i) => {
          const v = values[i];
          // 官方：Error 实例作为值返回时**会被缓存**，避免反复加载同一个错误
          if (this._cacheMap) this._cacheMap.set(s.cacheKey, v);
          s.resolve(v);
        });
      },
      (err) => this._failBatch(slice, err)
    );
  }

  /** 整批失败：清掉本批的缓存条目再 reject（官方 failedDispatch 的做法）。 */
  _failBatch(slice, err) {
    if (this._cacheMap) slice.forEach((s) => this._cacheMap.delete(s.cacheKey));
    slice.forEach((s) => s.reject(err));
  }
}

module.exports = { DataLoader, defaultScheduler };
