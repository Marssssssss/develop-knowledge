# Turbopack 增量计算引擎(turbo-tasks)最小实现

> 300 行 JS(附 TS 类型化孪生)复现 Turbopack 增量计算的三条核心机制:**value cell(Vc)+ 函数级记忆化**、
> **读时依赖跟踪**、**内容相等短路**,并附**聚合图**与**文件系统缓存**。结论全部由 `node turbo_check.js`
> 一条命令复核(36/36 PASS)。

## 一、要解决的问题:为什么"包级缓存"不够

传统构建器的缓存粒度是**文件/包**:webpack 存"这个文件的产物",Bazel 这类内容寻址系统把键设成"输入内容哈希"。
问题在于**失效判定是 top-down 的** —— 想知道 bundle 还能不能用,先要知道依赖图变没变;想判断依赖图,
又得先知道每个模块的 AST 变没变。于是**一次叶子改动会作废整条祖先链**,哪怕中间某步的输出在语义上毫无变化。

Turbopack 的做法是把粒度下沉到 *"the smallest unit of work"*:**一个函数 + 它的参数**就是一个 task,
缓存条目属于 task 而不是文件。本 demo 用 `parse / transform / graph / bundle` 四个 task 搭出这条流水线,
再量化它到底省了多少次执行。

## 二、四个原语与 task 身份

`turbo-tasks` 的 API 收敛成四个原语,**functions / values / traits / collectibles**,前两个是增量计算本体:

| 原语 | 含义 | 本 demo 对应物 |
| --- | --- | --- |
| `#[turbo_tasks::function]` | 可缓存的纯计算;参数不同 → 不同实例 | `engine.run(key, argIds, fn)` |
| `Vc<T>`(value cell) | 跨 task 传递的值;**读它才算依赖** | `engine.read(id)` / `write(id, v)` |
| trait | 运行期的能力抽象(等价 Rust trait) | 省略(JS 无对应物) |
| collectible | 分散在多 task 中、最后汇总的集合(如 diagnostics) | `AggregationGraph` |

**task 身份 = 函数名 + 参数**,所以 `parse:a.js` 与 `parse:b.js` 是两个独立缓存条目,
`graph:index.js` 与 `graph:admin.js` 也各自独立 —— 多入口项目里改 `index` 侧不会动 `admin` 侧。
`argIds` 就是这组参数的记录;**它不等于依赖集**(下一节)。

## 三、读时依赖跟踪:依赖是"读出来的",不是"声明出来的"

这是与 top-down memoization 最关键的分野。`taskGraph` 的签名只声明"要入口的 parse 输出",
但它实际会沿 `imports` 递归读到 `a/b/c.js` 的 parse 输出:

```js
function taskGraph(engine, entry) {
  const graph = engine.run('graph:' + entry, ['out:parse:' + entry], (read) => {
    const seen = new Set();
    const walk = (file) => {
      if (seen.has(file)) return;
      seen.add(file);
      for (const dep of read('out:parse:' + file).imports) walk(dep);   // ← 读到的才登记
    };
    walk(entry);
    return { entry, modules: [...seen].sort() };
  });
  engine.write('out:graph:' + entry, graph);
  return graph;
}
```

引擎侧只有一行:把正在执行的 task 压栈,`read()` 时把 cell id 塞进它的 `readCells`。

```js
read(id) {
  const current = this.stack[this.stack.length - 1];
  if (current) current.readCells.add(id);      // 动态依赖:运行时才知道
  return this.cell(id).value;
}
```

收益是**失效范围自动收敛到最小**:`affectedFrom('src/a.js')` 只返回
`{parse:a.js, transform:a.js, graph:index.js, bundle:index.js}` ——
兄弟 `parse:b.js` / `parse:c.js` / `parse:index.js` **不在闭包内**,因为它们没读 `src/a.js`。
换成"声明依赖"或"按目录/包失效",这三个任务会被无差别重跑。
注意这只是**静态闭包(上界)**,真正的重算范围由下一节进一步裁剪。

