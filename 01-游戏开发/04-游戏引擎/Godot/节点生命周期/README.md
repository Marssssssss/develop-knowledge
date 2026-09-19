# Godot 节点生命周期与场景树

## 简介

- Godot 的一切都是**节点树**：场景是模板（`PackedScene`），实例化出来的节点树挂进 `SceneTree` 才开始有回调和帧处理。理解"什么时候回调、按什么顺序回调"，是写 Godot 代码最容易踩坑也最基础的一块。
- 官方文档给出的完整顺序是一条铁律：

```text
父 _enter_tree()  →  子 _enter_tree()  →  子 _ready()  →  父 _ready()   （对整棵子树递归）
```

- 本 demo 用 Python + Go 复刻这套语义：**回调顺序、延迟删除、固定/可变帧处理的差异、实例化的两步走**，共 28 条断言。

## 原理详解

### 1. 进入树 vs 就绪，是两个不同的通知

| 通知 | 时机 | 顺序 |
| --- | --- | --- |
| `NOTIFICATION_ENTER_TREE` / `_enter_tree()` | 节点（及其子树）加入 SceneTree 时 | **父先于子**（自顶向下） |
| `NOTIFICATION_READY` / `_ready()` | 所有节点都进入树之后 | **子先于父**（自底向上，逆序） |

这个顺序差异是设计使然：`_ready()` 的语义是"我和我的孩子都已在树里"，所以只有孩子的 `_ready()` 跑完，父节点才算 ready——这也是为什么在父节点的 `_ready()` 里 `get_node("Child")` 一定拿得到。

`@onready` 注解的成员在 `_ready()` **之前**初始化，所以它也享受这个保证。

**游离（detached）节点不触发任何回调**：把子节点挂到一个还没进树的父节点上，什么都不会发生；等整棵挂进 `SceneTree` 时才一次性补上（顺序不变）。

### 2. 删除：queue_free 延迟，free 立即

- `queue_free()`：把节点排进队列，在**当前帧处理结束之后**才删除并释放内存——避免"在 `_process()` 里删自己导致后续帧处理踩空"。
- `free()`：**立即**销毁，任何已有引用会**立刻变 null**。官方明确推荐 `queue_free()`，"除非你很清楚自己在做什么"。
- 两者都会**连带释放该节点的全部子节点**——所以删一整棵子树只需要 free 最上面那个父节点。
- `remove_child()` 不是删除：它触发 `_exit_tree()`，但节点对象还活着、还带着自己的孩子。

### 3. 两种帧处理

| | `_process(delta)` | `_physics_process(delta)` |
| --- | --- | --- |
| 频率 | 随帧率变化 | 固定间隔，Physics Fps **默认 60 次/秒** |
| `delta` | 距上次调用经过的**秒数** | 恒为 `1 / physics_fps` |
| 与物理同步 | **不同步**（文档原话） | 同步 |
| 开关 | `Node.set_process()` | `Node.set_physics_process()` |

一帧里物理步可以是 0 次（帧太短）也可以是多次（掉帧后追赶），而 `_process()` 每帧恰好一次——这就是"不同步"的具体含义。

## 对比 / 选型

| 场景 | 该用哪个 |
| --- | --- |
| 移动、输入、UI 动画、计时器 | `_process`（每帧一次，delta 自取） |
| 刚体/运动学体、碰撞相关、需要可复现的逻辑 | `_physics_process`（固定步长） |
| 只依赖"节点和它的孩子都已就位" | `_ready()` |
| 需要在"刚进树、但孩子可能还没进"时做点事 | `_enter_tree()` |
| 立刻释放、且确定没人再引用 | `free()`；否则一律 `queue_free()` |

## 环境准备

- Python 3.8+（标准库，零依赖）
- Go 1.21+（零依赖）
- 不需要安装 Godot：复刻的是**回调与帧循环语义**

## 运行方式

```bash
python3 python/selfcheck_nodelifecycle.py   # 28 条断言
cd go && go run nodelifecycle.go
```

## 关键代码片段

顺序差异就藏在这两个递归的写法里（Python 版）：

