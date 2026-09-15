/**
 * Turbopack 增量计算引擎 (turbo-tasks) 最小实现 (TypeScript)
 *   与 js/turbo.js 同模型:value cell(Vc) + 函数级记忆化 + 读时依赖跟踪 + 内容相等短路
 *   + 需求驱动 + 文件系统缓存 + 聚合图。权威来源见 js/turbo.js 头部。
 *
 * TS 版本额外体现:cell 里存 unknown、任务边界由泛型收敛(对应真实的 `Vc<T>`);依赖关系是运行时
 * 数据(Set<CellId>),类型系统无法替你检查 —— 所以 turbo-tasks 用 trait/collectible 在运行期校验。
 */

// ==================== §1 基础类型 ====================

export type CellId = string;
export type TaskKey = string;
export type Reader = (id: CellId) => unknown;

interface Cell { value: unknown; version: number }

interface Task {
  key: TaskKey;
  argIds: Set<CellId>;    // 参数涉及的 cell(ArgIds):参数不同即不同实例
  readCells: Set<CellId>; // 上次执行时真正读到过的 cell(read-time dependency)
  seen: Map<CellId, number>; // 执行结束时的版本快照:下一次靠它判断是否过期
  value: unknown;
  hasValue: boolean;
  runs: number;
}

export interface EngineStats {
  taskRuns: number; cacheHits: number; cellReads: number;
  cellVersionBumps: number; cellWritesSkipped: number; equalOutputsReused: number;
}

/** contentEquality=false = 朴素基线:任务一重跑就标新版本,下游无条件失效 */
export interface EngineOptions { contentEquality?: boolean }

// ==================== §2 引擎 ====================

export class Engine {
  readonly contentEquality: boolean;
  private readonly cells = new Map<CellId, Cell>();
  private readonly tasks = new Map<TaskKey, Task>();
  private readonly stack: Task[] = [];
  readonly stats: EngineStats = {
    taskRuns: 0, cacheHits: 0, cellReads: 0,
    cellVersionBumps: 0, cellWritesSkipped: 0, equalOutputsReused: 0,
  };

  constructor({ contentEquality = true }: EngineOptions = {}) {
    this.contentEquality = contentEquality;
  }

  cell(id: CellId): Cell {
    let cell = this.cells.get(id);
    if (!cell) { cell = { value: undefined, version: 0 }; this.cells.set(id, cell); }
    return cell;
  }

  /** 引用相同 → 压根没有"写"发生;内容相等 → turbo-tasks 的额外短路,不脏化下游 */
  write(id: CellId, value: unknown): boolean {
    const cell = this.cell(id);
    if (cell.version > 0) {
      const identical = cell.value === value;
      const sameContent = this.contentEquality && equalValue(cell.value, value);
      if (identical || sameContent) { this.stats.cellWritesSkipped++; return false; }
    }
    cell.value = value;
    cell.version++;
    this.stats.cellVersionBumps++;
    return true;
  }

  /** 等价于 await Vc:只有真正被读到的 cell 才成为依赖 */
  read(id: CellId): unknown {
    const current = this.stack[this.stack.length - 1];
    if (current) current.readCells.add(id);
    this.stats.cellReads++;
    return this.cell(id).value;
  }

