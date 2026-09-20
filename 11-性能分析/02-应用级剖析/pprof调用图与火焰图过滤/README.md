# pprof 调用图与火焰图视图的过滤组合

> `-nodefraction` / `-focus` / `-ignore` / `-hide` / `-show` / `-show_from` 到底怎么改数据？
> 本 demo 直接照 google/pprof 的 **源码** 把过滤语义移植成可断言的模型：过滤是**改样本**而不是改显示，
> 所以「过滤之后再算 cum」和「先算 cum 再过滤」会得到完全不同的图。

## 一、两条容易读错的前提

**1. 栈是「叶在前」**。`profile.proto` 写明 `The leaf is at location_id[0]`——`Sample.Location[0]`
是当前正在执行的函数，根帧在**末位**。这决定了 `ShowFrom` 的扫描方向（见 §四）。

**2. 过滤改的是样本集**，`applyFocus`（`internal/driver/driver_focus.go`）的执行顺序是固定的：

```text
FilterSamplesByName(focus, ignore, hide, show)   ← 改样本
ShowFrom(show_from)                              ← 改栈
FilterSamplesByTag(tagfocus, tagignore)          ← 改样本
FilterTagsByName(tagshow, taghide)               ← 改标签
PruneFrom(prune_from)
```

每一步都独立调用 `warnNoMatches`，所以「focus 表达式一个样本都没匹配上」会在 stderr 明确报
`Focus expression matched no samples`——**不是静默空图**。

## 二、focus / ignore：ignore 优先，不是「交集」

`FilterSamplesByName` 的注释是「只保留至少一个帧命中 focus、且没有任何帧命中 ignore 的样本」。
判定函数 `focusedAndNotIgnored` 的实现是**遍历时一遇到 ignored 就 `return false`**：

```go
for _, loc := range locs {
    if focus, focusOrIgnore := m[loc.ID]; focusOrIgnore {
        if focus { f = true } else { return false }   // ignore 命中 ⇒ 立刻判死
    }
}
return f
```

三个直接后果（demo 里都断言到了）：

| 情形 | 结果 |
| --- | --- |
| 只有 focus | 保留「栈上出现过匹配帧」的样本；**没有 focus 时视为全命中** |
| focus + ignore 同时命中同一条栈 | **丢弃**（ignore 赢，不是取交集） |
| 既没匹配 focus 也没匹配 ignore 的帧 | 中性，不影响该样本的存留 |

示例：`main→work→alloc` 60、`main→work` 30、`main→gc` 10，
`-focus=work` 剩 90；再叠加 `-ignore=alloc` 只剩 30。

## 三、hide / show 是**行级**裁剪，不是帧级

一个 `Location` 可以有多行——**被内联的函数就挤在同一个地址上**。所以：

- `hide`：把命中的**行**摘掉（`unmatchedLines`）；摘空了整个 Location，该帧才从栈上消失。
- `show`：只留命中的行（`matchedLines`）；没命中就整个 Location 被标 hidden。
- 栈上**所有**帧都被摘空 ⇒ 整条样本丢弃（`continue`，不进结果集）。
- 命中 `Mapping.File` 时行为不同：`unmatchedLines` 返回 nil（整组丢弃），`matchedLines` 返回全部。

这也是为什么 `-hide` 之后调用图上会出现**虚线边**：中间帧没了，父子被直接连起来。

## 四、ShowFrom：丢掉「比最浅匹配帧更浅」的帧

`filter.go` 注释给了标准例子，栈 `[A, B, C, B]`（A 为根，展示顺序）：

| 表达式 | 结果 |
| --- | --- |
| `ShowFrom(A)` | `[A, B, C, B]`（根就是匹配帧，全留） |
| `ShowFrom(B)` | `[B, C, B]`（丢掉 A） |
| `ShowFrom(C)` | `[C, B]` |
| `ShowFrom(D)` | 无匹配 ⇒ **整条样本被丢弃** |

实现是从**根侧（末位）往叶侧**扫，命中第一个就 `sample.Location = sample.Location[:i+1]`：
保留匹配帧及其**更靠近叶**的部分。重复帧（B 出现两次）取**最靠根**的那次。

## 五、nodefraction / edgefraction：cum 口径，且临界值**保留**

`internal/graph/graph.go` 的 `getNodesAboveCumCutoff`：

