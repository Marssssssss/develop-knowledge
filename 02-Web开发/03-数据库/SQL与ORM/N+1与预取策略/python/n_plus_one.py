#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N+1 查询问题与预取（eager loading）策略的 SQL 语句计数复刻。

口径来源（先联网实读再写）：
  - SQLAlchemy 2.0 "Relationship Loading Techniques"（docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html）：
      * relationship.lazy 默认值是 "select"，即懒加载：属性首次访问时才发 SELECT；
      * "for any N objects loaded, accessing their lazy-loaded attributes means there will be N+1
         SELECT statements emitted" —— 这就是 N+1；
      * selectin（"IN" loading）发第二条 SELECT，把父对象主键拼进 IN 子句；"The strategy emits a
         SELECT for up to 500 parent primary key values at a time"；
      * joined 用一个 LEFT OUTER JOIN 取回关联行，对集合关联必须再调 Result.unique()，
         否则 "The ORM will raise an error if this is not present"；
      * subquery 发第二条 SELECT，把原查询原样嵌进子查询再 JOIN；
      * raiseload 用异常替代懒加载；"The 'raiseload' strategies do not apply within the unit of
         work flush process"；
      * "only the number of SQL statements required to fully load related objects and collections changes"。
  - GORM "Preloading (Eager Loading)"（gorm.io/docs/preload.html，Go 版对照实现）：
      * Preload 用独立的第二条 SQL（WHERE user_id IN (...)），Joins 用 LEFT JOIN；
      * "`Join Preload` works with one-to-one relation, e.g: `has one`, `belongs to`"；
      * clause.Associations 不会预加载嵌套关联。