  /** #[turbo_tasks::function] 的等价物:task 身份 = key(函数名 + 参数) */
  run<T>(key: TaskKey, argIds: readonly CellId[], fn: (read: Reader) => T): T {
    let task = this.tasks.get(key);
    if (!task) {
      task = {
        key, argIds: new Set(argIds), readCells: new Set(), seen: new Map(), value: undefined, hasValue: false, runs: 0,
      };
      this.tasks.set(key, task);
    }
    if (task.hasValue) {
      let stale = false;
      for (const id of task.readCells) {
        if (task.seen.get(id) !== this.cell(id).version) { stale = true; break; }
      }
      if (!stale) { this.stats.cacheHits++; return task.value as T; }
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

    // 输出内容相等 → 保留旧值对象,下游版本校验因此命中
    if (hadValue && this.contentEquality && equalValue(prev, value)) {
      this.stats.equalOutputsReused++;
      return task.value as T;
    }
    task.value = value;
    return value;
  }

  /** 从"某个 cell 变了"出发求受影响的 task 集合(task 输出 cell = 'out:' + key) */
  affectedFrom(changedCellId: CellId): Set<TaskKey> {
    const affected = new Set<TaskKey>();
    const queued = new Set<CellId>([changedCellId]);
    const queue: CellId[] = [changedCellId];
    while (queue.length > 0) {
      const cellId = queue.shift() as CellId;
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
  serialize(): string {
    return JSON.stringify({
      contentEquality: this.contentEquality,
      cells: [...this.cells].map(([id, c]) => [id, c.value, c.version]),
      tasks: [...this.tasks].map(([k, t]) => [k, t.value, [...t.seen], t.hasValue]),
    });
  }

  static deserialize(json: string): Engine {
    const data = JSON.parse(json) as {
      contentEquality: boolean;
      cells: [CellId, unknown, number][];
      tasks: [TaskKey, unknown, [CellId, number][], boolean][];
    };
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

export function equalValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  return JSON.stringify(a) === JSON.stringify(b);
}

// ==================== §3 流水线(类型化的 task 边界) ====================

export type Sources = Record<string, string>;

export interface Ast { file: string; imports: string[]; literals: number[] }
export interface TransformOut { file: string; deps: number; code: string }
export interface GraphOut { entry: string; modules: string[] }
export interface BundleOut { entry: string; moduleCount: number; size: number }

export const SOURCES: Sources = {
  'src/index.js': "import a from './a.js';\nimport b from './b.js';\nexport default a + b;\n",
  'src/a.js': 'export default 1;\n',
  'src/b.js': "import c from './c.js';\nexport default c + 1;\n",
  'src/c.js': 'export default 2;\n',
  'src/admin.js': "import u from './unused.js';\nexport default u;\n",
  'src/unused.js': 'export default 99;\n',
};

export function createProject(sources: Sources, opts?: EngineOptions): Engine {
  const engine = new Engine(opts);
  for (const [path, code] of Object.entries(sources)) engine.write(path, code);
  return engine;
}

export function resolveSpec(importer: string, spec: string): string {
  const dir = importer.includes('/') ? importer.slice(0, importer.lastIndexOf('/')) : '';
  const out: string[] = [];
  for (const part of (dir ? dir + '/' + spec : spec).split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') out.pop(); else out.push(part);
  }
  return out.join('/');
}

/** parse:只读自己的源文件 cell;输出不含字节数,注释改动不脏化下游 */
export function taskParse(engine: Engine, file: string): Ast {
  const ast = engine.run<Ast>('parse:' + file, ['src/' + file], (read) => {
    const code = read('src/' + file) as string;
    const imports = [...code.matchAll(/from\s+'([^']+)'/g)].map((m) => resolveSpec(file, m[1]));
    const literals = [...code.matchAll(/export default (\d+)/g)].map((m) => Number(m[1]));
    return { file, imports, literals };
  });
  engine.write('out:parse:' + file, ast);
  return ast;
}

/** transform:产物只取决于依赖个数 → 改字面量不改变产物字节 */
export function taskTransform(engine: Engine, file: string): TransformOut {
  const out = engine.run<TransformOut>('transform:' + file, ['out:parse:' + file], (read) => {
    const ast = read('out:parse:' + file) as Ast;
    return { file, deps: ast.imports.length, code: `compiled(${ast.imports.length} deps)` };
  });
  engine.write('out:transform:' + file, out);
  return out;
}

/** graph:递归读各模块 parse 输出 cell —— 读时跟踪,只依赖真正走到的模块 */
export function taskGraph(engine: Engine, entry: string): GraphOut {
  const graph = engine.run<GraphOut>('graph:' + entry, ['out:parse:' + entry], (read) => {
    const seen = new Set<string>();
    const walk = (file: string): void => {
      if (seen.has(file)) return;
      seen.add(file);
      for (const dep of (read('out:parse:' + file) as Ast).imports) walk(dep);
    };
    walk(entry);
    return { entry, modules: [...seen].sort() };
  });
  engine.write('out:graph:' + entry, graph);
  return graph;
}

/** bundle:依赖集合由 graph 决定,不重扫源码 */
export function taskBundle(engine: Engine, entry: string): BundleOut {
  const bundle = engine.run<BundleOut>('bundle:' + entry, ['out:graph:' + entry], (read) => {
    const graph = read('out:graph:' + entry) as GraphOut;
    const modules = graph.modules.map((file) => read('out:transform:' + file) as TransformOut);
    return {
      entry,
      moduleCount: modules.length,
      size: modules.reduce((n, m) => n + m.code.length, 0),
    };
  });
  engine.write('out:bundle:' + entry, bundle);
  return bundle;
}

/** 需求驱动:只解析真正可达的模块(懒打包) */
export function parseModule(engine: Engine, file: string, seen = new Set<string>()): Set<string> {
  if (seen.has(file)) return seen;
  seen.add(file);
  for (const dep of taskParse(engine, file).imports) parseModule(engine, dep, seen);
  return seen;
}

export function buildEntry(engine: Engine, entry: string): BundleOut {
  parseModule(engine, entry);
  const graph = taskGraph(engine, entry);
  for (const file of graph.modules) taskTransform(engine, file);
  return taskBundle(engine, entry);
}

// ==================== §4 聚合图 ====================

export interface Diagnostic { msg: string; module: string }
export interface CollectResult { diagnostics: Diagnostic[]; visits: number }

/** 依赖图可达百万节点;diagnostics 收集等查询不能靠全图遍历,改走并行的聚合图 */
export class AggregationGraph {
  private readonly leaves = new Map<string, Diagnostic[]>();
  private readonly chunks = new Map<string, Diagnostic[]>();

  record(module: string, diagnostics: Diagnostic[]): void { this.leaves.set(module, diagnostics); }

  build(entry: string, modules: readonly string[]): Diagnostic[] {
    const all: Diagnostic[] = [];
    for (const m of modules) all.push(...(this.leaves.get(m) ?? []));
    this.chunks.set(entry, all);
    return all;
  }

  collectNaive(modules: readonly string[]): CollectResult {
    let visits = 0;
    const found: Diagnostic[] = [];
    for (const m of modules) { visits++; found.push(...(this.leaves.get(m) ?? [])); }
    return { diagnostics: found, visits };
  }

  /** 访问次数与模块数无关 */
  collectAggregated(entry: string): CollectResult {
    return { diagnostics: this.chunks.get(entry) ?? [], visits: 1 };
  }
}