```go
if abs64(n.Cum) < nodeCutoff { continue }   // 小于才丢；等于保留
```

- 比较用的是 **cum**（帧在栈上出现过就计入），**不是 flat**（只有叶帧记自用量）。
- 严格小于才丢弃 ⇒ `cum == fraction*total` 的节点**留在图上**。demo 用 total=100、gc 的 cum=10 验了
  `nodefraction=0.10`（保留）与 `0.101`（裁掉）这一临界对。
- 边同理：`TrimLowFrequencyEdges` 删掉 `abs(weight) < cutoff` 的边，删掉的边在图上渲染成**虚线**。
- `-trim=false` 可以关掉这几个默认值，拿**完整、不裁剪**的图。

## 六、调用图与火焰图各自的「宽度/颜色」语义

**调用图**（`-dot` / `-web` / `-svg`）：

- 节点**颜色** = cum（大正值偏红，大负值偏绿，接近 0 灰）；节点**字号** = **flat** 的绝对值。
  ⇒ 「字小 + 红」= 自己不耗时但孩子很耗时，这是最常用的定位信号。
- 边**粗细** = 这条路径消耗的资源；边**颜色**同 cum 规则；**虚线** = 中间有节点被裁掉；
  `(inline)` 标记 = 该调用已被内联进调用者。

**火焰图**（web UI 的 `Flame graph` 视图）：

- 框宽 = 该帧出现过的样本值之和；孩子**从左到右按宽度降序**排列。
- 框**颜色按包名**分组（C++ 里所有 `std::` 同色），**不编码耗时**。
- 框内**字号差异只是为了塞下名字**，没有语义——别去读它。
- 选中一个框会翻转成「通向它的调用栈」，这解决了传统火焰图只能自上而下看、找不到调用者的问题。
- `--diff_base` 下：宽度 = 子树内**增量的绝对值之和**（150↓ + 200↑ ⇒ 宽 350），
  净变化用阴影区域表示（红=净增，绿=净减）。
- **内联用「没有横向边框」表示**：X→Y→Z 且 Y→Z 被内联，则 X|Y 有黑边、Y Z 之间没有。

## 七、运行

```bash
python python/selfcheck_filter.py       # 38 条断言，全部对照上述源码语义
cd go && go run .                      # 同语义的 Go 版（本机无 Go 工具链，走人工审查）
```

## 八、注意事项与常见坑

1. **`-focus` 会改总量**。过滤后 `total` 变小，而 `nodefraction` 的分母是**过滤后**的总量——
   连着用两个选项时，节点的去留不能按原图推。
2. **`ignore` 压过 `focus`**，想要「A 和 B 都要」不能用 `-focus=A -ignore=B` 表达。
3. **`hide` 会造出假的父子关系**（虚线边）。看到虚线先想是不是自己 hide 过。
4. **`nodefraction` 用的是 cum**：一个 flat 极小、cum 极大的热点绝不会被裁掉；
   反过来 flat 大、cum 相同的叶子节点很容易被裁。
5. 火焰图**没有** cum/flat 的视觉区分——判断「自己耗时」要回到 `top`（flat）或调用图字号。

## 参考资料（本轮实际读过）

- [google/pprof — doc/README.md（选项、调用图配色、火焰图语义）](https://raw.githubusercontent.com/google/pprof/master/doc/README.md)
- [google/pprof — profile/filter.go（FilterSamplesByName / ShowFrom 实现）](https://raw.githubusercontent.com/google/pprof/master/profile/filter.go)
- [google/pprof — internal/driver/driver_focus.go（applyFocus 顺序、tag 过滤）](https://raw.githubusercontent.com/google/pprof/master/internal/driver/driver_focus.go)
- [google/pprof — internal/driver/commands.go（nodecount/nodefraction/edgefraction 帮助文本）](https://raw.githubusercontent.com/google/pprof/master/internal/driver/commands.go)
- [google/pprof — internal/graph/graph.go（getNodesAboveCumCutoff / TrimLowFrequencyEdges）](https://raw.githubusercontent.com/google/pprof/master/internal/graph/graph.go)
- [google/pprof — proto/profile.proto（leaf at location_id[0]）](https://raw.githubusercontent.com/google/pprof/master/proto/profile.proto)
- [Go 官方博客 — Profiling Go Programs](https://go.dev/blog/pprof)
