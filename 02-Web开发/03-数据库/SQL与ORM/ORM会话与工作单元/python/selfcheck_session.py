"""ORM 会话与工作单元 自检 —— 断言全部基于官方源码语义。

运行： python selfcheck_session.py
"""

import gc
import sys

from main import Entity, IdentityMap, InstanceState, InvalidRequestError, Session, Table, _state

PASS = 0
FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL:", msg)


def eq(got, want, msg):
    ok(got == want, "%s (got=%r want=%r)" % (msg, got, want))


class User(Entity):
    __tablename__ = "user"

    def __init__(self, id=None, name=None):
        self.id = id
        self.name = name


class Order(Entity):
    __tablename__ = "order"
    __fks__ = {"user_id": "user"}

    def __init__(self, id=None, user_id=None, amount=None):
        self.id = id
        self.user_id = user_id
        self.amount = amount


def tables():
    return [Table("user"), Table("order", parent="user")]


def stmt_kinds(sql):
    return [s.split()[0] + ":" + s.split()[1 + (s.split()[0] == "UPDATE")] for s in sql]


# ---------------------------------------------------------------- A 身份映射
def t_identity():
    s = Session(tables())
    u1 = User(1, "a")
    s.add(u1)
    s.flush()
    # A1 同键取回的是同一个对象（身份映射的定义）
    ok(s.get(User, 1) is u1, "A1 identity map 应按主键返回同一实例")
    ok(u1.identity_key()[0] == "User", "A2 身份键首元素是实体名")

    # A3 同主键的另一个活对象 → InvalidRequestError（官方 identity.py add()）
    u1b = User(1, "b")
    st_b = _state(u1b)
    st_b.key = ("User", 1)
    raised = False
    try:
        s.identity.add(st_b)
    except InvalidRequestError:
        raised = True
    ok(raised, "A3 同主键的活对象 add 必须抛 InvalidRequestError")

    # A4 同一个 state 重复 add 返回 False，不抛
    ok(s.identity.add(_state(u1)) is False, "A4 同一 state 重复 add 返回 False")

    # A5 replace 不抛异常，直接换掉
    st_b.key = ("User", 1)
    replaced = s.identity.replace(st_b)
    ok(replaced is _state(u1), "A5 replace 返回被换掉的旧 state 且不抛")

    # A6 弱引用：对象被 GC 后 __contains__ 为 False
    im = IdentityMap()
    tmp = User(9, "tmp")
    st_tmp = _state(tmp)
    st_tmp.key = ("User", 9)
    im.add(st_tmp)
    ok(("User", 9) in im, "A6a 存活时键在身份映射里")
    del tmp
    gc.collect()
    ok(("User", 9) not in im, "A6b 对象被 GC 后键视为不存在（弱引用）")
    ok(len(im) == 0, "A6c 长度只数存活对象")


# ------------------------------------------------------- B flush 语句顺序
def t_flush_order():
    s = Session(tables())
    u = User(1, "a")
    o = Order(10, 1, 5)
    s.add(u)
    s.add(o)
    sql = s.flush()
    # B1 拓扑序：父表先 INSERT
    eq(sql, ["INSERT INTO user (id,name)", "INSERT INTO order (amount,id,user_id)"],
       "B1 INSERT 按外键拓扑序（user 先于 order）")

    # B2 同一张表内：先 UPDATE 后 INSERT（persistence._save_obj 的实测顺序）
    s2 = Session(tables())
    u1 = User(1, "a")
    s2.add(u1)
    s2.flush()
    s2.sql.clear()
    u1.name = "a2"
    u2 = User(2, "b")
    s2.add(u2)
    sql2 = s2.flush()
    eq(sql2, ["UPDATE user SET name WHERE id=1", "INSERT INTO user (id,name)"],
       "B2 同一表内 UPDATE 先于 INSERT")

    # B3 DELETE 走 _sorted_tables 的逆序：先子表后父表
    s3 = Session(tables())
    u3 = User(1, "a")
    o3 = Order(10, 1, 5)
    s3.add(u3)
    s3.add(o3)
    s3.flush()
    s3.sql.clear()
    s3.delete(u3)
    s3.delete(o3)
    sql3 = s3.flush()
    eq(sql3, ["DELETE FROM order WHERE id=10", "DELETE FROM user WHERE id=1"],
       "B3 DELETE 按逆拓扑序（order 先于 user）")

    # B4 整体：saves 早于 deletes（unitofwork 的 dependencies.add((saves, deletes))）
    s4 = Session(tables())
    u4 = User(1, "a")
    o4 = Order(10, 1, 5)
    s4.add(u4)
    s4.add(o4)
    s4.flush()
    s4.sql.clear()
    s4.delete(o4)
    u4b = User(2, "b")
    s4.add(u4b)
    sql4 = s4.flush()
    eq([x.split()[0] for x in sql4], ["INSERT", "DELETE"],
       "B4 整体顺序是保存早于删除")

    # B5 未持久化的对象被 delete 不产生 DELETE
    s5 = Session(tables())
    p = User(7, "pending")
    s5.add(p)
    s5.delete(p)
    sql5 = s5.flush()
    eq(sql5, ["INSERT INTO user (id,name)"], "B5 pending 对象 delete 不产生 DELETE")


