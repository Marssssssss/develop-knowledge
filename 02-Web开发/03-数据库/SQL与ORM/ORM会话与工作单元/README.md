# ORM 会话与工作单元（身份映射 + 脏检查 + flush 顺序）

## 一、简介

每个用 SQLAlchemy / Hibernate / GORM 写业务代码的人，迟早会被这三个问题绊住：

1. 同一条记录加载两次，为什么拿到的是**同一个对象**？
2. 我只改了字段、没调用任何写方法，为什么 `commit()` 前数据库里已经有数据了？
3. 一个事务里既有 INSERT 又有 UPDATE 又有 DELETE，它们**按什么顺序**发到数据库？

答案都在 ORM 的「工作单元（Unit of Work）」里：会话把改动攒在内存，直到 flush 才集中下发。本 demo 逐行转写 SQLAlchemy 2.0 官方源码（`orm/identity.py`、`orm/unitofwork.py`、`orm/persistence.py`），把上面三个机制做成可执行模型，并用 30 条断言把顺序钉死。

## 二、原理详解

### 2.1 身份映射（Identity Map）

`Session` 内部维护一张以身份键为下标的字典，身份键 = `(实体类, 主键元组)`。官方 `identity.py` 的实现要点：

- **弱引用**：`_WeakInstanceDict` 里存的是 `InstanceState`，而 state 对业务对象是弱引用。对象被 GC 后 `key in identity_map` 直接返回 `False`（源码里连 `state.obj()` 为 `None` 的情形都用 `try/except KeyError` 兜住了）。
- **同键冲突抛异常**：`add()` 若发现同键已有**另一个活着**的对象，抛 `InvalidRequestError`；若就是同一个 state，返回 `False`。
- **`replace()` 不抛**：它静默换掉旧 state。加载路径用的是这条，所以「查询结果覆盖内存对象」不会报错。
- **它不是查询缓存**：官方 FAQ「Is the Session a cache?」原话是 *Yeee…no*。执行 `select(Foo).filter_by(name='bar')` 时，哪怕 `Foo(name='bar')` 就在身份映射里，会话也不知道，照样发 SQL。只有 `Session.get()` 这类按主键取的操作才先查身份映射。

### 2.2 脏检查：只比「已提交快照」

会话在每次 flush / load 时为对象存一份 `committed` 快照。判断某字段是否变脏，是拿当前值**与快照比**，而不是记录「有没有被赋值过」。所以：

```python
u.name = "b"
u.name = "a"   # 改回原值
# flush 时这条 UPDATE 根本不会发
```

### 2.3 flush 的三层顺序

第一层（**整体**）：保存早于删除。`unitofwork.py` 的 `_per_mapper_flush_actions` 里写着

```python
saves = _SaveUpdateAll(self, mapper.base_mapper)
deletes = _DeleteAll(self, mapper.base_mapper)
self.dependencies.add((saves, deletes))     # (saves, deletes) = saves 必须先执行
```

第二层（**表间拓扑序**）：INSERT 按 `mapper._sorted_tables`（外键依赖）父表在前；DELETE 按它的**逆序**，先删子表。

第三层（**表内**）：最容易写反的一条 —— `persistence.py` 的 `_save_obj` 对每张表都是

```python
update = _collect_update_commands(...)   # 先收集 UPDATE
_emit_update_statements(...)             # 先发 UPDATE
insert = _collect_insert_commands(...)   # 后收集 INSERT
_emit_insert_statements(...)             # 后发 INSERT
```

**同一张表内 UPDATE 在 INSERT 之前**，与很多人以为的「先插入再更新」相反。

### 2.4 autoflush 的触发点

官方把触发分成两类：

- **可配置（autoflush）**：`Session.execute()` 打 ORM 语句、`Query` 发送 SQL、`Session.merge()` 查询前、对象刷新、未加载属性的懒加载。
- **无条件**：`Session.commit()` 内部、`Session.begin_nested()`（SAVEPOINT）之前、`Session.prepare()`（两阶段提交）。

关掉 autoflush 后最常见的现象是：刚 `add()` 的对象查不到 —— 因为会话不是缓存，未 flush 的对象根本没进数据库。

## 三、对比：SQLAlchemy / GORM / 裸 SQL

| 维度 | SQLAlchemy Session | GORM（Go） | 裸 SQL |
| --- | --- | --- | --- |
| 身份映射 | 有（弱引用，会话级） | 无（`Session` 只是配置载体，不做对象缓存） | 无 |
| 自动脏检查 | 有（快照比对） | 无（要显式 `Save` / `Updates`） | 无 |
| 写顺序 | 工作单元统一排序 | 调用即发，顺序即代码顺序 | 调用即发 |
| 典型坑 | 改回原值仍占一次快照、autoflush 触发点 | `Session` 复用导致条件串味（`New Session` 必须显式调用） | 需自己保证父子表顺序 |

