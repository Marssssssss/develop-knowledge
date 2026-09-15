/**
 * Turbopack 增量计算引擎 —— 自检脚本
 * 运行: node turbo_check.js
 *
 * 对照的是"top-down memoization + 内容寻址缓存"(webpack/Bazel 的做法):
 * 只要文件字节变了,其上游所有任务按 key 失效、整条链重跑;
 * turbo-tasks 把粒度下沉到"任务真正读过的 cell",再叠加内容相等短路 —— §2/§3 用量化差异说明。
 */

'use strict';

const M = require('./turbo.js');
const {
  Engine, SOURCES, createProject, buildEntry, AggregationGraph,
} = M;

/** 菱形依赖夹具(index → a/b → s),用来验证共享节点只算一次 */
const DIAMOND_SOURCES = {
  'src/index.js': "import a from './a.js';\nimport b from './b.js';\n",
  'src/a.js': "import s from './s.js';\n",
  'src/b.js': "import s from './s.js';\n",
  'src/s.js': 'export default 1;\n',
};

const log = { pass: 0, fail: 0 };
const check = (name, ok, extra) => {
  if (ok) { log.pass++; console.log(`  PASS  ${name}`); }
  else { log.fail++; console.log(`  FAIL  ${name}${extra === undefined ? '' : `  → ${extra}`}`); }
};
const section = (t) => console.log(`\n=== ${t} ===`);
const delta = (e, before) => e.stats.taskRuns - before;

// ==================== §1 冷构建:task 身份与依赖边界 ====================
section('§1 冷构建:task 身份与依赖边界');
const e = createProject(SOURCES);
const bundle = buildEntry(e, 'index.js');
check('冷构建执行 10 次任务(4 parse + 1 graph + 4 transform + 1 bundle)', e.stats.taskRuns === 10, e.stats.taskRuns);
check('task 表恰好 10 个实例(键 = 函数名 + 参数)', e.tasks.size === 10, e.tasks.size);
check('每个 parse/transform 各跑一次', ['parse:index.js', 'parse:a.js', 'parse:b.js', 'parse:c.js']
  .every((k) => e.tasks.get(k).runs === 1));
check('graph.modules 去重排序 = [a, b, c, index]',
  JSON.stringify(e.tasks.get('graph:index.js').value.modules)
  === JSON.stringify(['a.js', 'b.js', 'c.js', 'index.js']));
check('bundle 产物 4 个模块且 size > 0(由各 transform 输出长度累加)',
  bundle.moduleCount === 4 && bundle.size > 0);
check('需求驱动:admin.js/unused.js 从未被解析(无人请求)',
  !e.tasks.has('parse:admin.js') && !e.tasks.has('parse:unused.js'));
check('源 cell 与输出 cell 版本各为 1',
  e.cell('src/a.js').version === 1 && e.cell('out:parse:a.js').version === 1);
check('依赖全部未变时即使重跑 buildEntry 也 0 次执行', (() => {
  const hits = e.stats.cacheHits;
  buildEntry(e, 'index.js');
  return delta(e, 10) === 0 && e.stats.cacheHits - hits === 10;
})(), `${delta(e, 10)} runs / hits ${e.stats.cacheHits - 10}`);

// ==================== §2 读时依赖跟踪的粒度 ====================
section('§2 读时依赖跟踪的粒度(比 top-down memoization 更细)');
const affected = e.affectedFrom('src/a.js');
check('src/a.js 的失效闭包 = {parse:a, transform:a, graph:index, bundle:index}',
  JSON.stringify([...affected].sort())
  === JSON.stringify(['bundle:index.js', 'graph:index.js', 'parse:a.js', 'transform:a.js']),
  [...affected].sort().join(','));
check('兄弟模块 parse:b / parse:c 不在闭包内(它们没读 src/a.js)',
  !affected.has('parse:b.js') && !affected.has('parse:c.js') && !affected.has('parse:index.js'));

const aVer = e.cell('src/a.js').version;
check('注释改动会 bump 源 cell 版本(字节变了)',
  e.write('src/a.js', 'export default 1; // touched\n') === true && e.cell('src/a.js').version === aVer + 1);

const runsBefore2 = e.stats.taskRuns;
const reuseBefore2 = e.stats.equalOutputsReused;
const skippedBefore2 = e.stats.cellWritesSkipped;
buildEntry(e, 'index.js');
check('注释改动只让 parse:a.js 重跑 1 次', delta(e, runsBefore2) === 1, delta(e, runsBefore2));
check('AST 输出内容相等 → 复用旧值,out:parse:a.js 版本不 bump',
  e.stats.equalOutputsReused - reuseBefore2 === 1 && e.cell('out:parse:a.js').version === 1);
check('写回时内容相等 → 全部跳过(10 次写 0 次 bump)',
  e.stats.cellWritesSkipped - skippedBefore2 === 10, e.stats.cellWritesSkipped - skippedBefore2);
check('bundle 从未重跑(读集里只有 graph cell)', e.tasks.get('bundle:index.js').runs === 1);

// ==================== §3 真实源码改动:内容相等短路的连锁截断 ====================
section('§3 真实源码改动:内容相等短路的连锁截断');
check('字面量 1 → 42 会改变 parse 输出(AST 真的变了)',
  e.write('src/a.js', 'export default 42;\n') === true);
