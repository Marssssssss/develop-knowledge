"""autovacuum 的触发阈值与打分。

逐行转写 `src/backend/postmaster/autovacuum.c` 的 `relation_needs_vacanalyze()`:

```c
vacthresh   = vac_base_thresh + vac_scale_factor * reltuples;
if (vac_max_thresh >= 0 && vacthresh > vac_max_thresh) vacthresh = vac_max_thresh;
vacinsthresh = vac_ins_base_thresh + vac_ins_scale_factor * reltuples * pcnt_unfrozen;
anlthresh   = anl_base_thresh + anl_scale_factor * reltuples;
scores->vac = vactuples / Max(vacthresh, 1);
scores->anl = anltuples / Max(anlthresh, 1);
if (av_enabled && vactuples > vacthresh) *dovacuum = true;
if (av_enabled && anltuples > anlthresh) *doanalyze = true;
```

关键细节(源码逐条对过):

- **insert 阈值的分母是"未冻结比例"**:
  `pcnt_unfrozen = 1 - relallfrozen / relpages`(relallfrozen 先被 clamp 到 relpages)。
  纯插入型表(append-only)的插入阈值只按**未冻结页面**缩放 —— 全冻结的表几乎不触发。
- **`vacthresh` 会被 `vac_max_thresh`(默认 1 亿)封顶**,防止大表永远等不到 vacuum。
- **打分是"实际值 / 阈值"**,不是绝对值;候选表按 `scores.max` 排序,谁最接近阈值谁先跑。
- **force_vacuum(防回绕)不看死元组**:`relfrozenxid` 早于 `recentXid - freeze_max_age`
  就直接 `dovacuum = true`,**即使 autovacuum 被关掉也会触发**。

默认值(官方文档 runtime-config-autovacuum):

| 参数 | 默认 |
| --- | --- |
| `autovacuum_vacuum_threshold` | 50 |
| `autovacuum_vacuum_scale_factor` | 0.2 |
| `autovacuum_vacuum_insert_threshold` | 1000(-1 关闭) |
| `autovacuum_vacuum_insert_scale_factor` | 0.2 |
| `autovacuum_analyze_threshold` | 50 |
| `autovacuum_analyze_scale_factor` | 0.1 |
| `autovacuum_vacuum_max_threshold` | 100,000,000 |
| `autovacuum_freeze_max_age` | 200,000,000 |
| `autovacuum_naptime` | 1min |
| `autovacuum_max_workers` | 3 |
| `autovacuum_vacuum_cost_delay` | 2ms |
"""

VAC_BASE_THRESH = 50
VAC_SCALE = 0.2
VAC_INS_BASE_THRESH = 1000
VAC_INS_SCALE = 0.2
ANL_BASE_THRESH = 50
ANL_SCALE = 0.1
VAC_MAX_THRESH = 100_000_000
FREEZE_MAX_AGE = 200_000_000


def unfrozen_ratio(relpages, relallfrozen):
    """源码: relpages>0 且 relallfrozen>0 时 pcnt_unfrozen = 1 - allfrozen/pages。

    relallfrozen 会被 Min(relallfrozen, relpages) 夹住; 否则恒为 1.0。
    """
    if relpages > 0 and relallfrozen > 0:
        rf = min(relallfrozen, relpages)
        return 1.0 - (float(rf) / relpages)
    return 1.0


def thresholds(reltuples, relpages=0, relallfrozen=0,
               vac_base=VAC_BASE_THRESH, vac_scale=VAC_SCALE,
               vac_max=VAC_MAX_THRESH,
               ins_base=VAC_INS_BASE_THRESH, ins_scale=VAC_INS_SCALE,
               anl_base=ANL_BASE_THRESH, anl_scale=ANL_SCALE):
    """返回 (vacthresh, vacinsthresh, anlthresh)。"""
    vacthresh = vac_base + vac_scale * reltuples
    if vac_max >= 0 and vacthresh > vac_max:
        vacthresh = float(vac_max)
    vacinsthresh = ins_base + ins_scale * reltuples * unfrozen_ratio(relpages, relallfrozen)
    anlthresh = anl_base + anl_scale * reltuples
    return vacthresh, vacinsthresh, anlthresh


def scores(dead_tuples, ins_since_vacuum, mod_since_analyze,
           vacthresh, vacinsthresh, anlthresh):
    """源码: score = 实际值 / Max(阈值, 1); 阈值被 Max(...,1) 兜底防除零。"""
    return {
        "vac": dead_tuples / max(vacthresh, 1),
        "vac_ins": ins_since_vacuum / max(vacinsthresh, 1),
        "anl": mod_since_analyze / max(anlthresh, 1),
    }


def needs_vacanalyze(reltuples, dead_tuples=0, ins_since_vacuum=0, mod_since_analyze=0,
                     relpages=0, relallfrozen=0, av_enabled=True,
                     relfrozenxid=None, recent_xid=None, freeze_max_age=FREEZE_MAX_AGE,
                     **kw):
    """返回 (dovacuum, doanalyze, wraparound, scores, thresholds)。

    XID 侧的 force_vacuum 与死元组侧是**两条独立通路**。
    """
    vt, vit, at = thresholds(reltuples, relpages, relallfrozen, **kw)
    sc = scores(dead_tuples, ins_since_vacuum, mod_since_analyze, vt, vit, at)

    force = False
    if relfrozenxid is not None and recent_xid is not None:
        force = (recent_xid - relfrozenxid) > freeze_max_age
    dovac = bool(force)                        # 防回绕真空: 即使 autovacuum 关闭也做
    doanl = False
    if av_enabled:
        if dead_tuples > vt:
            dovac = True
        if vit >= 0 and ins_since_vacuum > vit:
            dovac = True
        if mod_since_analyze > at:
            doanl = True
    return dovac, doanl, force, sc, (vt, vit, at)