GORM 的 `Session` 与 SQLAlchemy 的 `Session` **不是同一个东西**：官方 `gorm.io/docs/session.html` 里它是「一次操作的配置载体」（`NewDB`、`SkipHooks`、`AllowGlobalUpdate` 等开关），默认**每次链式调用后失效**，需要 `New Session` 显式延寿，也不维护身份映射。

## 四、环境

- Python 3.8+（仅标准库）
- Go 1.21（无外部依赖，本机无工具链时走人工审查）

## 五、运行方式

```bash
cd python && python selfcheck_session.py     # 30 条断言
cd go     && go run .
```

## 六、关键代码

**身份映射的同键冲突**（`python/main.py`）：

```python
def add(self, state):
    if key in self._dict:
        existing_state = self._dict[key]
        if existing_state is not state:
            if existing_state.obj is not None:      # 弱引用已死则不冲突
                raise InvalidRequestError(...)
        else:
            return False                             # 同一 state 重复登记
    self._dict[key] = state
    return True
```

**flush 的三层顺序**（`python/main.py`）：

```python
def flush(self):
    self._emit_saves()     # 表拓扑序；每表 UPDATE → INSERT
    self._emit_deletes()   # reversed(拓扑序)
```

## 七、性能边界

- **身份映射是 O(1) 查表**，但它是**会话级**的：一个请求一个会话没问题，跨请求复用会话会既泄漏内存又读到过期对象。
- 弱引用意味着对象可能在你还持有着它的时候被 GC（如果你自己也没留强引用）。官方靠 `Session._new` 强持有 pending 对象来避免这种「add 之后对象消失」。
- flush 是**全量扫描**：每次 autoflush 都要遍历身份映射找脏对象。一个会话里挂上万个对象，一次查询前的 autoflush 就是 O(N)。长事务里应显式 `flush()` 后 `expunge_all()`。
- UPDATE 先于 INSERT 这一层不保证跨表语义（父表 UPDATE 与子表 INSERT 的先后仍由第三层的表间拓扑序决定）。

## 八、注意事项与常见坑

1. **「会话是缓存」是最大的误解**。它只做身份映射，不做查询缓存；想缓存要自己加（或 `dogpile.cache` 之类的方案）。
2. **改回原值不脏**，但**对象已在 `_new` 里就一定会 INSERT** —— 脏检查只对 persistent 对象生效。
3. **`delete()` 只打标记**，DELETE 语句要等 flush；对 pending 对象调 `delete()` 不会产生 DELETE（本 demo B5 断言）。
4. **flush 失败会留下半个事务**：官方在 flush 出错后要求显式 `rollback()`，否则会话进入不可用状态。
5. **同主键两个对象**在会话里是硬错误（`InvalidRequestError`），不是「后者覆盖前者」—— 合并数据请用 `Session.merge()`。
6. **Go 版没有弱引用**：`go/uow.go` 用 `Obj.Alive` 字段手工模拟存活性，Python 版的 `del obj; gc.collect()` 语义在 Go 里对应 `obj.Alive = false`。
7. **`expire_all()` 之后任何属性访问都会触发一次 SELECT**（本模型里表现为重新发 UPDATE），`commit()` 默认就是 expire 的。

## 九、参考资料（本轮实际读过）

- SQLAlchemy 2.0 官方文档 — Using the Session / Session Basics：<https://docs.sqlalchemy.org/en/20/orm/session_basics.html>
- SQLAlchemy 2.0 官方文档 — State Management：<https://docs.sqlalchemy.org/en/20/orm/session_state_management.html>
- SQLAlchemy 源码 `lib/sqlalchemy/orm/identity.py`（`_WeakInstanceDict.add/replace/get`）
  <https://github.com/sqlalchemy/sqlalchemy/blob/main/lib/sqlalchemy/orm/identity.py>
- SQLAlchemy 源码 `lib/sqlalchemy/orm/unitofwork.py`（`_per_mapper_flush_actions` 的 `(saves, deletes)` 依赖）
  <https://github.com/sqlalchemy/sqlalchemy/blob/main/lib/sqlalchemy/orm/unitofwork.py>
- SQLAlchemy 源码 `lib/sqlalchemy/orm/persistence.py`（`_save_obj` 的 UPDATE→INSERT、`_delete_obj` 的 `reversed(_sorted_tables)`）
  <https://github.com/sqlalchemy/sqlalchemy/blob/main/lib/sqlalchemy/orm/persistence.py>
- GORM 官方文档 — Session：<https://gorm.io/docs/session.html>
