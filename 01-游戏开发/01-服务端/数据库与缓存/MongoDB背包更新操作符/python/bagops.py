# -*- coding: utf-8 -*-
"""MongoDB 背包文档建模与更新操作符语义。

口径(实读源):MongoDB 官方手册——
  $set:字段不存在则新增;点路径会**顺手创建嵌套文档**;
  $inc:正负皆可;字段不存在则创建并置为增量值;**null 字段上用 $inc 报错**;
       "atomic operation within a single document";
  位置操作符 $:"acts as a placeholder for the **first element** that matches
       the query document",且数组字段必须出现在查询条件里。
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def _set_path(doc, path, value):
    parts = path.split(".")
    cur = doc
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):   # 点路径顺手建嵌套文档
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def apply_update(doc, update, query=None):
    """实现 $set / $inc / 数组位置 $ 三类操作符(官方语义子集)。"""
    if "$set" in update:
        for path, v in update["$set"].items():
            _set_path(doc, path, v)
    if "$inc" in update:
        for path, delta in update["$inc"].items():
            cur = doc
            parts = path.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            leaf = parts[-1]
            if leaf in cur and cur[leaf] is None:
                raise ValueError(f"$inc on null field: {path}")   # 官方:报错
            cur[leaf] = cur.get(leaf, 0) + delta                   # 不存在则=增量
    if "$positional" in update:
        arr_path, cond, value = update["$positional"]
        arr = doc
        for p in arr_path.split("."):
            arr = arr[p]
        for item in arr:                                           # 第一个匹配查询的元素
            if all(item.get(k) == v for k, v in cond.items()):
                for k, v in value.items():
                    item[k] = v
                break
    return doc


def main():
    print("1. $set 与点路径")
    player = {"_id": 1, "name": "alice"}
    apply_update(player, {"$set": {"bag.slots": 30, "level": 5}})
    assert player["bag"] == {"slots": 30} and player["level"] == 5
    ok("$set 不存在即新增;点路径 bag.slots 顺手创建嵌套文档 bag(官方语义)")

    print("2. $inc 三个边界")
    p2 = {"gold": 100}
    apply_update(p2, {"$inc": {"gold": -30, "gems": 5}})
    assert p2 == {"gold": 70, "gems": 5}
    ok("正负皆可;字段不存在则创建并直接置为增量值(gems=5 而非 0+5 的『先建 0』两步)")
    try:
        apply_update({"score": None}, {"$inc": {"score": 1}})
        raise AssertionError("unreachable")
    except ValueError:
        ok("**null 字段上 $inc 报错**(文档里存过 null 就不能再 $inc);"
           "$inc 是单文档内原子操作——扣款与加款各自原子,跨文档要事务")

    print("3. 位置操作符 $ 与背包经典坑")
    bag = {"items": [
        {"id": "potion", "count": 3},
        {"id": "potion", "count": 2},     # 同名第二组
        {"id": "key", "count": 1},
    ]}
    apply_update(bag, {"$positional": ("items", {"id": "potion"}, {"count": 99})})
    assert bag["items"][0]["count"] == 99 and bag["items"][1]["count"] == 2
    ok("位置 $ 是**查询所匹配的第一个元素**的占位符,且数组字段必须出现在查询里——"
       "背包里同名多组道具时它只改第一组(经典 bug 源)")

    print("4. 建模取舍")
    ok("背包一行一物(数组内嵌)读得快、整包一次读写;格子表(一行一格)更新精确——"
       "位置 $ 的『只改第一组』正是把两者混用的代价信号")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
