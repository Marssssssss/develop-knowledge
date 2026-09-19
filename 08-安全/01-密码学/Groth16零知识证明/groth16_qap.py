# -*- coding: utf-8 -*-
"""Groth16 zk-SNARK 的构造（Groth, "On the Size of Pairing-based Non-interactive
Arguments", eprint 2016/260，第 14 页的 Setup / Prove / Vfy 定义）。

R1CS → QAP → 三次群元素的证明 → 单个配对乘积等式。

关于配对：真实实现跑 BLS12-381/BN254 的椭圆曲线配对。本 demo 在**符号双线性群**里工作：
群元素记为有限域 F_p 里的指数，配对 e(X, Y) 记作指数相乘 X·Y。
于是论文里的验证等式

    A·B = α·β + Σ_{i=0}^{ℓ} a_i·(βu_i(x)+αv_i(x)+w_i(x))/γ · γ + C·δ

可以直接按整数运算核对。这样能完整演示 QAP 构造、trusted setup、证明生成与
「3 个群元素 / 3 次配对」这些**结构性质**，而把曲线算术这一层留给专门的配对 demo
（见同目录 `../BLS聚合签名/`）。
"""

P = (1 << 61) - 1          # 梅森素数，模乘直接用 Python 大整数


class F:
    """F_p 上的元素，重载四则运算"""

    def __init__(self, v):
        self.v = int(v) % P

    def __add__(self, o):
        return F(self.v + (o.v if isinstance(o, F) else int(o)))

    __radd__ = __add__

    def __neg__(self):
        return F(-self.v)

    def __sub__(self, o):
        return F(self.v - (o.v if isinstance(o, F) else int(o)))

    def __rsub__(self, o):
        return F((o.v if isinstance(o, F) else int(o)) - self.v)

    def __mul__(self, o):
        return F(self.v * (o.v if isinstance(o, F) else int(o)))

    __rmul__ = __mul__

    def inv(self):
        return F(pow(self.v, P - 2, P))

    def __truediv__(self, o):
        return self * (o.inv() if isinstance(o, F) else F(o).inv())

    def __eq__(self, o):
        return self.v == (o.v if isinstance(o, F) else int(o) % P)

    def __hash__(self):
        return hash(self.v)

    def __repr__(self):
        return f"F({self.v})"


# ============================== 多项式（系数低→高） ==============================
def p_trim(c):
    while c and c[-1] == 0:
        c.pop()
    return c


def p_add(a, b):
    n = max(len(a), len(b))
    return p_trim([((a[i] if i < len(a) else 0) + (b[i] if i < len(b) else 0)) % P
                   for i in range(n)])


def p_sub(a, b):
    n = max(len(a), len(b))
    return p_trim([((a[i] if i < len(a) else 0) - (b[i] if i < len(b) else 0)) % P
                   for i in range(n)])


def p_mul(a, b):
    if not a or not b:
        return []
    out = [0] * (len(a) + len(b) - 1)
    for i, ai in enumerate(a):
        if ai:
            for j, bj in enumerate(b):
                out[i + j] = (out[i + j] + ai * bj) % P
    return p_trim(out)


def p_eval(a, x):
    xv = x.v if isinstance(x, F) else int(x) % P
    acc = 0
    for c in reversed(a):
        acc = (acc * xv + c) % P
    return F(acc)


def p_divmod(a, b):
    """长除法，返回 (商, 余)；要求 b 非零"""
    a = list(a)
    q = [0] * max(0, len(a) - len(b) + 1)
    binv = b[-1]
    binv = pow(binv, P - 2, P)
    for i in range(len(a) - 1, len(b) - 2, -1):
        if a[i] == 0:
            continue
        coef = a[i] * binv % P
        q[i - len(b) + 1] = coef
        for j, bj in enumerate(b):
            a[i - len(b) + 1 + j] = (a[i - len(b) + 1 + j] - coef * bj) % P
    return p_trim(q), p_trim(a)


def lagrange(xs, ys):
    """在 xs 处取值为 ys 的插值多项式"""
    out = []
    for i, xi in enumerate(xs):
        term = [ys[i]]
        for j, xj in enumerate(xs):
            if i == j:
                continue
            den = pow((xi - xj) % P, P - 2, P)
            term = p_mul(term, [(-xj * den) % P, den])
        out = p_add(out, term)
    return out


# ============================== R1CS → QAP ==============================
class R1CS:
    """约束系统：每一条约束是 <U_j, a> · <V_j, a> = <W_j, a>，a 是完整赋值向量"""

    def __init__(self, u, v, w, n_public):
        self.u, self.v, self.w = u, v, w          # 各是 m+1 列 × n 行
        self.n = len(u)                            # 约束条数
        self.m = len(u[0]) - 1                     # 变量数（含恒为 1 的 a_0）
        self.ell = n_public                        # a_0..a_ℓ 是公开输入

    def check(self, a):
        for j in range(self.n):
            au = sum(self.u[j][i] * a[i] for i in range(self.m + 1)) % P
            av = sum(self.v[j][i] * a[i] for i in range(self.m + 1)) % P
            aw = sum(self.w[j][i] * a[i] for i in range(self.m + 1)) % P
            if au * av % P != aw:
                return False
        return True


