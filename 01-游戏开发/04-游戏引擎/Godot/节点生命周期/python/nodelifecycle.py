"""nodelifecycle.py — Godot 节点生命周期与场景/帧处理顺序的最小模型与自检.

事实来源（本机 curl 落地后实读的 Godot 官方文档）：
  * docs.godotengine.org/en/stable/classes/class_node.html
      —— 节点加入 SceneTree 时先 NOTIFICATION_ENTER_TREE / _enter_tree()，
         **父节点先于子节点**；全部加入后收到 NOTIFICATION_READY / _ready()，
         对一组节点 **_ready() 从子节点开始逆序到父节点**；
         完整顺序：父 _enter_tree → 子 _enter_tree → 子 _ready → 父 _ready（递归）；
         Object.free() 或 queue_free() 都会连带释放它的所有子节点。
  * docs.godotengine.org/en/stable/tutorials/scripting/nodes_and_scene_instances.html
      —— queue_free() 把节点排到**当前帧处理结束之后**删除；free() 立即销毁，
         已有引用会立刻变 null，官方推荐 queue_free()；
         实例化分两步：load(PackedScene) 得到资源 → PackedScene.instantiate() 得到节点树；
         @onready 成员在 _ready() **之前**初始化；get_node 支持 ".." 与 "/root" 绝对路径。
  * docs.godotengine.org/en/stable/tutorials/scripting/idle_and_physics_processing.html
      —— _process(delta) 的频率取决于帧率，delta = 距上次调用经过的秒数；
         _physics_process(delta) 按固定间隔跑（Physics Fps 默认 60 次/秒）；
         _process 与物理**不同步**；可用 Node.set_process() 开关。
"""

from __future__ import annotations

from typing import List, Optional

PHYSICS_FPS = 60
STEP_US = 1_000_000 // PHYSICS_FPS        # 16.666 ms

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


class Node:
    def __init__(self, name: str, world: "World", physics: bool = False) -> None:
        self.name = name
        self.world = world
        self.parent: Optional["Node"] = None
        self.children: List["Node"] = []
        self.in_tree = False
        self.ready = False
        self.alive = True
        self.process_on = True
        self.physics_on = physics
        self.process_calls = 0
        self.physics_calls = 0
        self.deltas: List[float] = []
        self.physics_deltas: List[float] = []

    # ---- 树操作 ----------------------------------------------------
    def add_child(self, child: "Node") -> None:
        if child.parent is not None:
            child.parent.children.remove(child)
        child.parent = self
        self.children.append(child)
        if self.in_tree:                       # 父已在树中 ⇒ 立刻触发回调
            self.world.enter_tree(child)
            self.world.mark_ready(child)

    def remove_child(self, child: "Node") -> None:
        if child in self.children:
            self.children.remove(child)
        child.parent = None
        if child.in_tree:
            self.world.exit_tree(child)

    def queue_free(self) -> None:
        self.world.pending_free.append(self)

    def free(self) -> None:
        self.world.free_node(self)

    # ---- 回调（由 World 调用） --------------------------------------
    def _enter_tree(self) -> None:
        self.world.log.append(f"{self.name}:enter_tree")

    def _ready(self) -> None:
        self.world.log.append(f"{self.name}:ready")

    def _exit_tree(self) -> None:
        self.world.log.append(f"{self.name}:exit_tree")

    def _process(self, delta: float) -> None:
        self.process_calls += 1
        self.deltas.append(delta)

    def _physics_process(self, delta: float) -> None:
        self.physics_calls += 1
        self.physics_deltas.append(delta)

    def get_node(self, path: str) -> Optional["Node"]:
        if path == "..":
            return self.parent
        if path.startswith("/root"):
            node = self.world.root
            for part in path.split("/")[2:]:
                if not part:
                    continue
                node = next((c for c in node.children if c.name == part), None)
                if node is None:
                    return None
            return node
        return next((c for c in self.children if path in (c.name,)), None)


class PackedScene:
    """场景模板：instantiate() 返回一棵**不在树里**的节点树。"""

    def __init__(self, spec: dict) -> None:
        self.spec = spec

    def instantiate(self, world: "World") -> Node:
        """返回一棵**不在场景树里**的节点树（还要 add_child 才会触发回调）。"""

        def build(name: str, spec: dict) -> Node:
            node = Node(name, world)
            for child_name, child_spec in (spec or {}).items():
                node.children.append(build(child_name, child_spec))
                node.children[-1].parent = node
            return node

        return build(self.spec["name"], self.spec.get("children", {}))


class World:
    def __init__(self) -> None:
        self.root = Node("root", self)
        self.root.in_tree = True
        self.root.ready = True
        self.log: List[str] = []
        self.pending_free: List[Node] = []
        self.physics_acc = 0
        self.elapsed_us = 0

    # ---- 回调派发 ---------------------------------------------------
    def enter_tree(self, node: Node) -> None:
        """_enter_tree：父先于子（自顶向下）。"""
        node.in_tree = True
        node._enter_tree()
        for ch in list(node.children):
            self.enter_tree(ch)

    def mark_ready(self, node: Node) -> None:
        """_ready：子先于父（自底向上）；@onready 在 _ready 之前初始化。"""
        for ch in list(node.children):
            self.mark_ready(ch)
        self.log.append(f"{node.name}:onready")
        node.ready = True
        node._ready()

    def exit_tree(self, node: Node) -> None:
        node.in_tree = False
        node._exit_tree()
        for ch in list(node.children):
            self.exit_tree(ch)

    def free_node(self, node: Node) -> None:
        if not node.alive:
            return
        if node.parent is not None and node in node.parent.children:
            node.parent.children.remove(node)
        if node.in_tree:
            self.exit_tree(node)
        for ch in list(node.children):      # 释放父节点会连带释放所有子节点
            self.free_node(ch)
        node.children.clear()
        node.parent = None
        node.alive = False
        self.log.append(f"{node.name}:free")

    def all_nodes(self, node: Optional[Node] = None) -> List[Node]:
        node = node or self.root
        out = [node]
        for ch in node.children:
            out.extend(self.all_nodes(ch))
        return out

    # ---- 帧循环 ------------------------------------------------------
    def frame(self, dt_us: int) -> None:
        self.elapsed_us += dt_us
        self.physics_acc += dt_us
        phys_steps = 0
        while self.physics_acc >= STEP_US:
            self.physics_acc -= STEP_US
            phys_steps += 1
            for n in self.all_nodes():
                if n.alive and n.in_tree and n.physics_on:
                    n._physics_process(STEP_US / 1_000_000.0)
        for n in self.all_nodes():
            if n.alive and n.in_tree and n.process_on:
                n._process(dt_us / 1_000_000.0)
        for n in list(self.pending_free):   # queue_free：当前帧处理结束后才删
            self.free_node(n)
            self.pending_free.remove(n)


def pacing(total_us: int, frame_us: int) -> List[int]:
    out, left = [], total_us
    while left > 0:
        step = min(frame_us, left)
        out.append(step)
        left -= step
    return out

