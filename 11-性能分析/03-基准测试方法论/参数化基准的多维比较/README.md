# 参数化基准的多维公平比较

> 待研究项落地：`参数化基准（hyperfine --parameter-scan / JMH @Param）的结果如何做公平的多维比较`。核心结论：**hyperfine 默认用"全局最快"当参考点，在多维参数下这个比值会把几个维度混成一个数**；公平的做法是固定其它维度后在组内各自选参考。

## 简介

用 `--parameter-scan` 或 `@Param` 跑出一组结果后，"谁快几倍"这个数字很危险：它默认以**整个扫描里最快的那条**为参考。如果扫的是两个维度（比如 `threads × size`），那么"1 线程 + 大数据"的 ×10 里同时混进了 size 的 4× 和 threads 的 2.5×——**10 = 4 × 2.5**，你没法从这个数里分离出任何一个维度的效应。

| 比较方式 | 1 线程 large | 8 线程 large |
| --- | --- | --- |
| 全局参考（hyperfine 默认） | ×10.00 | ×4.00 |
| 分组参考（固定 threads，只比 size） | ×4.00 | ×4.00 |

分组之后结论**一致且可解释**：`large` 恒为 `small` 的 4 倍，与并发度无关。这是真正能写进报告的结论。

## 原理详解

### 1. hyperfine 的参数范围：`RangeStep` 的三条拒绝与一个 off-by-one

```rust
pub fn new(start: T, end: T, step: T) -> Result<Self, ParameterScanError> {
    if end < start { return Err(ParameterScanError::EmptyRange); }
    if step == T::from(0) { return Err(ParameterScanError::ZeroStep); }
    const MAX_PARAMETERS: usize = 100_000;
    match range_step_size_hint(start, end, step) {
        (_, Some(size)) if size <= MAX_PARAMETERS => Ok(...),
        _ => Err(ParameterScanError::TooLarge),
    }
}
```

迭代是 `if self.state > self.end { return None }`——**闭区间**，最后一个值是 ≤ end 的那个（0..10 step 3 得 `[0,3,6,9]`，下一个 12 越界才停）。

规模估算用的是：

```rust
let steps = (end - start + T::from(1)) / step;
```

**加的是绝对值 1，不是一个 step**。后果是方向不一致的偏差（自检 E3）：

| 步长 | size_hint | 实际值个数 | 方向 |
| --- | --- | --- | --- |
| 0..10 step 3 | 3 | 4 | **低估** |
| 0..1 step 0.1 | 20 | 11 | **高估** |
| 0..10 step 1 | 11 | 11 | 精确 |

低估的代价在 `MAX_PARAMETERS` 边界上会真的漏过去：`0..300000 step 3` 的 `size_hint` 恰好是 100000（通过检查），实际却产生 **100001** 个值（自检 E4）。

### 2. 相对速度怎么算：参考点、方向、误差传播

```rust
let ratio = match relative_ordering {
    Ordering::Less    => reference.mean / result.mean,   // 结果更快
    Ordering::Equal   => 1.0,
    Ordering::Greater => result.mean / reference.mean,   // 结果更慢
};
let ratio_stddev = ratio * ((result_stddev / result.mean).powi(2)
                          + (fastest_stddev / reference.mean).powi(2)).sqrt();
```

三个要点：

- **比值恒 ≥ 1**，语义是"比参考慢多少倍"；参考自己恒为 1.0；
- 参考点是 `fastest_of`（按均值最小的那条命令）——**是全局的，不分维度**；
- 标准差按误差传播算，源码注释明确写了 `Covariance assumed to be 0, i.e. variables are assumed to be independent`。同一台机器上相邻两次运行其实**不独立**，所以这个 σ 偏乐观。任一侧 `stddev` 缺失就给 `None`。

还有两个边界分支：`result.mean == 0.0` 时，非参考给 `f64::INFINITY`、参考自己给 `1.0`；`compare_mean_time` 用 `partial_cmp(...).unwrap_or(Ordering::Equal)`，出现 NaN 时按相等处理。另外**参考自己 mean 为 0 时，别人的比值是 `x/0.0 = inf`**（Rust 的 f64 语义不 panic，Python 直写会抛 `ZeroDivisionError`，demo 里显式模拟了这个语言差异）。

### 3. JMH 侧：外积与 Enum 的隐式默认值

`@Param` 的 Javadoc 写得很直接：

> When multiple `@Param`-s are needed for the benchmark run, JMH will compute the **outer product** of all the parameters in the run.

即多个参数是**笛卡尔积**（2 × 3 = 6），不是配对展开。默认值方面：

