#!/usr/bin/env python3
"""在线 DDL 最小模拟:INSTANT / INPLACE / COPY 三算法 + MDL 独占窗口 + gh-ost。

依据 dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html 与
github/gh-ost README 归纳:
- INSTANT 只改数据字典元数据(加列 8.0.12+ 默认,8.0.29+ 任意位置);
- INPLACE 引擎内就地执行(可重建可不重建),执行期并发 DML 记入 row log,
  提交阶段应用;MDL 独占只出现在 prepare 开始与 commit 收尾两端;
- COPY 走服务器层临时表全量复制,共享锁期间只读;
- gh-ost:触发器方案之外 —— 直接订阅 binlog 异步回放到 ghost 表,
  copy 完成后原子 cut-over 换名。
"""
import copy


class Schema:
    def __init__(self, columns, rows):
        self.columns = list(columns)
        self.rows = rows               # list[dict]
        self.row_versions = 0          # INSTANT 行版本计数(上限 64)


class DDL:
    def __init__(self, schema):
        self.schema = schema
        self.events = []               # 观测到的行为轨迹

    def instant_add_column(self, name, default=None):
        """INSTANT:仅修改元数据 + 行版本 +1,不碰任何数据行。"""
        assert name not in self.schema.columns
        self.schema.columns.append(name)
        self.schema.row_versions += 1  # TOTAL_ROW_VERSIONS +1
        self.events.append(("instant", name, "metadata-only"))
        return "INSTANT"

    def inplace_add_index(self, key_col):
        """INPLACE:引擎内建二级索引,不重建表;执行期 DML 进 row log。

        返回 (算法, 并发DML, row_log)。MDL 独占只发生在两端。
        """
        row_log = []

        def execute_phase(concurrent_dml):
            # 执行阶段:MDL 共享,允许并发 DML,变更先记 row log
            row_log.extend(concurrent_dml)

        def commit_phase():
            # 提交阶段:短暂独占 MDL,把 row log 应用到新索引
            return list(row_log)

        self.events.append(("inplace", key_col, "index built in engine"))
        self._pending_row_log = commit_phase
        return "INPLACE", True, execute_phase

    def copy_modify_column(self, old, new):
        """COPY:服务器层临时表全量复制;期间共享锁只允许读。"""
        tmp = [dict(r, **{new: r.pop(old)}) for r in self.schema.rows]
        self.schema.rows = tmp
        self.events.append(("copy", old, "->", new))
        return "COPY"

    def apply_row_log(self, idx):
        return idx  # commit 阶段把 row log 应用到索引后的最终态


def gh_ost_migrate(schema, binlog_stream):
    """gh-ost:ghost 表 + binlog 流异步回放 + 原子 cut-over。"""
    ghost = copy.deepcopy(schema)          # 1. 建空 ghost 表(含新结构)
    ghost.rows = []
    changelog = []

    def copy_rows():
        ghost.rows = [dict(r) for r in schema.rows]  # 增量拷贝
        changelog.append(("copy", len(ghost.rows)))

    def apply_binlog(events):
        # 2. 主库 binlog 流(不依赖触发器)异步应用到 ghost
        for kind, pk, change in events:
            if kind == "I":
                ghost.rows.append(dict(change))
            elif kind == "U":
                for r in ghost.rows:
                    if r.get("id") == pk:
                        r.update(change)
            elif kind == "D":
                ghost.rows = [r for r in ghost.rows if r.get("id") != pk]
        changelog.append(("binlog_applied", len(events)))

    def cut_over():
        # 3. 原子换名:锁定极短窗口,ghost <--> 原表
        changelog.append(("cutover", "atomic swap"))
        return ghost

    return copy_rows, apply_binlog, cut_over, changelog


def main():
    sch = Schema(["id", "name"], [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}])

    # ---- 1. INSTANT:只改元数据,行版本 +1 ----
    ddl = DDL(sch)
    alg = ddl.instant_add_column("created_at")
    assert alg == "INSTANT" and sch.row_versions == 1
    assert all("created_at" not in r for r in sch.rows)   # 不碰数据行
    # 上限 64 个行版本(手册 ERROR 4092)
    sch2 = Schema(["id"], [{"id": 1}])
    for i in range(64):
        DDL(sch2).instant_add_column("c%d" % i)
    assert sch2.row_versions == 64

    # ---- 2. INPLACE:执行期 DML 进 row log,提交阶段应用;两端独占 ----
    alg, dml_ok, execute = ddl.inplace_add_index("name")
    assert alg == "INPLACE" and dml_ok is True
    execute([{"id": 1, "name": "a2"}])            # 执行阶段:并发 DML 记入 row log
    applied = ddl._pending_row_log()               # commit 阶段应用
    assert applied == [{"id": 1, "name": "a2"}]

    # ---- 3. COPY:全量复制到临时表,期间只读(共享锁)----
    alg = ddl.copy_modify_column("name", "title")
    assert alg == "COPY" and sch.rows[0] == {"id": 1, "title": "a"}

    # ---- 4. 算法优先级 INSTANT > INPLACE > COPY ----
    prio = {"INSTANT": 0, "INPLACE": 1, "COPY": 2}
    matrix = {  # 手册支持矩阵摘录:操作 → 可用算法
        "add_secondary_index": ["INPLACE"],
        "add_column": ["INSTANT", "INPLACE", "COPY"],
        "drop_pk": ["COPY"],
        "change_column_type": ["COPY"],
        "rename_table": ["INSTANT"],
    }
    best = lambda ops: min(matrix[ops], key=lambda a: prio[a])
    assert best("add_column") == "INSTANT"
    assert best("add_secondary_index") == "INPLACE"
    assert best("drop_pk") == "COPY"                # 删主键只有 COPY

    # ---- 5. gh-ost:copy 期间 binlog 流异步回放 + 原子 cut-over ----
    sch3 = Schema(["id"], [{"id": 1}, {"id": 2}, {"id": 3}])
    copy_rows, apply_binlog, cut_over, log = gh_ost_migrate(sch3, None)
    copy_rows()                                     # 拷 3 行到 ghost
    apply_binlog([("U", 2, {"name": "x"}),          # copy 期间的写
                  ("I", None, {"id": 4}),
                  ("D", 3, None)])
    ghost = cut_over()
    assert [r["id"] for r in ghost.rows] == [1, 2, 4]  # U 生效、I 进入、D 移除
    assert log[-1] == ("cutover", "atomic swap")

    print("ALL 5 DEMO-5 (OnlineDDL) ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
