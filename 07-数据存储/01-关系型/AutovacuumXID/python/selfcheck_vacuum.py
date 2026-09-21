# -*- coding: utf-8 -*-
"""自检: autovacuum 阈值 / 打分 / XID 回绕四线。

期望值全部手算: 阈值是 base + scale*reltuples, 限位是 oldest + 固定偏移。
"""

from autovac import (needs_vacanalyze, thresholds, scores, unfrozen_ratio,
                     VAC_BASE_THRESH, VAC_SCALE, ANL_BASE_THRESH, ANL_SCALE,
                     VAC_INS_BASE_THRESH, VAC_INS_SCALE, VAC_MAX_THRESH)
import xid

PASS = [0]
FAIL = []


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append(label)


# --- 1. 阈值公式 ---------------------------------------------------------------
vt, vit, at = thresholds(1000)
ok(abs(vt - (50 + 0.2 * 1000)) < 1e-9, "vacthresh = 50 + 0.2*1000 = 250")
ok(abs(at - (50 + 0.1 * 1000)) < 1e-9, "anlthresh = 50 + 0.1*1000 = 150")
ok(abs(vit - (1000 + 0.2 * 1000 * 1.0)) < 1e-9, "insert 阈值默认按全表未冻结算 = 1200")
vt0, _, at0 = thresholds(0)
ok(vt0 == 50 and at0 == 50, "空表时阈值就是 base(50 / 50)")
ok(VAC_INS_BASE_THRESH == 1000, "insert threshold 默认 1000 而不是 50")

# --- 2. vac_max_thresh 封顶 ----------------------------------------------------
big_vt, _, _ = thresholds(10 ** 9)
ok(abs(big_vt - VAC_MAX_THRESH) < 1e-9, "10 亿行表的 0.2*N 远超 1 亿 -> 被封顶")
ok(thresholds(10 ** 6)[0] < VAC_MAX_THRESH, "100 万行 -> 200050, 未封顶")
ok(thresholds(10 ** 8)[0] < VAC_MAX_THRESH, "1 亿行 -> 2000 万, 未封顶")
ok(thresholds(10 ** 9, vac_max=-1)[0] > VAC_MAX_THRESH, "vac_max=-1 关闭封顶")

# --- 3. 未冻结比例(insert 阈值的分母) -------------------------------------------
ok(abs(unfrozen_ratio(0, 0) - 1.0) < 1e-9, "无页面信息时视为全未冻结")
ok(abs(unfrozen_ratio(100, 100) - 0.0) < 1e-9, "全冻结 -> 比例 0")
ok(abs(unfrozen_ratio(100, 25) - 0.75) < 1e-9, "1/4 冻结 -> 0.75")
ok(abs(unfrozen_ratio(100, 150) - 0.0) < 1e-9, "relallfrozen > relpages 会被 clamp(源码 Min)")
_, vit_frozen, _ = thresholds(1000, relpages=100, relallfrozen=100)
ok(abs(vit_frozen - 1000.0) < 1e-9, "全冻结表的 insert 阈值退化成纯 base=1000")
_, vit_half, _ = thresholds(1000, relpages=100, relallfrozen=50)
ok(abs(vit_half - (1000 + 0.2 * 1000 * 0.5)) < 1e-9, "半冻结 -> 1000 + 100 = 1100")

# --- 4. 触发判定: 严格大于 --------------------------------------------------------
d, a, w, sc, th = needs_vacanalyze(1000, dead_tuples=250)
ok(not d, "死元组恰好等于阈值 250 -> 不触发(源码是 > 不是 >=)")
d, a, w, sc, th = needs_vacanalyze(1000, dead_tuples=251)
ok(d, "251 > 250 -> 触发 vacuum")
d, a, w, sc, th = needs_vacanalyze(1000, mod_since_analyze=150)
ok(not a and not d, "analyze 恰好 150 -> 不触发")
d, a, w, sc, th = needs_vacanalyze(1000, mod_since_analyze=151)
ok(a, "151 -> 触发 analyze, 但不触发 vacuum")
ok(not d, "只改 analyze 不动 vacuum(两条独立判定)")

# --- 5. 打分 = 实际 / Max(阈值, 1) -----------------------------------------------
sc = scores(500, 600, 300, 250.0, 1200.0, 150.0)
ok(abs(sc["vac"] - 2.0) < 1e-9, "500/250 = 2.0")
ok(abs(sc["anl"] - 2.0) < 1e-9, "300/150 = 2.0")
ok(abs(sc["vac_ins"] - 0.5) < 1e-9, "600/1200 = 0.5")
ok(scores(0, 0, 0, 0.0, 0.0, 0.0)["vac"] == 0.0, "阈值 0 被 Max(,1) 兜底, 不除零")

