/**
 * Turbopack 增量计算引擎 (turbo-tasks) 最小实现 (JavaScript)
 *   value cell(Vc) + 函数级记忆化 + 读时依赖跟踪 + 内容相等短路 + 需求驱动 + 文件系统缓存 + 聚合图
 *
 * 权威来源(实际读过):
 *   - https://nextjs.org/blog/turbopack-incremental-computation —— value cell 像电子表格单元;await 时才登记依赖
 *     (比 top-down memoization 更细);mark dirty 自底向上传播;cell 内容相等就跳过更新;需求驱动(defer until
 *     active query);聚合图避免遍历几十万节点。灵感来自 Salsa / Rust query system / Adapton / Parcel
 *   - https://turbopack-rust-docs.vercel.sh/rustdoc/turbo_tasks —— 四原语 functions/values/traits/collectibles;
 *     Task = 函数 + 参数(ArgIds);Vc = 值单元;每个 task 都跑在 Tokio 上
 *   - https://turbo.build/pack/docs —— 统一图 / 开发期仍打包 / 函数级缓存 + 磁盘持久化 / 懒打包
 *   - https://rolldown.rs/ —— Rolldown(Rollup 兼容 API,19k 模块基准),Vite 8+ 默认打包器
 *
 * 简化:用 JS 对象模拟 Vc 与版本号,不做多线程,文件系统缓存序列化成可比较结构而非真落盘;
 *       脏标记用"版本戳 + 读时惰性校验"实现(真实是 eager mark-dirty 调度 + 惰性验证,可观察结果一致)。
 * 运行入口见同目录 turbo_check.js
 */

'use strict';

// ==================== §1 引擎:value cell(Vc) + 函数级记忆化 ====================

class Engine {
  /** contentEquality=false 退化成朴素基线:任务一重跑就把输出标为新版本,下游无条件失效;用来量化短路收益 */
  constructor({ contentEquality = true } = {}) {
    this.contentEquality = contentEquality;
    this.cells = new Map();   // cellId → { value, version }
    this.tasks = new Map();   // taskKey → task
    this.stack = [];          // 正在执行的 task 栈:read 时据此登记依赖
    this.stats = {
      taskRuns: 0,           // 函数真正执行次数 —— 唯一值得优化的指标
      cacheHits: 0,          // 依赖版本全都没变 → 直接返回缓存值
      cellReads: 0,
      cellVersionBumps: 0,   // 内容真的变了才 +1
      cellWritesSkipped: 0,  // 写回时内容相等 → 跳过(不脏化下游)
      equalOutputsReused: 0, // 重跑后输出与旧值相等 → 复用旧值对象
    };
  }

  cell(id) {
    if (!this.cells.has(id)) this.cells.set(id, { value: undefined, version: 0 });
    return this.cells.get(id);
  }

  /**
   * 外部输入(文件 watcher / 上游任务)写入。两种短路必须分清:
   *   1) 引用相同 —— 压根没有"写"发生,任何实现都不该 bump(不是优化,是常识);
   *   2) 内容相等 —— turbo-tasks 的额外优化:结构比较发现没变,于是不脏化下游。
   */
  write(id, value) {
    const cell = this.cell(id);
    if (cell.version > 0) {
      if (cell.value === value) { this.stats.cellWritesSkipped++; return false; }
      if (this.contentEquality && equalValue(cell.value, value)) { this.stats.cellWritesSkipped++; return false; }
    }
    cell.value = value;
    cell.version++;
    this.stats.cellVersionBumps++;
    return true;
  }

  /** 等价于 await Vc:只有真正被读到的 cell 才成为依赖 */
  read(id) {
    const current = this.stack[this.stack.length - 1];
    if (current) current.readCells.add(id);
    this.stats.cellReads++;
    return this.cell(id).value;
  }

