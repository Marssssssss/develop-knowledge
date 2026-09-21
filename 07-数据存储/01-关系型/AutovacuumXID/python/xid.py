"""事务 ID 回绕的四条防线。

逐行转写 `src/backend/access/transam/varsup.c` 的 `SetTransactionIdLimit()`:

```c
xidWrapLimit = oldest_datfrozenxid + (MaxTransactionId >> 1);   // +2147483647
xidStopLimit = xidWrapLimit - 3000000;                          // 300 万
xidWarnLimit = xidWrapLimit - 100000000;                        // 1 亿
xidVacLimit  = oldest_datfrozenxid + autovacuum_freeze_max_age; // 2 亿
```

注释原文:

- wrap: "The place where we actually get into deep trouble is **halfway around** from
  the oldest potentially-existing XID."
- stop: "We'll refuse to continue assigning XIDs in interactive mode once we get within
  **3M** transactions of data loss."
- warn: "We'll start complaining loudly when we get within **100M** transactions of data
  loss. ... (No, we're not gonna make this configurable.)"
- vac: "We'll start trying to force autovacuums when oldest_datfrozenxid gets to be more
  than `autovacuum_freeze_max_age` transactions old."

`GetNewTransactionId()` 里的行为:

- 过了 `xidVacLimit`:每 65536 个事务发一次 `PMSIGNAL_START_AUTOVAC_LAUNCHER`(别灌爆 postmaster);
- 过了 `xidStopLimit`:`ereport(ERROR)` —— "database is not accepting commands that assign
  new transaction IDs to avoid wraparound data loss";
- 过了 `xidWarnLimit`:`WARNING` + 剩余百分比 `(xidWrapLimit - xid) / (MaxTransactionId / 2) * 100`。

注意**百分比的分母是 `MaxTransactionId / 2`(2147483647),不是 MaxTransactionId**。
"""

MAX_TRANSACTION_ID = 0xFFFFFFFF          # 4294967295
FIRST_NORMAL_TRANSACTION_ID = 3
HALF = MAX_TRANSACTION_ID >> 1           # 2147483647
STOP_MARGIN = 3_000_000
WARN_MARGIN = 100_000_000
AUTOVACUUM_FREEZE_MAX_AGE = 200_000_000


def _normal(x):
    """源码里的 FirstNormalTransactionId 回绕修正(简化为同一环上的取模)。"""
    return x % (MAX_TRANSACTION_ID + 1)


def limits(oldest_datfrozenxid, freeze_max_age=AUTOVACUUM_FREEZE_MAX_AGE):
    """返回四条限位的字典(值可能超过 2^32, 用普通整数表示环上位置)。"""
    wrap = oldest_datfrozenxid + HALF
    stop = wrap - STOP_MARGIN
    warn = wrap - WARN_MARGIN
    vac = oldest_datfrozenxid + freeze_max_age
    return {"wrap": wrap, "stop": stop, "warn": warn, "vac": vac}


def remaining_pct(cur_xid, xid_wrap_limit):
    """源码: (xidWrapLimit - xid) / (MaxTransactionId / 2) * 100。"""
    return (xid_wrap_limit - cur_xid) / (MAX_TRANSACTION_ID / 2) * 100


def classify(cur_xid, lim):
    """判断当前 XID 落在哪个区间: normal / vac / warn / stop / wrap。"""
    if cur_xid < lim["vac"]:
        return "normal"
    if cur_xid < lim["warn"]:
        return "vac"        # 强制 autovacuum
    if cur_xid < lim["stop"]:
        return "warn"       # WARNING: must be vacuumed within N transactions
    if cur_xid < lim["wrap"]:
        return "stop"       # ERROR: 拒绝分配新 XID
    return "wrap"           # 数据已不可区分


def xid_age(cur_xid, relfrozenxid):
    """`age()` = 当前 XID 与冻结点的差(同一环上)。"""
    return cur_xid - relfrozenxid


def freeze_interval(freeze_max_age=AUTOVACUUM_FREEZE_MAX_AGE,
                    freeze_min_age=50_000_000):
    """文档: 静态表被强制 vacuum 的间隔 ≈ freeze_max_age - freeze_min_age。"""
    return freeze_max_age - freeze_min_age
