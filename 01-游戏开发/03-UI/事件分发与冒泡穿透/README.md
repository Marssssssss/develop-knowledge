# UI 事件分发：冒泡、捕获与穿透

## 简介

一次点击从"屏幕坐标"变成"某个按钮的 onClick"，中间要经过**命中测试**和**沿 UI 树派发**两步。派发的标准模型由 **WHATWG DOM Standard §2.9 Dispatching events** 定义：先算出一条 `event path`（从 target 到根），再沿这条路径**先逆序捕获、后正序冒泡**。游戏引擎的 UGUI EventSystem、UMG、Cocos 触摸分发都是这套模型的变体。

- **event path**：target + 其祖先链，派发期间固定不变
- **捕获阶段（capture）**：根 → 叶，用于在目标收到之前预处理/拦截
- **冒泡阶段（bubble）**：叶 → 根，用于"子元素的事让父容器也知道"
- **AT_TARGET**：`eventPhase` 在 target 上永远是 2，无论当前是捕获循环还是冒泡循环
- **stopPropagation / stopImmediatePropagation**：前者停掉后续所有节点（**两个阶段都停**），后者还停掉当前节点的剩余监听
- **穿透（pass-through）**：游戏侧概念——最上层元素没"消费"事件时，把事件交给下一层继续处理

## 原理详解

### 1. §2.9 派发流程

```
dispatch(event, target)
  1. path = [target, parent, grandparent, ..., root]
  2. 捕获：for item in reverse(path)
        eventPhase = (item === target) ? AT_TARGET : CAPTURING_PHASE
        invoke(item, "capturing")
  3. 冒泡：for item in path
        if (item !== target && !event.bubbles) continue      ← 关键一行
        eventPhase = (item === target) ? AT_TARGET : BUBBLING_PHASE
        invoke(item, "bubbling")
```

### 2. invoke 与 inner invoke 的四条硬规则

| 规则 | 规范原文要点 | 可观察行为 |
| --- | --- | --- |
| 阶段过滤 | capturing 只跑 `capture=true`；bubbling 只跑 `capture=false` | target 上两种监听都会跑（分属两个循环） |
| 克隆监听表 | "Let listeners be a **clone** of … event listener list" | 本节点触发后新加的不会被**本节点**调用；但**尚未到达的祖先**上新加的会生效 |
| removed 生效 | "removal still has an effect due to the removed field" | 派发中移除的监听不会被调用 |
| stop 标志 | invoke 开头 "If event's stop propagation flag is set, then return" | 一旦置位，本阶段剩余项**和**另一阶段全部跳过 |

实测（本 demo [2]，树 root ← panel ← button，各节点注册一对 capture/bubble 监听）：

```
C-root@root(capture) | C-panel@panel(capture) | C-button@button(at-target)
| B-button@button(at-target) | B-panel@panel(bubble) | B-root@root(bubble)
```

注意 **`C-button` 与 `B-button` 的 eventPhase 都是 `at-target`** —— 规范给 target 项置 `AT_TARGET`，与它在哪个循环里被调用无关。

### 3. `bubbles=false` 到底停在哪

规范是 `if (item !== target && !event.bubbles) continue`，也就是**只跳过非 target 项**。所以 `bubbles=false` 时：捕获阶段三个节点照常跑，冒泡阶段只有 **target 自己**的非捕获监听跑一次（本 demo [3] 实测输出与浏览器一致）。很多自研 UI 误实现成"冒泡整个跳过"，导致 target 上的点击回调丢失。

### 4. 两个 stop 的差别（本 demo [4][5]）

```
在 panel 的 capture 监听里 stopPropagation()
  → 输出只剩 "STOP@panel"，button 的监听一次都不执行（连 AT_TARGET 都到不了）

在 button 上第二个监听里 stopImmediatePropagation()
  → 输出 "btn1 | btn2-SIP"，第三个监听 btn3 与祖先 panel 都不执行
```

**这是 UI 里最常见的坑**：想"只拦住子元素"却在祖先的捕获阶段 `stopPropagation`，结果子元素的 onClick 永远收不到。要拦住**冒泡**应当在祖先的**冒泡**监听里 stop，或干脆在命中层做消费标记。

### 5. 命中测试与穿透（游戏侧）

```
命中：按 depth 从大到小找第一个包含该点的元素（UGUI GraphicRaycaster 就是这么做的）
穿透：命中元素派发后若 event.consumed 为 false，则把事件交给下一层元素重来一次
```

实测：400×300 的背景（depth 0）+ 同尺寸半透明面板（depth 1）+ 60×30 按钮（depth 2）

| 点击点 | 命中 | 未消费时 | 面板消费后 |
| --- | --- | --- | --- |
| (20,20) | 按钮 | — | — |
| (200,200) | 半透明面板 | 穿透到「背景」 | 止于面板（**模态遮罩**的经典做法） |
| (500,10) | 无 | — | — |

