"""selfcheck_nodelifecycle.py — nodelifecycle.py 的自检（模型与自检分文件）。"""

from __future__ import annotations

from nodelifecycle import *  # noqa: F401,F403

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


def main() -> None:
    w = World()

    # 1) 回调顺序：父 enter_tree → 子 enter_tree → 子 ready → 父 ready
    parent = Node("parent", w)
    child = Node("child", w)
    grand = Node("grand", w)
    child.add_child(grand)
    parent.add_child(child)
    w.root.add_child(parent)
    check("整体回调顺序符合官方文档",
          w.log == ["parent:enter_tree", "child:enter_tree", "grand:enter_tree",
                    "grand:onready", "grand:ready", "child:onready", "child:ready",
                    "parent:onready", "parent:ready"], str(w.log))
    check("_enter_tree 父先于子",
          w.log.index("parent:enter_tree") < w.log.index("child:enter_tree"))
    check("_ready 子先于父（逆序）",
          w.log.index("grand:ready") < w.log.index("child:ready") < w.log.index("parent:ready"))
    check("@onready 在 _ready 之前完成",
          all(w.log.index(f"{n}:onready") < w.log.index(f"{n}:ready")
              for n in ("parent", "child", "grand")))

    # 2) 挂到游离父节点上不会触发任何回调
    w2 = World()
    detached = Node("detached", w2)
    kid = Node("kid", w2)
    detached.add_child(kid)
    check("父节点不在树中 ⇒ 子节点不触发回调", w2.log == [] and not kid.in_tree, str(w2.log))
    w2.root.add_child(detached)
    check("整棵挂上去后回调才补触发",
          w2.log == ["detached:enter_tree", "kid:enter_tree", "kid:onready", "kid:ready",
                     "detached:onready", "detached:ready"], str(w2.log))

    # 3) queue_free 延迟到本帧处理结束后；free() 立即
    w3 = World()
    a = Node("a", w3)
    a_child = Node("a_child", w3)
    a.add_child(a_child)
    w3.root.add_child(a)
    a.queue_free()
    check("queue_free 当帧仍在树里（还没删）", a.in_tree and a.alive)
    w3.frame(16_666)
    check("queue_free 的节点在本帧仍跑了一次 _process", a.process_calls == 1,
          f"{a.process_calls}")
    check("帧末才真正释放", not a.alive and a not in w3.root.children)
    check("释放父节点会连带释放子节点", not a_child.alive)

    w4 = World()
    b = Node("b", w4)
    w4.root.add_child(b)
    b.free()
    check("free() 立即生效（不等帧末）", not b.alive and b not in w4.root.children)

    # 4) remove_child 只触发 _exit_tree，节点本身还活着
    w5 = World()
    c = Node("c", w5)
    cc = Node("cc", w5)
    c.add_child(cc)
    w5.root.add_child(c)
    w5.log.clear()
    w5.root.remove_child(c)
    check("remove_child 触发 _exit_tree", w5.log[:2] == ["c:exit_tree", "cc:exit_tree"],
          str(w5.log))
    check("remove_child 后节点仍存活且仍带着孩子", c.alive and cc in c.children)

    # 5) 帧循环：_process 随帧率，_physics_process 固定 60/秒
    w6 = World()
    d = Node("d", w6, physics=True)
    w6.root.add_child(d)
    for dt in pacing(1_000_000, 16_666):
        w6.frame(dt)
    fps60 = (d.process_calls, d.physics_calls)

    w7 = World()
    e = Node("e", w7, physics=True)
    w7.root.add_child(e)
    for dt in pacing(1_000_000, 33_333):
        w7.frame(dt)
    fps30 = (e.process_calls, e.physics_calls)

    check("1 秒内 _physics_process 恒为 60 次（与帧率无关）",
          fps60[1] == fps30[1] == 60, f"{fps60[1]} vs {fps30[1]}")
    check("_process 次数 = 帧数（随帧率变化）", fps60[0] != fps30[0],
          f"60fps={fps60[0]} / 30fps={fps30[0]}")
    check("_physics_process 的 delta 恒定 = 1/physics_fps",
          set(round(x, 9) for x in d.physics_deltas) == {round(STEP_US / 1_000_000.0, 9)},
          f"{d.physics_deltas[:2]}")
    check("_process 的 delta = 距上次调用的秒数（帧长）",
          all(round(x, 6) == round(16_666 / 1_000_000.0, 6) for x in d.deltas[:-1]),
          f"{d.deltas[:2]}")
    check("delta 累计 = 总时长", abs(sum(d.deltas) - 1.0) < 1e-6, f"{sum(d.deltas):.6f}")

    # 6) set_process(False) 关掉 _process（不影响 _physics_process）
    w8 = World()
    f = Node("f", w8, physics=True)
    w8.root.add_child(f)
    f.process_on = False
    w8.frame(16_666)
    check("关掉 processing 后 _process 不再调用", f.process_calls == 0)
    check("关掉 processing 不影响 _physics_process", f.physics_calls >= 1)

    # 7) 一帧内 0 或 2 次物理步：_process 与物理不同步
    w9 = World()
    g = Node("g", w9, physics=True)
    w9.root.add_child(g)
    w9.frame(1_000)                       # 1ms：不足一个物理步
    check("短帧不触发物理步", g.physics_calls == 0 and g.process_calls == 1)
    w9.frame(33_333)
    check("长帧可触发多个物理步", g.physics_calls == 2, f"{g.physics_calls}")

    # 8) 实例化：模板可重复实例化且互不影响
    w10 = World()
    scene = PackedScene({"name": "Enemy", "children": {"Sprite": {}, "Hitbox": {}}})
    i1 = scene.instantiate(w10)
    i2 = scene.instantiate(w10)
    check("instantiate 返回的树不在场景树中", not i1.in_tree and i1.parent is None)
    check("两次实例化得到不同的节点对象", i1 is not i2 and i1.name == i2.name == "Enemy")
    i1.children[0].name = "Renamed"
    check("改一个实例不影响另一个",
          [c.name for c in i2.children] == ["Sprite", "Hitbox"],
          f"{[c.name for c in i2.children]}")
    w10.root.add_child(i1)
    check("add_child 之后才触发实例化节点的回调",
          w10.log[:3] == ["Enemy:enter_tree", "Renamed:enter_tree", "Hitbox:enter_tree"],
          str(w10.log[:3]))

    # 9) 节点路径：.. 与 /root 绝对路径
    check('get_node("..") 取父节点', i1.children[0].get_node("..") is i1)
    check('get_node("/root/Enemy") 取绝对路径',
          i1.get_node("/root/Enemy") is i1)

    print(f"\n全部 {_ASSERTIONS} 条断言通过")


if __name__ == "__main__":
    main()
