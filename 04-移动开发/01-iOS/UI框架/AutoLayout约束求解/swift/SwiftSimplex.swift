import Foundation

// Swift 版求解内核:强度刻度 + 表格式两阶段单纯形 + 建模层(Solver/Constraint)。
// 被 main.swift 引用;拆分只为守住「单源文件 ≤300 行」硬约束,代码逐字节未改。
// 权威依据见同目录 README 的「参考资料」。
// MARK: - 表格式两阶段单纯形

enum LPError: Error { case infeasible }

final class Simplex {
    private var rows: [[Double]] = []
    private var b: [Double] = []
    private var cost: [Double] = []
    private var basis: [Int] = []
    private(set) var pivots = 0

    var columnCount: Int { cost.count }

    /// 新增一列(变量),代价即它在目标函数里的系数。
    func col(_ c: Double = 0) -> Int {
        for i in rows.indices { rows[i].append(0) }
        cost.append(c)
        return cost.count - 1
    }

    /// 新增一行约束 row · x = rhs。
    func row(_ coeffs: [Int: Double], _ rhs: Double) {
        var r = [Double](repeating: 0, count: cost.count)
        for (c, v) in coeffs { r[c] = v }
        rows.append(r)
        b.append(rhs)
        basis.append(-1)
    }

    /// 目标行存的是 reduced cost r_j = c_j − c_B·B⁻¹A_j:最小化时 r_j < 0 才进基。
    /// 主元时**两行目标都要跟着消**,否则第二阶段拿到的不是规范形(这是本 demo
    /// 开发期真实踩到的坑:只消第一阶段目标行,第二阶段会误判"已最优")。
    private func pivot(_ r: Int, _ c: Int, _ objectives: inout [[Double]]) {
        pivots += 1
        let piv = rows[r][c]
        rows[r] = rows[r].map { $0 / piv }
        b[r] /= piv
        for i in rows.indices where i != r {
            let f = rows[i][c]
            if abs(f) > 1e-12 {
                for j in rows[i].indices { rows[i][j] -= f * rows[r][j] }
                b[i] -= f * b[r]
            }
        }
        for k in objectives.indices {
            let f = objectives[k][c]
            if abs(f) > 1e-12 {
                for j in objectives[k].indices { objectives[k][j] -= f * rows[r][j] }
            }
        }
        basis[r] = c
    }

    private func run(_ objectives: inout [[Double]], _ active: Int, _ allowed: Set<Int>) -> String {
        while true {
            guard let enter = allowed.sorted().first(where: { objectives[active][$0] < -1e-9 }) else {
                return "optimal"
            }
            var leave = -1
            var best = Double.infinity
            for i in rows.indices where rows[i][enter] > 1e-9 {
                let ratio = b[i] / rows[i][enter]
                if ratio < best - 1e-12 { leave = i; best = ratio }
            }
            if leave < 0 { return "unbounded" }
            pivot(leave, enter, &objectives)
        }
    }

    func solve() throws -> (status: String, values: [Double], objective: Double) {
        let m = rows.count
        for i in rows.indices where b[i] < 0 {          // b ≥ 0
            rows[i] = rows[i].map { -$0 }
            b[i] = -b[i]
        }
        var arts: [Int] = []
        for i in 0..<m {                                 // 每行一个人工变量当初始基
            let c = col(0)
            rows[i][c] = 1
            basis[i] = c
            arts.append(c)
        }
        var p1 = [Double](repeating: 0, count: cost.count)
        for c in arts { p1[c] = 1 }
        var objectives = [p1, cost]
        for i in 0..<m {
            for k in objectives.indices {
                let f = objectives[k][basis[i]]
                if abs(f) > 1e-12 {
                    for j in objectives[k].indices { objectives[k][j] -= f * rows[i][j] }
                }
            }
        }
        _ = run(&objectives, 0, Set(0..<cost.count))
        let artSum = (0..<m).filter { arts.contains(basis[$0]) }.reduce(0.0) { $0 + b[$1] }
        if artSum > 1e-7 { throw LPError.infeasible }    // required 集自相矛盾
        let allowed = Set(0..<cost.count).subtracting(arts)
        for i in 0..<m where arts.contains(basis[i]) {    // 把人工变量赶出基
            if let sub = allowed.sorted().first(where: { abs(rows[i][$0]) > 1e-9 }) {
                pivot(i, sub, &objectives)
            }
        }
        let status = run(&objectives, 1, allowed)
        var z = [Double](repeating: 0, count: cost.count)
        for i in 0..<m { z[basis[i]] = b[i] }
        let obj = (0..<cost.count).reduce(0.0) { $0 + cost[$1] * z[$1] }
        return (status, z, obj)
    }
}

// MARK: - 建模层

struct Constraint {
    let coeffs: [Int: Double]
    let constant: Double
    let op: String                       // "==" / ">=" / "<="
    let strength: Double
    let name: String

    func residual(_ x: [Double]) -> Double {
        var total = constant
        for (i, k) in coeffs { total += k * x[i] }
        switch op {
        case "==": return abs(total)
        case ">=": return max(0, -total)
        default: return max(0, total)
        }
    }
}

final class Solver {
    private let sp = Simplex()
    private(set) var names: [String] = []
    private(set) var cons: [Constraint] = []
    private(set) var pivots = 0

    @discardableResult func variable(_ name: String) -> Int {
        names.append(name)
        return names.count - 1
    }

    /// 变量在单纯形里拆成 x⁺ − x⁻(两列),并用 lazy 方式分配,保证索引对齐。
    private lazy var plus: [Int] = names.map { _ in sp.col() }
    private lazy var minus: [Int] = names.map { _ in sp.col() }

    func add(_ coeffs: [Int: Double], _ constant: Double, _ op: String,
             _ st: Double = REQUIRED, _ name: String = "") {
        cons.append(Constraint(coeffs: coeffs, constant: constant, op: op,
                               strength: st, name: name))
    }

    /// stay 约束:weak 地把变量按在原值 —— Cassowary 表达"别乱动"的方式。
    func stay(_ v: Int, at: Double, _ st: Double = strength(0, 0, 1)) {
        add([v: 1], -at, "==", st, "stay(\(names[v]))")
    }

    func edit(_ v: Int, to value: Double, _ st: Double = strength(1)) {
        if let i = cons.firstIndex(where: { $0.name == "edit(\(names[v]))" }) {
            cons[i] = Constraint(coeffs: [v: 1], constant: -value, op: "==",
                                 strength: st, name: cons[i].name)
        } else {
            add([v: 1], -value, "==", st, "edit(\(names[v]))")
        }
    }

    func solve() throws -> [Double] {
        for c in cons {
            var row: [Int: Double] = [:]
            for (v, k) in c.coeffs {
                row[plus[v], default: 0] += k
                row[minus[v], default: 0] -= k
            }
            if c.strength >= REQUIRED {
                if c.op == ">=" { row[sp.col()] = -1 }   // surplus
                if c.op == "<=" { row[sp.col()] = 1 }    // slack
                sp.row(row, -c.constant)
                continue
            }
            let em = sp.col(c.strength), ep = sp.col(c.strength)
            row[em, default: 0] += 1
            row[ep, default: 0] -= 1
            sp.row(row, -c.constant)
        }
        let (status, z, _) = try sp.solve()
        pivots = sp.pivots
        guard status != "infeasible" else { throw LPError.infeasible }
        return names.indices.map { z[plus[$0]] - z[minus[$0]] }
    }
}