  /**
   * #[turbo_tasks::function] 的等价物。task 实例身份 = 函数名 + 参数(ArgIds);
   * 失效判定是"上次读过的 cell 版本是否还是原值"—— 比按文件哈希做的 top-down
   * memoization 更细:没被读到的 cell 变了不影响本任务。
   */
  run(key, argIds, fn) {
    let task = this.tasks.get(key);
    if (!task) {
      task = {
        key, argIds: new Set(argIds), readCells: new Set(), seen: new Map(),
        value: undefined, hasValue: false, runs: 0,
      };
      this.tasks.set(key, task);
    }
    if (task.hasValue) {
      let stale = false;
      for (const id of task.readCells) {
        if (task.seen.get(id) !== this.cell(id).version) { stale = true; break; }
      }
      if (!stale) { this.stats.cacheHits++; return task.value; }
    }

    const prev = task.value;
    const hadValue = task.hasValue;
    task.readCells = new Set();
    this.stack.push(task);
    this.stats.taskRuns++;
    task.runs++;
    const value = fn((id) => this.read(id));
    this.stack.pop();
    task.seen = new Map();
    for (const id of task.readCells) task.seen.set(id, this.cell(id).version);
    task.hasValue = true;

    // 内容相等 → 保留旧值对象、不重写 cell,下游的版本校验自然命中
    if (hadValue && this.contentEquality && equalValue(prev, value)) {
      this.stats.equalOutputsReused++;
      return task.value;
    }
    task.value = value;
    return value;
  }

  /** 从"某个 cell 变了"出发求受影响的 task 集合(task 的输出 cell = 'out:' + key) */
  affectedFrom(changedCellId) {
    const affected = new Set();
    const queued = new Set([changedCellId]);
    const queue = [changedCellId];
    while (queue.length > 0) {
      const cellId = queue.shift();
      for (const task of this.tasks.values()) {
        if (!task.readCells.has(cellId) || affected.has(task.key)) continue;
        affected.add(task.key);
        const out = 'out:' + task.key;
        if (!queued.has(out)) { queued.add(out); queue.push(out); }
      }
    }
    return affected;
  }

  /** 文件系统缓存:真实实现把 cell 值 + 依赖版本落盘 */
  serialize() {
    return JSON.stringify({
      contentEquality: this.contentEquality,
      cells: [...this.cells].map(([id, c]) => [id, c.value, c.version]),
      tasks: [...this.tasks].map(([k, t]) => [k, t.value, [...t.seen], t.hasValue]),
    });
  }

  static deserialize(json) {
    const data = JSON.parse(json);
    const engine = new Engine({ contentEquality: data.contentEquality });
    for (const [id, value, version] of data.cells) engine.cells.set(id, { value, version });
    for (const [key, value, seen, hasValue] of data.tasks) {
      engine.tasks.set(key, {
        key, argIds: new Set(), readCells: new Set(seen.map(([id]) => id)),
        seen: new Map(seen), value, hasValue, runs: 0,
      });
    }
    return engine;
  }
}