## 对比 / 选型

| 机制 | 语义 | 典型用途 |
| --- | --- | --- |
| 捕获 + stopPropagation | 祖先在目标之前拦截 | 全屏模态层吞掉一切点击 |
| 冒泡 | 子元素的事件向上汇报 | 列表项点击由列表容器统一处理（Unity 里常见"ScrollView 里 100 个 item 只在父节点挂一个监听"） |
| `bubbles=false` | 只有 target 自己收到 | 高频滚动/拖拽事件，避免逐层上报 |
| 消费标记（`consumed`） | 引擎侧语义，非 DOM 概念 | 触摸穿透控制、Cocos `swallowTouches` |

## 环境准备

- Python ≥ 3.8（标准库）/ Node ≥ 14，无第三方依赖

## 运行方式

```bash
cd python && python3 event_dispatch.py    # 19 项断言
cd js     && node event_dispatch.js       # 打印各场景派发序列
```

## 关键代码片段

```python
def dispatch(event, target, log=None):
    path = event_path(target)                 # 1. target → 根
    event.target = target
    for item in reversed(path):               # 2. 捕获：根 → 叶
        event.eventPhase = AT_TARGET if item is target else CAPTURING_PHASE
        invoke(item, event, "capturing", log)
    for item in path:                         # 3. 冒泡：叶 → 根
        if item is not target and not event.bubbles:
            continue                          #    bubbles=false 只跳过非 target 项
        event.eventPhase = AT_TARGET if item is target else BUBBLING_PHASE
        invoke(item, event, "bubbling", log)

def invoke(node, event, phase, log):
    if event._stop_propagation:               # 两阶段都停
        return
    event.currentTarget = node
    listeners = list(node.listeners)          # 克隆：本节点触发后新增的不生效
    for lsn in listeners:
        if lsn.removed or lsn.type != event.type:   continue
        if phase == "capturing" and not lsn.capture: continue
        if phase == "bubbling" and lsn.capture:      continue
        if lsn.once: lsn.removed = True
        lsn.callback(event, log)
        if event._stop_immediate: break
```

## 性能与边界

- 派发复杂度 O(树高 × 该节点监听数)。深 UI 层级（10+ 层嵌套面板）会让每次点击走满整条 path，把监听挂在**共同祖先**上通常比挂在每个叶子上更省。
- 命中测试若遍历全部元素，是 O(n)；UGUI 的 GraphicRaycaster 会先用 `RectTransform` 的包围盒做粗筛。全屏 scroll list 里上千个 item 时应做空间划分或按可见区间裁剪。
- `stopImmediatePropagation` 无法被"撤销"：一旦调用，同节点剩余监听永久丢失，谨慎用于第三方监听共存的节点。
- 派发期间**不要结构性修改 UI 树**（增删父子关系）：path 在派发开始时已固定，改树会让 path 上出现已脱离的节点。

## 注意事项与常见坑

1. **在祖先的捕获阶段 `stopPropagation` 会连 target 一起干掉**——想拦冒泡要在冒泡阶段拦。
2. **`bubbles=false` 不等于"不冒泡就什么都不跑"**：target 自己的监听仍然会在第二个循环里跑一次。
3. **`eventPhase` 在 target 上恒为 `AT_TARGET`**，不能用它区分"这是捕获还是冒泡"；要区分得看监听的 `capture` 标志。
4. **`target` 与 `currentTarget` 是两回事**：`target` 全程是最深的那个节点，`currentTarget` 随派发逐节点变化。日志里打错这两个是最常见的调试困惑。
5. **派发中新增监听的生效范围**取决于"该节点是否已被 invoke 过"：已 invoke 的节点不生效（因为克隆发生在 invoke 当刻），未到达的祖先会生效。
6. **穿透要显式设计**：DOM 模型里没有"穿透"，它是游戏侧在命中层加的消费标记；默认让事件一路穿透会导致下层按钮被误触（模态层必须消费）。
7. **不可见/透明元素仍会被命中**：alpha=0 的元素照常参与命中测试与绘制（见本大类 UI 合批 demo 的 fill-rate 一节），应设 `interactable=false` 或直接禁用。

## 参考资料（实际阅读过的权威来源）

- [DOM Standard — §2.9 Dispatching events](https://dom.spec.whatwg.org/#dispatching-events) — event path 构建、捕获/冒泡两个循环、`bubbles` 跳过条件、`invoke` 的克隆与 stop 标志、`inner invoke` 的阶段过滤与 `once`/removed 语义，本 demo 逐条对照实现
- [Unity Learn — Optimizing Unity UI](https://learn.unity.com/tutorial/optimizing-unity-ui) — Graphic Raycaster 每帧 raycast 的行为、alpha=0 元素仍会提交 GPU 与参与命中的说明（用于第 7 条注意事项）