```python
def enter_tree(self, node: Node) -> None:      # 父先于子
    node.in_tree = True
    node._enter_tree()
    for ch in list(node.children):
        self.enter_tree(ch)

def mark_ready(self, node: Node) -> None:      # 子先于父（逆序）
    for ch in list(node.children):
        self.mark_ready(ch)
    self.log.append(f"{node.name}:onready")    # @onready 在 _ready 之前
    node.ready = True
    node._ready()
```

帧循环（固定步长物理 + 每帧一次 idle + 帧末处理延迟删除）：

```python
def frame(self, dt_us: int) -> None:
    self.physics_acc += dt_us
    while self.physics_acc >= STEP_US:          # 一帧可跑 0 次或多次
        self.physics_acc -= STEP_US
        for n in self.all_nodes():
            if n.alive and n.in_tree and n.physics_on:
                n._physics_process(STEP_US / 1_000_000.0)
    for n in self.all_nodes():                  # _process 每帧恰好一次
        if n.alive and n.in_tree and n.process_on:
            n._process(dt_us / 1_000_000.0)
    for n in list(self.pending_free):           # queue_free 到帧末才落刀
        self.free_node(n)
```

## 性能与边界

- 1 秒内：60fps 与 30fps 两种帧率下 `_physics_process` **都是 60 次**，而 `_process` 分别是 61 次与 31 次（按帧数走）——这正是物理逻辑放进 `_physics_process` 的理由。
- `_physics_process` 的 `delta` 恒为 `1 / physics_fps`；`_process` 的 `delta` 累计恰好等于总时长（浮点误差 < 1e-6）。
- 固定物理步长 + 变帧率，本质就是《Fix Your Timestep!》的 accumulator 方案（见同目录 `../../03-固定时间步/`）。Godot 的取舍是：物理步不与渲染帧对齐，靠引擎内部的插值/外插平滑表现。
- 大场景的 `_process` 链会顺着整棵子树递归，节点越多每帧遍历成本越高；把不需要每帧跑的节点 `set_process(false)` 是常规优化。

## 注意事项与常见坑

- **别在 `_enter_tree()` 里取子节点**：那时孩子还没进树。取子节点请放 `_ready()`（或在 `@onready` 成员里）。
- **别在 `_ready()` 里假设父节点已 ready**：`_ready()` 是自底向上的，父节点的 `_ready()` 还没跑——要拿父节点状态请用 `_enter_tree()`，或改成父节点下发。
- **动态加子节点要小心时机**：给一个已经在树里的父节点 `add_child()`，子节点会**立即**收到 `_enter_tree` + `_ready`；给游离父节点加则什么都不发生。
- **`free()` 之后引用变 null**：与 `queue_free()` 混用很容易出现"这一帧还有效、下一帧变 null"的隐蔽空指针。
- **删父节点会带走整棵子树**：只想摘掉一个分支用 `remove_child()`。
- **物理与 idle 不同步**：在 `_process()` 里读刚体的位置会看到插值抖动；物理相关逻辑一律放 `_physics_process()`。
- **`delta` 是秒不是毫秒**，`_process(delta)` 里做"每秒 X"的运算要乘 `delta`。

## 参考资料（实际阅读过的权威来源）

- [Godot — Node class reference](https://docs.godotengine.org/en/stable/classes/class_node.html) — `_enter_tree` / `_ready` / `_exit_tree` 的触发时机与顺序（父先于子 / 子先于父）、`queue_free` 与 `free` 连带释放子节点、`request_ready`、信号 `ready` / `tree_exiting`。
- [Godot — Nodes and scene instances](https://docs.godotengine.org/en/stable/tutorials/scripting/nodes_and_scene_instances.html) — `queue_free()` 延迟到本帧处理之后、`free()` 立即且引用变 null、实例化两步（load → `PackedScene.instantiate()`）、`@onready` 与 `$` 语法糖、`/root` 绝对路径。
- [Godot — Idle and Physics Processing](https://docs.godotengine.org/en/stable/tutorials/scripting/idle_and_physics_processing.html) — `_process` 频率随帧率、`delta` 是距上次调用的秒数、`_physics_process` 固定间隔（默认 60）、两者不同步、`set_process()` 开关。