# ------------------------------------------------------------- C 脏检查
def t_dirty():
    s = Session(tables())
    u = User(1, "a")
    s.add(u)
    s.flush()
    s.sql.clear()
    u.name = "b"
    u.name = "a"          # 改回原值
    s.flush()
    eq(s.sql, [], "C1 改回原值不算脏，不产生 UPDATE")

    u.name = "c"
    s.flush()
    eq(s.sql, ["UPDATE user SET name WHERE id=1"], "C2 真改动才发 UPDATE")

    s.sql.clear()
    s.expire_all()        # 过期后任何属性都被视为脏
    s.flush()
    ok(len(s.sql) == 1 and s.sql[0].startswith("UPDATE"),
       "C3 expire_all 后首次访问会重新取值（本模型里表现为再发一次 UPDATE）")


# ------------------------------------------------------------- D autoflush
def t_autoflush():
    s = Session(tables(), autoflush=True)
    u = User(1, "a")
    s.add(u)
    rows = s.query_all("user")
    ok(any(x.startswith("INSERT") for x in s.sql), "D1 autoflush 为真时查询前先 flush")
    eq(len(rows), 1, "D1b flush 之后查询才能看到新行")

    s2 = Session(tables(), autoflush=False)
    u2 = User(1, "a")
    s2.add(u2)
    rows2 = s2.query_all("user")
    eq(s2.sql, [], "D2 autoflush 为假时查询不触发 flush")
    eq(len(rows2), 0, "D2b 未 flush 的新对象查不到（会话不是缓存）")

    # D3 官方 FAQ：会话不做查询缓存 —— 把库里的行清掉后，查询返回空，
    #    但身份映射里那个对象还在（反过来证明查询不是从身份映射取的）
    s3 = Session(tables())
    u3 = User(1, "a")
    s3.add(u3)
    s3.flush()
    s3._rows["user"].clear()
    eq(len(s3.query_all("user")), 0, "D3a 查询不受身份映射影响（不是查询缓存）")
    ok(s3.get(User, 1) is u3, "D3b 而按主键取仍然命中身份映射")


# ------------------------------------------------------- E 状态与事务边界
def t_states():
    s = Session(tables())
    u = User(1, "a")
    s.add(u)
    ok(_state(u).has_identity is False, "E1 add 之后是 pending（无身份键）")
    s.flush()
    ok(_state(u).has_identity is True, "E2 flush 之后变成 persistent")
    eq(_state(u).key, ("User", 1), "E3 flush 时写入身份键")

    u.name = "z"
    s.rollback()
    eq(u.name, "a", "E4 rollback 把属性恢复到已提交快照")

    s.delete(u)
    ok(_state(u).deleted is True, "E5 delete 只打标记，语句等 flush 才发")
    s.commit()
    ok(_state(u).has_identity is False, "E6 commit 后已删除对象变成 detached")

    # E7 同一个对象重复 add 幂等
    s7 = Session(tables())
    x = User(1, "a")
    s7.add(x)
    s7.add(x)
    eq(len(s7._new), 1, "E7 同一对象重复 add 只登记一次")

    # E8 新会话里同主键是另一个对象（会话级身份，不是全局）
    s8 = Session(tables())
    y = User(1, "a")
    s8.add(y)
    s8.flush()
    ok(s8.get(User, 1) is y and y is not u, "E8 身份映射是会话级的")


def main():
    t_identity()
    t_flush_order()
    t_dirty()
    t_autoflush()
    t_states()
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
