// Auto Layout 约束求解(Cassowary 型)—— Swift 版:表格式两阶段单纯形 + 误差变量。
//
// 权威依据:
//   * Apple《Auto Layout Guide》*Anatomy of a Constraint*
//     item1.attribute1 = multiplier × item2.attribute2 + constant;优先级 1..1000
//     (1000 = required);CHCR 默认 Hugging 250 / CompressionResistance 750。
//   * Badros / Borning / Stuckey《The Cassowary Linear Arithmetic Constraint
//     Solving Algorithm》(TOCHI 8(4), 2001):slack 变量化不等式为等式、
//     error 变量承载偏差、目标是误差加权和最小、edit / stay 约束。
//
// 与 python/cassowary_check.py 同题:两边都解出 50/200/300 与 80/220,
// 跨语言结果一致本身就是一层验证。
//
// 运行: swift SwiftLayoutSolver.swift

import Foundation

// MARK: - 强度

let REQUIRED = 1_001_001_000.0

/// Cassowary 符号强度压平:required 之下一律 < REQUIRED。
func strength(_ a: Double, _ b: Double = 0, _ c: Double = 0) -> Double {
    min(REQUIRED - 1000, a * 1_000_000 + b * 1_000 + c)
}

/// Apple 的 UILayoutPriority(1..1000)。1000 即 required;250/750 是 CHCR 默认值。
func apple(_ priority: Int) -> Double {
    priority >= 1000 ? REQUIRED : strength(Double(priority))
}


// MARK: - 场景自检

final class Report {
    private(set) var pass = 0
    private(set) var fail = 0
    func check(_ label: String, _ ok: Bool, _ detail: String = "") {
        if ok { pass += 1 } else { fail += 1 }
        print("  [\(ok ? "PASS" : "FAIL")] \(label)\(detail.isEmpty ? "" : "   \(detail)")")
    }
    func near(_ a: Double, _ b: Double, _ tol: Double = 1e-6) -> Bool { abs(a - b) <= tol }
}

let r = Report()

// [1] 强度刻度
print("[1] 强度刻度与 Apple 优先级映射")
r.check("required > strong > medium > weak > 0",
        REQUIRED > strength(1) && strength(1) > strength(0, 1)
            && strength(0, 1) > strength(0, 0, 1))
r.check("apple(250) < apple(500) < apple(750) < apple(1000)=required",
        apple(250) < apple(500) && apple(500) < apple(750) && apple(1000) == REQUIRED)

// [2] 可伸缩行:窗宽 300 required,两个盒子宽度是 weak 偏好
print("[2] 可伸缩行(required 定骨架 + weak 定偏好)")
let s2 = Solver()
let w = s2.variable("windowWidth")
let l1 = s2.variable("box1.left"), r1 = s2.variable("box1.right")
let l2 = s2.variable("box2.left"), r2 = s2.variable("box2.right")
s2.add([w: 1], -300, "==", REQUIRED, "windowWidth=300")
s2.add([l1: 1], 0, "==", REQUIRED, "box1.left=0")
s2.add([r2: 1, w: -1], 0, "==", REQUIRED, "box2.right=windowWidth")
s2.add([l2: 1, r1: -1], 0, ">=", REQUIRED, "box2.left>=box1.right")
s2.add([r1: 1, l1: -1], 0, ">=", REQUIRED, "width1>=0")
s2.add([r2: 1, l2: -1], 0, ">=", REQUIRED, "width2>=0")
s2.add([r1: 1, l1: -1], -50, "==", strength(0, 0, 1), "prefer width1=50")
s2.add([r2: 1, l2: -1], -100, "==", strength(0, 0, 1), "prefer width2=100")
let v2 = try s2.solve()
r.check("box1.right = 50", r.near(v2[r1], 50), "\(v2[r1])")
r.check("box2.left = 200", r.near(v2[l2], 200), "\(v2[l2])")
r.check("box2.right = 300", r.near(v2[r2], 300), "\(v2[r2])")
r.check("总宽守恒 50 + 150 + 100 = 300", r.near(v2[r2] - v2[l1], 300))