def qap_from_r1cs(cs, roots):
    """把每一列在 roots 上插值成 u_i(X) / v_i(X) / w_i(X)"""
    us, vs, ws = [], [], []
    for i in range(cs.m + 1):
        us.append(lagrange(roots, [cs.u[j][i] for j in range(cs.n)]))
        vs.append(lagrange(roots, [cs.v[j][i] for j in range(cs.n)]))
        ws.append(lagrange(roots, [cs.w[j][i] for j in range(cs.n)]))
    t = [1]
    for r in roots:
        t = p_mul(t, [(-r) % P, 1])
    return us, vs, ws, t


def qap_witness(us, vs, ws, a):
    U = [0]
    V = [0]
    W = [0]
    for i, ai in enumerate(a):
        if ai % P:
            U = p_add(U, p_mul([ai % P], us[i]))
            V = p_add(V, p_mul([ai % P], vs[i]))
            W = p_add(W, p_mul([ai % P], ws[i]))
    return U, V, W


def qap_h(us, vs, ws, t, a):
    """h(X) = (U·V − W) / t(X)；赋值不合法时余式非零"""
    U, V, W = qap_witness(us, vs, ws, a)
    num = p_sub(p_mul(U, V), W)
    h, rem = p_divmod(num, t)
    return h, rem


# ============================== Groth16 ==============================
class SRS:
    """σ（证明密钥）与 τ（有毒废料）：τ 必须在 setup 之后销毁"""

    def __init__(self, us, vs, ws, t, ell, secret, randoms):
        self.alpha, self.beta, self.gamma, self.delta, self.x = randoms
        self.ell, self.us, self.vs, self.ws, self.t = ell, us, vs, ws, t
        self.ux = [p_eval(p, self.x) for p in us]
        self.vx = [p_eval(p, self.x) for p in vs]
        self.wx = [p_eval(p, self.x) for p in ws]
        self.tx = p_eval(t, self.x)
        g, d = self.gamma, self.delta
        # σ 里给公开输入的那一段要除以 γ，给 witness 的那一段除以 δ
        self.sigma_public = [(self.beta * self.ux[i] + self.alpha * self.vx[i] + self.wx[i]) / g
                             for i in range(len(us))]
        self.sigma_witness = [(self.beta * self.ux[i] + self.alpha * self.vx[i] + self.wx[i]) / d
                              for i in range(len(us))]
        self.tau = (self.alpha, self.beta, self.delta)   # 仅供「伪造演示」使用

    def prove(self, a, h, r, s):
        """A = α + Σa_i u_i(x) + rδ ； B = β + Σa_i v_i(x) + sδ ； C = (…)/δ + As + rB − rsδ"""
        d = self.delta
        A = self.alpha + sum((F(a[i]) * self.ux[i] for i in range(len(a))), F(0)) + r * d
        B = self.beta + sum((F(a[i]) * self.vx[i] for i in range(len(a))), F(0)) + s * d
        wt = sum((F(a[i]) * (self.beta * self.ux[i] + self.alpha * self.vx[i] + self.wx[i])
                  for i in range(self.ell + 1, len(a))), F(0))
        ht = p_eval(h, self.x) * self.tx
        C = (wt + ht) / d + A * s + r * B - r * s * d
        return A, B, C

    def verify(self, a_public, proof, pair_count=None):
        """A·B = α·β + Σ_{i=0}^{ℓ} a_i·σ_public_i · γ + C·δ —— 恰好 3 次「配对」"""
        A, B, C = proof
        lhs = A * B
        acc = F(0)
        for i in range(self.ell + 1):
            acc = acc + F(a_public[i]) * self.sigma_public[i]
        rhs = self.alpha * self.beta + acc * self.gamma + C * self.delta
        if pair_count is not None:
            pair_count.append(3)
        return lhs == rhs

    def forge(self, a_public):
        """论文 §Sim：知道 τ = (α, β, δ) 就能凭空造出「合法」证明。
        C = (A·B − αβ − Σ_{i=0}^{ℓ} a_i(βu_i(x)+αv_i(x)+w_i(x))) / δ
        注意求和只覆盖**公开输入** i = 0..ℓ —— 这正是 τ 必须销毁的原因。"""
        A, B = F(0x1234), F(0x5678)
        pub = sum((F(a_public[i]) * (self.beta * self.ux[i] + self.alpha * self.vx[i]
                                     + self.wx[i]) for i in range(self.ell + 1)), F(0))
        C = (A * B - self.alpha * self.beta - pub) / self.delta
        return A, B, C