function equalValue(a, b) {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

// ==================== §2 一条真实感的构建流水线 ====================

const SOURCES = {
  'src/index.js': "import a from './a.js';\nimport b from './b.js';\nexport default a + b;\n",
  'src/a.js': 'export default 1;\n',
  'src/b.js': "import c from './c.js';\nexport default c + 1;\n",
  'src/c.js': 'export default 2;\n',
  'src/admin.js': "import u from './unused.js';\nexport default u;\n",
  'src/unused.js': 'export default 99;\n',
};

function createProject(sources, opts) {
  const engine = new Engine(opts);
  for (const [path, code] of Object.entries(sources)) engine.write(path, code);
  return engine;
}

/** 相对路径解析:模块身份 = 不带 src/ 前缀的相对路径 */
function resolve(importer, spec) {
  const dir = importer.includes('/') ? importer.slice(0, importer.lastIndexOf('/')) : '';
  const parts = (dir ? dir + '/' + spec : spec).split('/');
  const out = [];
  for (const part of parts) {
    if (part === '' || part === '.') continue;
    if (part === '..') out.pop();
    else out.push(part);
  }
  return out.join('/');
}

/** parse:只读自己的源文件 cell(依赖是"这一个文件"不是"整棵子树");输出刻意不含字节数,注释改动不该脏化下游 */
function taskParse(engine, file) {
  const ast = engine.run('parse:' + file, ['src/' + file], (read) => {
    const code = read('src/' + file);
    const imports = [...code.matchAll(/from\s+'([^']+)'/g)].map((m) => resolve(file, m[1]));
    const literals = [...code.matchAll(/export default (\d+)/g)].map((m) => Number(m[1]));
    return { file, imports, literals };
  });
  engine.write('out:parse:' + file, ast);
  return ast;
}

/** transform:读 parse 的输出 cell。产物只取决于依赖个数 → 改字面量不会改变产物字节 */
function taskTransform(engine, file) {
  const out = engine.run('transform:' + file, ['out:parse:' + file], (read) => {
    const ast = read('out:parse:' + file);
    return { file, deps: ast.imports.length, code: `compiled(${ast.imports.length} deps)` };
  });
  engine.write('out:transform:' + file, out);
  return out;
}

/** graph:递归读各模块的 parse 输出 cell(读时跟踪 → 只依赖真正走到的模块) */
function taskGraph(engine, entry) {
  const graph = engine.run('graph:' + entry, ['out:parse:' + entry], (read) => {
    const seen = new Set();
    const walk = (file) => {
      if (seen.has(file)) return;
      seen.add(file);
      for (const dep of read('out:parse:' + file).imports) walk(dep);
    };
    walk(entry);
    return { entry, modules: [...seen].sort() };
  });
  engine.write('out:graph:' + entry, graph);
  return graph;
}

/** bundle:读 graph 输出 cell + 各模块 transform 输出。依赖集合由 graph 决定,不重扫源码 */
function taskBundle(engine, entry) {
  const bundle = engine.run('bundle:' + entry, ['out:graph:' + entry], (read) => {
    const graph = read('out:graph:' + entry);
    const modules = graph.modules.map((file) => read('out:transform:' + file));
    return {
      entry,
      moduleCount: modules.length,
      size: modules.reduce((n, m) => n + m.code.length, 0),
    };
  });
  engine.write('out:bundle:' + entry, bundle);
  return bundle;
}

/** 需求驱动:只解析真正可达的模块(懒打包 —— 没人请求的入口一行都不跑) */
function parseModule(engine, file, seen = new Set()) {
  if (seen.has(file)) return seen;
  seen.add(file);
  const ast = taskParse(engine, file);
  for (const dep of ast.imports) parseModule(engine, dep, seen);
  return seen;
}

/** 一次完整构建:parse(按需) → graph → transform → bundle */
function buildEntry(engine, entry) {
  parseModule(engine, entry);
  const graph = taskGraph(engine, entry);
  for (const file of graph.modules) taskTransform(engine, file);
  return taskBundle(engine, entry);
}

// ==================== §3 聚合图(避免遍历整棵依赖图) ====================

/**
 * 真实 Turbopack 的依赖图可达几十万~上百万节点,"等子树完成""收集 diagnostics"
 * 都不能靠全图遍历。做法:与依赖图并行维护聚合图,高层节点汇总更多子节点、分辨率更低。
 */
class AggregationGraph {
  constructor() {
    this.leaves = new Map();   // module → 自身 diagnostics
    this.chunks = new Map();   // 入口 → 该子树全部 diagnostics
  }

  record(module, diagnostics) { this.leaves.set(module, diagnostics); }

  /** 聚合层:入口节点直接持有整棵子树的 diagnostics(增量维护,查询时不再遍历) */
  build(entry, modules) {
    const all = [];
    for (const m of modules) all.push(...(this.leaves.get(m) || []));
    this.chunks.set(entry, all);
    return all;
  }

  /** 朴素做法:每次查询都遍历整棵依赖图 */
  collectNaive(modules) {
    let visits = 0;
    const found = [];
    for (const m of modules) { visits++; found.push(...(this.leaves.get(m) || [])); }
    return { diagnostics: found, visits };
  }

  /** 聚合做法:直接读入口聚合节点 —— 访问次数与模块数无关 */
  collectAggregated(entry) {
    const visits = 1;
    return { diagnostics: this.chunks.get(entry) || [], visits };
  }
}

module.exports = {
  Engine, equalValue, SOURCES, createProject, resolve,
  taskParse, taskTransform, taskGraph, taskBundle, parseModule, buildEntry, AggregationGraph,
};