// [3] 优先级冲突:required 100 vs weak 50;strong 10 vs weak 20
print("[3] 优先级冲突")
let s3 = Solver()
let x = s3.variable("x")
s3.add([x: 1], -100, "==", REQUIRED, "required width=100")
s3.add([x: 1], -50, "==", strength(0, 0, 1), "weak width=50")
let v3 = try s3.solve()
r.check("required 100 压过 weak 50 → x=100", r.near(v3[x], 100), "\(v3[x])")
let s3b = Solver()
let z = s3b.variable("z")
s3b.add([z: 1], -10, "==", strength(1), "strong z=10")
s3b.add([z: 1], -20, "==", strength(0, 0, 1), "weak z=20")
let v3b = try s3b.solve()
r.check("strong 10 压过 weak 20 → z=10", r.near(v3b[z], 10), "\(v3b[z])")

// [4] CHCR:等 hugging 时歧义;label 提到 251 后唯一解 80/220
print("[4] CHCR:先复现歧义,再靠 hugging 优先级差消歧")
func chcr(labelHug: Int, fieldHug: Int) -> ([Double], [Constraint]) {
    let s = Solver()
    let a = s.variable("label.width"), b = s.variable("field.width")
    s.add([a: 1, b: 1], -300, "==", REQUIRED, "a+b=300")
    s.add([a: 1], -80, ">=", apple(750), "label CR 750")
    s.add([a: 1], -80, "<=", apple(labelHug), "label Hug")
    s.add([b: 1], -100, ">=", apple(750), "field CR 750")
    s.add([b: 1], -100, "<=", apple(fieldHug), "field Hug")
    return (try! s.solve(), s.cons)
}
let (va, ca) = chcr(labelHug: 250, fieldHug: 250)
func softCost(_ cons: [Constraint], _ v: [Double]) -> Double {
    cons.filter { $0.strength < REQUIRED }.reduce(0) { $0 + $1.strength * $1.residual(v) }
}
let endpointA = [80.0, 220.0], endpointB = [200.0, 100.0]
r.check("等 hugging 时解落在 80..220", va[0] >= 80 - 1e-6 && va[0] <= 220 + 1e-6,
        "label=\(va[0]) field=\(va[1])")
r.check("两个端点解代价相同 → 布局歧义(Apple 文档指出的典型陷阱)",
        r.near(softCost(ca, endpointA), softCost(ca, endpointB), 1.0),
        "\(softCost(ca, endpointA)) vs \(softCost(ca, endpointB))")
let (vb, cb) = chcr(labelHug: 251, fieldHug: 250)
r.check("label Hug 251 → label 保持内在宽 80", r.near(vb[0], 80), "\(vb[0])")
r.check("剩余空间归 textField → 220", r.near(vb[1], 220), "\(vb[1])")
r.check("field 的 Hugging 被违反 120 点", r.near(cb[4].residual(vb), 120),
        "\(cb[4].residual(vb))")

// [5] stay + 编辑:只有被拖的点动
print("[5] 编辑约束 + stay 约束")
let s5 = Solver()
let m = (0..<4).map { s5.variable("m\($0)") }
for (i, mv) in m.enumerated() { s5.stay(mv, at: 100 * Double(i + 1)) }
let v5a = try s5.solve()
r.check("未拖动时 stay 全部满足", (0..<4).allSatisfy { r.near(v5a[m[$0]], 100 * Double($0 + 1)) })
let cold = s5.pivots
s5.edit(m[0], to: 250)
let v5b = try s5.solve()
r.check("拖动后 m0 = 250(strong 压过 weak stay)", r.near(v5b[m[0]], 250), "\(v5b[m[0]])")
r.check("其余点纹丝不动", r.near(v5b[m[1]], 200) && r.near(v5b[m[2]], 300) && r.near(v5b[m[3]], 400))
print("         冷启动主元数 = \(cold);改常数后重解 = \(s5.pivots)(无对偶单纯形,增量不会更快)")

// [6] required 冲突检测
print("[6] required 冲突检测")
let s6 = Solver()
let q = s6.variable("q")
s6.add([q: 1], -10, "==", REQUIRED, "q=10")
s6.add([q: 1], -20, "==", REQUIRED, "q=20")
do {
    _ = try s6.solve()
    r.check("矛盾的 required 应抛 infeasible", false)
} catch {
    r.check("矛盾的 required 抛 infeasible(对应 Auto Layout 打印冲突日志)", true)
}

print("\n断言 \(r.pass) 通过 / \(r.fail) 失败")
exit(r.fail == 0 ? 0 : 1)