## 四、内容相等短路:把失效链"截断"在中间

`write()` 里有两级短路,必须分清:

1. **引用相同** —— 压根没有"写"发生,任何实现都不该因此 bump 版本(这不是优化,是常识)。
2. **内容相等** —— 结构比较发现值没变,于是不递增版本号,下游版本校验全部命中级联失效到此为止。

```js
write(id, value) {
  const cell = this.cell(id);
  if (cell.version > 0) {
    if (cell.value === value) { this.stats.cellWritesSkipped++; return false; }              // 引用相同
    if (this.contentEquality && equalValue(cell.value, value)) { this.stats.cellWritesSkipped++; return false; } // 内容相等
  }
  cell.value = value; cell.version++; this.stats.cellVersionBumps++;
  return true;
}
```

第二个落点在 `run()`:任务确实重跑了,但输出与旧值相等时**保留旧值对象、不重写 cell**,
下游版本校验依然命中 —— `if (hadValue && contentEquality && equalValue(prev, value)) { equalOutputsReused++; return task.value; }`。

实测(数字由 `node turbo_check.js` 直接产出):

| 场景 | 任务执行次数 | 发生了什么 |
| --- | --- | --- |
| 冷构建 | **10** | 4 parse + 1 graph + 4 transform + 1 bundle |
| 无改动重建 | **0** | 10 次缓存命中,一次函数体都没进 |
| 注释改动 `src/a.js`(字节变、AST 不变) | **1** | 只有 `parse:a.js` 重跑;输出相等 → `out:parse:a.js` 版本不 bump → 其余 9 个任务全 hit |
| 改字面量 `1 → 42`(AST 变、产物不变) | **3** | `parse:a` 版本 bump → `graph` / `transform:a` 重跑但输出相等 → **`bundle` 一次都没重跑** |
| 朴素基线(关掉内容相等,同一处注释改动) | **4** | `parse:a → transform:a → graph → bundle` 全链重跑 |
| 反序列化后重建(文件系统缓存) | **0** | 落盘的 cell 值 + 依赖版本直接命中 |
| 只构建 `admin` 入口 | **6** | `index`/`c` 分支完全没被触碰(懒 + 需求驱动) |

第 3 行与第 5 行是本 demo 最该记住的对比:同一处改动,turbo-tasks 少跑 25% 的任务;
注释类改动上差距是 1 vs 4(AST 层就收敛了)。越靠后的 task 越贵,**`bundle` 一笔不跑才是增量构建真正的收益来源**。

## 五、需求驱动与懒打包

*"we defer work until an active query needs it"* 的直接推论是**没人请求的东西不该被构建**。
`parseModule` 天然只沿 `imports` 走:`const ast = taskParse(engine, file); for (const dep of ast.imports) parseModule(engine, dep, seen);`

自检断言了三件能证明"懒"的事:

- 只构建 `admin` 入口时 `parse:index.js` **从未被创建**(整张 task 表里没有它);
- 给 `admin.js` 新增一行 `import './c.js'`,重建时 `parse:c.js` 才**按需**补跑一次;
- 菱形依赖(`index → a/b → s`)里共享节点 `parse:s.js` 只执行 1 次,不是按边重复计算。

## 六、聚合图:让"查询开销"与"图规模"解耦

真实依赖图可达几十万到上百万节点,收集 diagnostics、等整棵子树完成都不能靠全图遍历。
做法是与依赖图并行维护一张聚合图:叶节点记自身信息,高层节点汇总更多子节点、分辨率更低,查询只访问高层节点。

| 做法 | 200 个模块时的节点访问次数 |
| --- | --- |
| `collectNaive`(每次遍历整图) | **200** |
| `collectAggregated`(读入口聚合节点) | **1** |

## 七、文件系统缓存与生态现状