- 哨兵是 `BLANK_ARGS = "blank_blank_blank_2014"`；
- 类型限于基本类型 / 包装类型 / `String` / `Enum`；
- **`Enum` 是唯一有隐式默认值的**：自动取全部枚举常量；其它类型不给默认值就跑不起来；
- 字段必须是 `@State` 类里的**非 final** 字段，且在任何 `@Setup` 之前注入（"It is not guaranteed the field value would be accessible in any initializer or any constructor"）。

### 4. 输出顺序由参数名的字典序决定

`BenchmarkResult.parameters` 的类型是 `BTreeMap<String, String>`，**按 key 排序**。导出 CSV 时列的顺序取决于参数名的字典序，与你在命令行里写的顺序无关（`{threads, size}` 的列序是 `size` 在 `threads` 前，因为 `s < t`）。跨工具拼接结果时这是个隐蔽的错位来源。

## 环境依赖

- Python ≥ 3.9（仅标准库，小数步长用 `decimal.Decimal`）；Go ≥ 1.21（`go run .`）

## 运行方式

```bash
cd 11-性能分析/03-基准测试方法论/参数化基准的多维比较
python python/main.py             # 冒烟：RangeStep、全局参考 vs 分组参考、JMH 外积
python python/selfcheck_param.py  # 37 条断言，全绿
cd go && go run .                 # Go 版同模型
```

## 关键代码

| 文件 | 职责 |
| --- | --- |
| `python/main.py` | `RangeStep` 与 `range_step_size_hint`；`BenchmarkResult` + `compare_mean_time` / `fastest_of` / `compute_relative_speeds`；`group_by` / `grouped_relative_speeds`（分组参考）；`param_outer_product` / `enum_defaults` |
| `python/selfcheck_param.py` | 37 条断言：三条拒绝、off-by-one 两个方向、100000 边界漏过、参考点选择、比值方向、`mean == 0` 三分支、误差传播、NaN 处理、字典序、外积完整性、多维混合的可分解性 |
| `go/paramscan.go` | 同模型的 Go 版（247 行） |

## 性能边界与注意事项

- **不要在多维结果上直接读 hyperfine 的"×N"**：它混了所有维度。先分组，再在组内比。
- **分组参考会给出多个 1.0**（每组一个），这是对的：跨组的 1.0 之间没有可比性。
- **相对速度的 σ 假定两次运行独立**，同一机器上通常不成立，别拿它做显著性判断。
- **`size_hint` 对非 1 步长有偏差**：整数步长低估、小数步长高估；在接近 100000 的扫描上要自己数一遍。
- **`mean == 0` 会产生 `inf`**，导出 JSON 时 `inf` 不是合法 JSON 数字，下游解析会炸。
- **JMH 的 `@Param` 是笛卡尔积**，参数个数是指数增长的：3 个各 10 值的参数就是 1000 次基准。
- **`@Param` 字段不要写成 `final`**，也不要在构造器或字段初始化里读它的值。

## 参考资料（实际阅读过的来源）

- [`sharkdp/hyperfine` — `src/parameter/range_step.rs`](https://github.com/sharkdp/hyperfine/blob/master/src/parameter/range_step.rs) — `EmptyRange` / `ZeroStep` / `TooLarge` 三条拒绝、`MAX_PARAMETERS = 100_000`、`Iterator::next` 的 `state > end` 判据、`range_step_size_hint` 的 `(end - start + 1) / step`，以及模块自带测试（0..10 step 3 得 4 个值、0..1 step 0.1 得 11 个值、`0..100001 step 1` 报 too large）
- [`sharkdp/hyperfine` — `src/benchmark/relative_speed.rs`](https://github.com/sharkdp/hyperfine/blob/master/src/benchmark/relative_speed.rs) — `fastest_of` / `compare_mean_time` 的 `unwrap_or(Ordering::Equal)`、`mean == 0` 的 INFINITY 与 1.0 两分支、ratio 的三向取法、误差传播公式与"Covariance assumed to be 0"注释、`SortOrder::Command` 与 `MeanTime`
- [`sharkdp/hyperfine` — `src/benchmark/benchmark_result.rs`](https://github.com/sharkdp/hyperfine/blob/master/src/benchmark/benchmark_result.rs) — `parameters: BTreeMap<String, String>` 的字典序语义、`stddev` 在只跑一次时缺失
- [`openjdk/jmh` — `jmh-core/src/main/java/org/openjdk/jmh/annotations/Param.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/annotations/Param.java) — outer product 的 Javadoc 原文、`BLANK_ARGS` 哨兵、`@State` 非 final 字段与"任何 @Setup 之前注入"的约束、Enum 的隐式默认值