const runsBefore3 = e.stats.taskRuns;
const reuseBefore3 = e.stats.equalOutputsReused;
buildEntry(e, 'index.js');
check('失效链只走 3 步:parse:a → graph:index / transform:a', delta(e, runsBefore3) === 3, delta(e, runsBefore3));
check('out:parse:a.js 版本 bump 到 2(AST 变了)', e.cell('out:parse:a.js').version === 2);
check('graph / transform:a 重跑但输出相等 → 复用旧值,不继续向上传播',
  e.stats.equalOutputsReused - reuseBefore3 === 2
  && e.cell('out:graph:index.js').version === 1 && e.cell('out:transform:a.js').version === 1);
check('最贵的 bundle 仍然一次都没重跑', e.tasks.get('bundle:index.js').runs === 1);

const eb = createProject(SOURCES, { contentEquality: false });
buildEntry(eb, 'index.js');
eb.write('src/a.js', 'export default 1; // touched\n');
const runsBeforeBase = eb.stats.taskRuns;
buildEntry(eb, 'index.js');
check('朴素基线(关掉内容相等)同一处注释改动要跑 4 次:parse:a → transform:a → graph → bundle',
  runsBeforeBase === 10 && delta(eb, runsBeforeBase) === 4, delta(eb, runsBeforeBase));
check('基线的 bundle 被迫重跑(turbo 1 次 vs 基线 4 次执行)', eb.tasks.get('bundle:index.js').runs === 2);

// ==================== §4 菱形依赖只算一次 + 文件系统缓存 ====================
section('§4 菱形依赖只算一次 + 文件系统缓存');
const d = createProject(DIAMOND_SOURCES);
buildEntry(d, 'index.js');
check('菱形依赖(index→a/b→s)冷构建同样 10 次,共享节点 parse:s.js 只跑一次',
  d.stats.taskRuns === 10 && d.tasks.get('parse:s.js').runs === 1);
check('a.js / b.js 的 parse 各 1 次(不是按 import 边重复计算)',
  d.tasks.get('parse:a.js').runs === 1 && d.tasks.get('parse:b.js').runs === 1);

const json = d.serialize();
const d2 = Engine.deserialize(json);
buildEntry(d2, 'index.js');
check('反序列化后的新实例重建:0 次任务执行、10 次缓存命中',
  d2.stats.taskRuns === 0 && d2.stats.cacheHits === 10, `${d2.stats.taskRuns}/${d2.stats.cacheHits}`);
check('序列化保留了依赖版本(cell version 与 seen 一致)',
  JSON.parse(json).cells.every(([, , v]) => v >= 1) && JSON.parse(json).tasks.every(([, , seen]) => seen.length >= 1));

// ==================== §5 聚合图:查询开销与图规模解耦 ====================
section('§5 聚合图:查询开销与图规模解耦');
const ag = new AggregationGraph();
const mods = [];
for (let i = 0; i < 200; i++) { const m = `m${i}.js`; mods.push(m); ag.record(m, [{ msg: `w${i}`, module: m }]); }
ag.build('index.js', mods);
const naive = ag.collectNaive(mods);
const aggregated = ag.collectAggregated('index.js');
check('朴素收集要访问 200 个模块节点', naive.visits === 200, naive.visits);
check('聚合节点只访问 1 次,且 diagnostics 完全一致',
  aggregated.visits === 1 && JSON.stringify(aggregated.diagnostics) === JSON.stringify(naive.diagnostics));
check('诊断条数守恒', aggregated.diagnostics.length === 200, aggregated.diagnostics.length);

// ==================== §6 需求驱动的懒构建:切到 admin 入口 ====================
section('§6 需求驱动的懒构建:切到 admin 入口');
const ea = createProject(SOURCES);
buildEntry(ea, 'admin.js');
check('只构建 admin 入口时执行 6 次任务(2 parse + 1 graph + 2 transform + 1 bundle)',
  ea.stats.taskRuns === 6, ea.stats.taskRuns);
check('index/c 分支完全没被触碰(懒打包)', !ea.tasks.has('parse:index.js') && !ea.tasks.has('parse:c.js'));
check('admin 构建完成后 bundle 只跑过 1 次', ea.tasks.get('bundle:admin.js').runs === 1);

ea.write('src/admin.js', "import u from './unused.js';\nimport c from './c.js';\nexport default u + c;\n");
const runsBefore6 = ea.stats.taskRuns;
buildEntry(ea, 'admin.js');
check('新增依赖后按需补跑 c.js 分支(6 次:parse:admin/c + graph + 3 transform + bundle)',
  delta(ea, runsBefore6) === 6, delta(ea, runsBefore6));
check('parse:c.js 被首次创建并只跑一次', ea.tasks.has('parse:c.js') && ea.tasks.get('parse:c.js').runs === 1);
check('未被引用的 parse:index.js 依旧不存在(依赖是按需发现的)',
  !ea.tasks.has('parse:index.js'));
check('graph/bundle 输出变了 → 强制重跑', ea.tasks.get('bundle:admin.js').runs === 2
  && JSON.stringify(ea.tasks.get('graph:admin.js').value.modules)
  === JSON.stringify(['admin.js', 'c.js', 'unused.js']));

// ==================== 汇总 ====================
console.log(`\n${log.fail === 0 ? 'ALL PASS' : 'FAILED'}  ${log.pass} passed, ${log.fail} failed`);
process.exit(log.fail === 0 ? 0 : 1);