`serialize()` 把 cell 值 + 每个 task 的依赖版本快照序列化(真实实现落盘,重启即复用):
`tasks: [...this.tasks].map(([k, t]) => [k, t.value, [...t.seen], t.hasValue])`。
自检里"反序列化后的新实例重建 = 0 次执行、10 次命中"就是这条路径。Turbopack 强调这是**函数级**缓存而非包级,
且开发期**仍然打包**(不做原生 ESM 逐模块请求)。另一条 Rust 路线是 Rolldown(Rollup 兼容 API),
Vite 8+ 已采用;Turbopack 走统一图 + 增量引擎,Next.js 默认启用。

## 八、目录结构与运行

```
Turbopack/
├── js/turbo.js         # 引擎 + 流水线 + 聚合图(299 行)
├── js/turbo_check.js   # 36 条断言的对照实验
├── ts/turbo.ts         # 类型化孪生:cell 存 unknown,任务边界用泛型收敛(300 行)
└── README.md
```

```bash
cd js && node turbo_check.js      # 期望输出:ALL PASS  36 passed, 0 failed
```

环境:Node.js ≥ 18(用到 `String.prototype.matchAll`),无第三方依赖。

## 九、性能边界

- **短路成本**:内容相等用结构比较,大对象上 `JSON.stringify` 是 O(size);真实实现用更便宜的指针/哈希比较
  再按需深比较。若 task 输出极大,结构比较本身可能比重算更贵。
- **失效闭包是上界**:`affectedFrom` 返回的是"可能受影响"的集合,实际重算集合是它的子集。
- **内存**:每个 task 常驻 `readCells` / `seen`,与读集大小成正比;读几千个 cell 的任务会持有很宽的依赖表
  —— 这也是 Turbopack 要把 task 拆细的原因之一。
- **本 demo 单线程同步**:真实实现每个 task 是 Tokio 任务,并行执行 + 同 task 去重。单线程会**放大**串行开销,
  但不改变这里的因果结论。

## 十、注意事项与常见坑

1. **"引用相同"不等于"内容相等"**:混在一起统计会得到虚高的短路收益。基线对照必须只关掉内容相等、
   保留引用短路,否则对比失真(本 demo 第一版就踩了:基线被算成 7 次而不是 4 次)。
2. **不要给 parse 输出塞字节数**:输出里一旦含 `code.length`,注释改动就会改变 AST 输出,级联失效一路传到 bundle。
   **输出的字段选择本身就是增量策略**(本 demo 刻意去掉 `bytes`)。
3. **task key 必须包含全部参数**:`graph:index.js` 与 `graph:admin.js` 共用 key 会让两个入口互相污染缓存。
4. **输出 cell 的写入必须在 `run` 之外**:写在回调里会被记成本任务自己的依赖,形成自环。
5. **`readCells` 每次执行都要重置**,否则上一轮的依赖残留(动态依赖缩小后旧依赖仍会触发失效);
   `seen` 快照则必须在**执行结束**时记录。
6. **菱形/环形依赖**:`seen` 去重但只对 DAG 安全;真环需要额外检测,本 demo 未处理。
7. **失效是惰性的**:本 demo 用"版本戳 + 读时校验"模拟,不主动遍历下游;真实实现是 eager mark-dirty 调度
   + 惰性验证,可观察行为一致但并发下的调度机会不同。**别把聚合图当依赖图用** —— 它只汇总信息,不承载依赖语义。

## 参考资料

- [Inside Turbopack: Building Faster by Building Less](https://nextjs.org/blog/turbopack-incremental-computation)
  —— value cell、read-time tracking、content equality、demand-driven、aggregation graph 的一手说明
- [`turbo_tasks` rustdoc](https://turbopack-rust-docs.vercel.sh/rustdoc/turbo_tasks)
  —— 四原语 functions / values / traits / collectibles;Task = 函数 + 参数;每个 task 跑在 Tokio 上
- [Turbopack docs (turbo.build/pack)](https://turbo.build/pack/docs)
  —— 统一图、开发期仍然打包、函数级缓存 + 磁盘持久化、懒打包
- [Rolldown 官网](https://rolldown.rs/) —— Rollup 兼容 API 的 Rust 打包器,19k 模块基准,Vite 8+ 默认