# --- 6. 大表 vs 小表: 同一份死元组的命运 -------------------------------------------
small = needs_vacanalyze(100, dead_tuples=71)      # 阈值 50 + 0.2*100 = 70
large = needs_vacanalyze(1_000_000, dead_tuples=71)  # 阈值 50 + 200000 = 200050
ok(small[0], "100 行的表: 阈值 70, 71 个死元组 -> 触发")
ok(not large[0], "100 万行的表: 阈值 200050, 71 个死元组 -> 不触发")
ok(small[4][0] < large[4][0], "表越大阈值越高")

# --- 7. 防回绕: 不看死元组, 且 autovacuum 关掉也做 -----------------------------------
lim_recent = 300_000_000
d, a, w, sc, th = needs_vacanalyze(0, recent_xid=lim_recent, relfrozenxid=0)
ok(w and d, "relfrozenxid 落后 3 亿 > freeze_max_age 2 亿 -> 强制 vacuum")
d_off, _, _, _, _ = needs_vacanalyze(0, recent_xid=lim_recent, relfrozenxid=0, av_enabled=False)
ok(d_off, "autovacuum 关闭时仍会强制 vacuum(文档: even when autovacuum is disabled)")
d_no, _, _, _, _ = needs_vacanalyze(0, dead_tuples=50, av_enabled=False)
ok(not d_no, "关掉后普通死元组触发被跳过(负控)")
d_young, _, w_young, _, _ = needs_vacanalyze(0, recent_xid=100_000_000, relfrozenxid=0)
ok(not w_young, "落后 1 亿 < 2 亿 -> 不强制")
d_edge, _, w_edge, _, _ = needs_vacanalyze(0, recent_xid=200_000_000, relfrozenxid=0)
ok(not w_edge, "恰好等于 freeze_max_age 也不强制(源码用 Precedes, 严格早于)")

# --- 8. XID 四线 ------------------------------------------------------------------
oldest = 1000
L = xid.limits(oldest)
ok(L["wrap"] == oldest + 2147483647, "wrap = oldest + (MaxTransactionId>>1) = +2147483647")
ok(L["stop"] == L["wrap"] - 3_000_000, "stop = wrap - 300 万")
ok(L["warn"] == L["wrap"] - 100_000_000, "warn = wrap - 1 亿")
ok(L["vac"] == oldest + 200_000_000, "vac = oldest + autovacuum_freeze_max_age")
ok(L["vac"] < L["warn"] < L["stop"] < L["wrap"], "时间线上: 先 vac(2e8), 再 warn, 再 stop, 最后 wrap")

ok(xid.classify(L["vac"] - 1, L) == "normal", "vac 之前是 normal")
ok(xid.classify(L["vac"], L) == "vac", "到 vac -> 强制 autovacuum")
ok(xid.classify(L["warn"], L) == "warn", "到 warn -> WARNING")
ok(xid.classify(L["stop"], L) == "stop", "到 stop -> 拒绝分配 XID")
ok(xid.classify(L["wrap"], L) == "wrap", "过 wrap -> 数据不可区分")

# --- 9. 剩余百分比的分母是 MaxTransactionId/2 ---------------------------------------
pct = xid.remaining_pct(L["warn"], L["wrap"])
ok(abs(pct - 100_000_000 / (xid.MAX_TRANSACTION_ID / 2) * 100) < 1e-9,
   "warn 点还剩 (wrap-warn)/(MaxTransactionId/2)")
ok(abs(pct - 4.656612875) < 1e-5, "≈4.66% —— 与官方'剩 5% 找加油站'的类比一致")
ok(abs(xid.remaining_pct(L["wrap"], L["wrap"])) < 1e-12, "wrap 点剩余 0%")
ok(abs(xid.remaining_pct(L["stop"], L["wrap"]) - 3_000_000 / (xid.MAX_TRANSACTION_ID / 2) * 100) < 1e-9,
   "stop 点剩余约 0.14%")

# --- 10. 静态表被强制 vacuum 的间隔 -------------------------------------------------
ok(xid.freeze_interval() == 150_000_000, "间隔 ≈ freeze_max_age(2e8) - freeze_min_age(5e7)")

# --- 11. age -----------------------------------------------------------------------
ok(xid.xid_age(1000, 500) == 500, "age = cur - relfrozenxid")

if __name__ == "__main__":
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  FAIL:", f)