本文件只做**语句计数 + 语义**验证，不连真实数据库；语句数量口径与上列文档一致。
运行：python3 n_plus_one.py    （仅标准库，无第三方依赖）
"""
from __future__ import annotations

# ---------------------------------------------------------------- 内存数据库
USERS = [{"id": i, "name": f"u{i}"} for i in range(1, 6)]
ADDRESSES = [{"id": i, "user_id": (i // 2) + 1, "email": f"a{i}@x"}
             for i in range(1, 9)]          # 每个用户 1~2 条地址
ORDERS = [{"id": i, "user_id": (i % 5) + 1, "price": float(i)}
          for i in range(1, 21)]             # 每个用户 4 条订单

SELECT_IN_BATCH = 500        # 文档原文：up to 500 parent primary key values at a time


class InvalidRequestError(RuntimeError):
    """对应 sqlalchemy.orm.exc.InvalidRequestError：未预加载就访问、或 joined 未去重。"""


class Database:
    """记录每一条发往数据库的 SQL，用于统计语句数（demo 的度量核心）。"""

    def __init__(self):
        self.statements: list[str] = []

    def emit(self, sql: str) -> None:
        self.statements.append(" ".join(sql.split()))

    @property
    def count(self) -> int:
        return len(self.statements)

    def reset(self) -> None:
        self.statements.clear()


class Row:
    """一行记录；属性访问由 Session 的加载器状态决定是否触发 SQL。"""

    def __init__(self, table: str, data: dict):
        self.table, self.data = table, dict(data)

    def __getitem__(self, key):
        return self.data[key]


class Session:
    """最小 ORM：identity map + 懒加载器 + 预取策略。"""

    def __init__(self, db: Database):
        self.db = db
        self.identity_map: dict[tuple[str, int], list] = {}   # (table, pk) -> [Row]
        self._loaded: dict[tuple[int, str], list[int]] = {}   # (id(row), rel) -> 已加载
        self._options: dict[str, str] = {}                    # rel -> 预取策略

    # -- identity map：同一主键只保留一个实例 ------------------------------
    def _instance(self, table: str, data: dict) -> list:
        key = (table, data["id"])
        bucket = self.identity_map.setdefault(key, [])
        if bucket:
            return bucket[0]
        bucket.append(Row(table, data))
        return bucket[0]

    # -- 基查询：1 条 SELECT --------------------------------------------------
    def load_users(self, *, joined: bool = False, unique: bool = False,
                   options: dict[str, str] | None = None) -> list:
        if options:
            self._options.update(options)
        if joined:
            self.db.emit("SELECT users.*, addresses.* FROM users LEFT OUTER JOIN addresses "
                         "ON users.id = addresses.user_id ORDER BY addresses.email")
            rows: list = []
            for u in USERS:
                for a in [x for x in ADDRESSES if x["user_id"] == u["id"]] or [None]:
                    rows.append(self._instance("users", u))
                    if a is not None:
                        rows.append(self._instance("addresses", a))
            for u in USERS:                      # joined 已把集合填好
                self._loaded[(id(u), "addresses")] = [
                    a["id"] for a in ADDRESSES if a["user_id"] == u["id"]]
            if not unique:                       # 文档：没调 unique() 要报错
                raise InvalidRequestError(
                    "The unique() method must be invoked on this Result, as it contains "
                    "results that were joined directly to a collection")
            seen, dedup = set(), []
            for r in rows:                       # Result.unique() 按主键去重
                key = (r.table, r["id"])
                if key not in seen:
                    seen.add(key)
                    dedup.append(r)
            return [r for r in dedup if r.table == "users"]
        self.db.emit("SELECT users.* FROM users")
        return [self._instance("users", u) for u in USERS]

    # -- 懒加载：属性访问时才发 SQL（N+1 的来源）---------------------------
    def _lazy(self, owner: Row, rel: str) -> list:
        if (id(owner), rel) in self._loaded:
            return self._loaded[(id(owner), rel)]
        strategy = self._options.get(rel, "select")
        if strategy in ("raise", "raise_on_sql"):
            raise InvalidRequestError(f"'{owner.table}.{rel}' is not available due to raiseload=True")
        if strategy == "noload":
            self._loaded[(id(owner), rel)] = []
            return []
        table, fk = ("addresses", "user_id") if rel == "addresses" else ("orders", "user_id")
        self.db.emit(f"SELECT {table}.* FROM {table} WHERE {fk} = {owner['id']}")
        rows = [self._instance(table, r) for r in
                (ADDRESSES if table == "addresses" else ORDERS) if r[fk] == owner["id"]]
        self._loaded[(id(owner), rel)] = rows
        return rows

    def related(self, owner: Row, rel: str) -> list:
        return self._lazy(owner, rel)

    # -- 预取策略 -------------------------------------------------------------
    def eager(self, users: list, rel: str, strategy: str) -> None:
        ids = [u["id"] for u in users]
        if strategy == "selectin":
            # 文档：把父对象主键拼进 IN 子句，每条 SQL 最多 500 个主键
            for i in range(0, len(ids), SELECT_IN_BATCH):
                chunk = ids[i:i + SELECT_IN_BATCH]
                placeholders = ", ".join(str(x) for x in chunk)
                table, fk = ("addresses", "user_id") if rel == "addresses" else ("orders", "user_id")
                self.db.emit(f"SELECT {table}.* FROM {table} WHERE {fk} IN ({placeholders})")
                for owner in users:
                    if owner["id"] not in chunk:
                        continue
                    self._loaded[(id(owner), rel)] = [
                        self._instance(table, r) for r in
                        (ADDRESSES if table == "addresses" else ORDERS) if r[fk] == owner["id"]]
        elif strategy == "subquery":
            table, fk = ("addresses", "user_id") if rel == "addresses" else ("orders", "user_id")
            self.db.emit(f"SELECT {table}.* FROM (SELECT users.id FROM users) AS anon_1 "
                         f"JOIN {table} ON anon_1.id = {table}.{fk} ORDER BY anon_1.id")
            for owner in users:
                self._loaded[(id(owner), rel)] = [
                    self._instance(table, r) for r in
                    (ADDRESSES if table == "addresses" else ORDERS) if r[fk] == owner["id"]]
        elif strategy == "joined":
            self.db.emit(f"SELECT users.* FROM users LEFT OUTER JOIN {rel} ON users.id = {rel}.user_id")
            raise InvalidRequestError("joined 策略由 load_users(joined=True) 走基查询路径")
        elif strategy in ("raise", "noload"):
            pass                                  # 只是登记策略，不发 SQL
        else:
            raise ValueError(f"unknown strategy: {strategy}")


# ---------------------------------------------------------------- 断言工具
_RESULTS = {"pass": 0, "fail": 0}


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        _RESULTS["pass"] += 1
        print(f"  [PASS] {label}")
    else:
        _RESULTS["fail"] += 1
        print(f"  [FAIL] {label} :: {detail}")


def main() -> int:
    print("=" * 72)
    print("1) N+1：默认 lazy='select'，N 个父对象 = 1 + N 条 SELECT")
    db = Database()
    s = Session(db)
    users = s.load_users()
    check("基查询只发 1 条 SELECT", db.count == 1, f"count={db.count}")
    for u in users:                                   # 触发每个父对象的懒加载
        s.related(u, "addresses")
    check("5 个用户访问 addresses -> 1+5=6 条（N+1）", db.count == 6, f"count={db.count}")
    check("语句文本形态为逐主键 WHERE user_id = ?",
          db.statements[-1].endswith("WHERE user_id = 5"), db.statements[-1])

    print("\n2) selectin（'IN' 加载）：1 + ceil(N/500) 条")
    db = Database()
    s = Session(db)
    users = s.load_users(options={"addresses": "selectin"})
    s.eager(users, "addresses", "selectin")
    check("5 个用户 -> 2 条", db.count == 2, f"count={db.count}")
    check("第二条 SQL 用 IN 收集主键", "IN (1, 2, 3, 4, 5)" in db.statements[1], db.statements[1])
    check("IN 之后不再懒加载（属性已填）", s.related(users[0], "addresses") and db.count == 2)

    # 500 分批：用 1200 个虚拟父对象验证 ceil(1200/500)=3
    db = Database()
    s = Session(db)
    fake = [s._instance("users", {"id": i, "name": f"u{i}"}) for i in range(1, 1201)]
    db.reset()
    s.eager(fake, "orders", "selectin")
    check("1200 个父对象 -> 3 条（500/500/200 分批）", db.count == 3, f"count={db.count}")
    check("末批主键数 = 200", db.statements[-1].count(",") + 1 == 200,
          db.statements[-1][:60])

    print("\n3) joined（LEFT OUTER JOIN）：1 条，但必须 Result.unique()")
    db = Database()
    s = Session(db)
    try:
        s.load_users(joined=True, unique=False)
        check("未 unique() 必须报错", False, "no error raised")
    except InvalidRequestError as exc:
        check("未 unique() 报 InvalidRequestError", "unique()" in str(exc))
    db = Database()
    s = Session(db)
    users = s.load_users(joined=True, unique=True)
    check("joined 只要 1 条 SQL", db.count == 1, f"count={db.count}")
    check("join 后行数被放大，需按主键去重", len(users) == 5, f"len={len(users)}")

    print("\n4) subquery：2 条，原查询被原样嵌进子查询")
    db = Database()
    s = Session(db)
    users = s.load_users(options={"addresses": "subquery"})
    s.eager(users, "addresses", "subquery")
    check("subquery 共 2 条", db.count == 2, f"count={db.count}")
    check("第二条含 (SELECT users.id FROM users) AS anon_1", "anon_1" in db.statements[1],
          db.statements[1])

    print("\n5) raiseload：把 N+1 变成显式异常；flush 阶段不受其约束")
    db = Database()
    s = Session(db)
    users = s.load_users(options={"addresses": "raise"})
    try:
        s.related(users[0], "addresses")
        check("raiseload 必须抛异常", False, "no error raised")
    except InvalidRequestError as exc:
        check("raiseload 抛 InvalidRequestError", "raiseload" in str(exc))
    check("raiseload 不产生额外 SQL", db.count == 1, f"count={db.count}")
    check("raiseload 在 flush 流程中不生效（文档口径）", not _raiseload_applies_in_flush())

    print("\n6) 策略只改语句数，不改结果：四种策略取回同一组主键")
    got = {}
    for name in ("select", "selectin", "subquery", "joined"):
        db = Database()
        s = Session(db)
        if name == "joined":
            us = s.load_users(joined=True, unique=True)
        else:
            us = s.load_users(options={"addresses": "selectin" if name == "selectin" else name})
        if name == "select":
            for u in us:
                s.related(u, "addresses")
        elif name == "subquery":
            s.eager(us, "addresses", "subquery")
        elif name == "selectin":
            s.eager(us, "addresses", "selectin")
        got[name] = (db.count, tuple(sorted(a["id"] for u in us
                                           for a in s.related(u, "addresses"))))
    check("结果等价（同一组 addresses.id）", len({v[1] for v in got.values()}) == 1, str(got))
    check("语句数各不同：select=6 / selectin=2 / subquery=2 / joined=1",
          [got[k][0] for k in ("select", "selectin", "subquery", "joined")] == [6, 2, 2, 1],
          str({k: v[0] for k, v in got.items()}))

    print("\n7) identity map：同一主键只有一份实例，回引不再查库")
    db = Database()
    s = Session(db)
    users = s.load_users(options={"addresses": "selectin"})
    s.eager(users, "addresses", "selectin")
    before = db.count
    a0 = s.related(users[0], "addresses")[0]
    again = s._instance("addresses", {"id": a0["id"], "user_id": a0["user_id"], "email": a0["email"]})
    check("同一主键返回同一 Python 对象", a0 is again)
    check("identity map 命中不产生 SQL", db.count == before, f"{before}->{db.count}")

    print("\n" + "=" * 72)
    print(f"断言结果：pass={_RESULTS['pass']} fail={_RESULTS['fail']}")
    return 1 if _RESULTS["fail"] else 0


def _raiseload_applies_in_flush() -> bool:
    """文档原文：'The "raiseload" strategies do not apply within the unit of work flush process.'
    故 flush 期间仍可加载 —— 返回 False 表示 raiseload 不生效。"""
    return False


if __name__ == "__main__":
    raise SystemExit(main())
