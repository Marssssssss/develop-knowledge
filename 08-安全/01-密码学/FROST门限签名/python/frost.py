"""FROST 两轮门限签名，逐行对应 RFC 9591 §4、§5 与附录 C。"""

import os

import frost_group as grp

Q = grp.Q


def random_bytes(n):
    return os.urandom(n)


def random_scalar():
    return int.from_bytes(random_bytes(32), "big") % Q


# ------------------------------------------------------- §4.1 nonce 生成
def nonce_generate(secret, rand=None):
    """H3(random_bytes(32) || SerializeScalar(secret))。

    `rand` 让自检可以确定性地钉死随机源——否则「测试通过」只是运气好。
    """
    rb = rand if rand is not None else random_bytes(32)
    return grp.h3(rb + grp.serialize_scalar(secret))


# ---------------------------------------------------- 附录 C.1 Shamir
def polynomial_evaluate(x, coeffs):
    """Horner 法求值；coeffs[0] 是常数项（秘密）。"""
    value = 0
    for c in reversed(coeffs):
        value = (value * x + c) % Q
    return value


def secret_share_shard(s, coefficients, max_participants):
    coeffs = [s] + list(coefficients)
    return [(i, polynomial_evaluate(i, coeffs)) for i in range(1, max_participants + 1)], coeffs


def derive_interpolating_value(L, x_i):
    """§4.2 —— 拉格朗日系数。x_i 必须在 L 中，且 L 无重复。"""
    if x_i not in L:
        raise ValueError("invalid parameters")
    for x in L:
        if L.count(x) > 1:
            raise ValueError("invalid parameters")
    num, den = 1, 1
    for x_j in L:
        if x_j == x_i:
            continue
        num = num * x_j % Q
        den = den * (x_j - x_i) % Q
    return num * pow(den, Q - 2, Q) % Q


def secret_share_combine(shares, min_participants):
    if len(shares) < min_participants:
        raise ValueError("invalid parameters")
    xs = [i for i, _ in shares]
    total = 0
    for x, y in shares:
        total = (total + y * derive_interpolating_value(xs, x)) % Q
    return total


def polynomial_interpolate_constant(points):
    return secret_share_combine(points, len(points))


# ------------------------------------------------- 附录 C.2 Feldman VSS
def vss_commit(coeffs):
    """对多项式的每个系数做承诺：C_j = g^{a_j}。"""
    return [grp.scalar_base_mult(c) for c in coeffs]


def vss_verify(share_i, commitments):
    """校验 share 是否落在被承诺的那条多项式上：g^{f(i)} == prod C_j^{i^j}。"""
    i, y = share_i
    lhs = grp.scalar_base_mult(y)
    rhs = 1
    for j, c in enumerate(commitments):
        rhs = grp.element_add(rhs, grp.scalar_mult(c, pow(i, j, Q)))
    return lhs == rhs


# ------------------------------------------------------------- §4.3 列表
def encode_group_commitment_list(commitment_list):
    out = b""
    for ident, hiding, binding in commitment_list:
        out += grp.serialize_scalar(ident) + grp.serialize_element(hiding) \
            + grp.serialize_element(binding)
    return out


def participants_from_commitment_list(commitment_list):
    return [ident for ident, _, _ in commitment_list]


def binding_factor_for_participant(binding_factor_list, identifier):
    for i, bf in binding_factor_list:
        if i == identifier:
            return bf
    raise ValueError("invalid participant")


# -------------------------------------------------- §4.4 / §4.5 / §4.6
def compute_binding_factors(group_public_key, commitment_list, msg):
    pk_enc = grp.serialize_element(group_public_key)
    msg_hash = grp.h4(msg)
    com_hash = grp.h5(encode_group_commitment_list(commitment_list))
    prefix = pk_enc + msg_hash + com_hash
    return [(ident, grp.h1(prefix + grp.serialize_scalar(ident)))
            for ident, _, _ in commitment_list]


def compute_group_commitment(commitment_list, binding_factor_list):
    r = grp.identity()
    for ident, hiding, binding in commitment_list:
        rho = binding_factor_for_participant(binding_factor_list, ident)
        r = grp.element_add(r, grp.scalar_mult(binding, rho))
        r = grp.element_add(r, hiding)
    return r


def compute_challenge(group_commitment, group_public_key, msg):
    return grp.h2(grp.serialize_element(group_commitment)
                  + grp.serialize_element(group_public_key) + msg)


# ------------------------------------------------------------- 两轮协议
def commit(sk_i, rand_h=None, rand_b=None):
    """§5.1 —— 两个 nonce + 两个承诺。"""
    hiding = nonce_generate(sk_i, rand_h)
    binding = nonce_generate(sk_i, rand_b)
    return (hiding, binding), (grp.scalar_base_mult(hiding), grp.scalar_base_mult(binding))


def sign(identifier, sk_i, group_public_key, nonce_i, msg, commitment_list):
    """§5.2 —— 产出签名份额。"""
    bfl = compute_binding_factors(group_public_key, commitment_list, msg)
    rho = binding_factor_for_participant(bfl, identifier)
    group_commitment = compute_group_commitment(commitment_list, bfl)
    xs = participants_from_commitment_list(commitment_list)
    lam = derive_interpolating_value(xs, identifier)
    c = compute_challenge(group_commitment, group_public_key, msg)
    hiding, binding = nonce_i
    return (hiding + binding * rho + lam * sk_i * c) % Q


def aggregate(commitment_list, msg, group_public_key, sig_shares):
    """§5.3 —— 只把 z 加起来，R 由承诺列表重新算出。"""
    bfl = compute_binding_factors(group_public_key, commitment_list, msg)
    r = compute_group_commitment(commitment_list, bfl)
    z = 0
    for zi in sig_shares:
        z = (z + zi) % Q
    return r, z


def verify_signature_share(identifier, pk_i, comm_i, sig_share_i,
                           commitment_list, group_public_key, msg):
    """§5.3 —— 可识别中止：定位到具体哪个参与者使坏。"""
    bfl = compute_binding_factors(group_public_key, commitment_list, msg)
    rho = binding_factor_for_participant(bfl, identifier)
    group_commitment = compute_group_commitment(commitment_list, bfl)
    hiding_com, binding_com = comm_i
    comm_share = grp.element_add(hiding_com, grp.scalar_mult(binding_com, rho))
    c = compute_challenge(group_commitment, group_public_key, msg)
    xs = participants_from_commitment_list(commitment_list)
    lam = derive_interpolating_value(xs, identifier)
    lhs = grp.scalar_base_mult(sig_share_i)
    rhs = grp.element_add(comm_share, grp.scalar_mult(pk_i, c * lam % Q))
    return lhs == rhs


def schnorr_verify(r, z, group_public_key, msg):
    """附录 B —— 普通 Schnorr 验签：g^z == R * PK^c。"""
    c = compute_challenge(r, group_public_key, msg)
    return grp.scalar_base_mult(z) == grp.element_add(r, grp.scalar_mult(group_public_key, c))


# ------------------------------- 对照：不带绑定因子的朴素门限 Schnorr
def naive_group_commitment(hiding_list):
    """R = prod(D_i)，不含 binding 项，因此与消息无关。"""
    r = grp.identity()
    for d in hiding_list:
        r = grp.element_add(r, d)
    return r


def naive_sign_share(identifier, sk_i, nonce, group_public_key, msg, r):
    """z_i = nonce + lambda_i * sk_i * c —— 没有 rho 这一项。

    这是 FROST 之前朴素门限 Schnorr 的做法：同一个 nonce 复用在两条消息上
    就能直接解出 sk_i，FROST 的绑定因子正是为了堵这个洞。
    """
    c = compute_challenge(r, group_public_key, msg)
    return (nonce + sk_i * c) % Q


def recover_sk_from_reused_nonce(z1, z2, c1, c2):
    """朴素方案下 nonce 复用时的私钥恢复：sk = (z1 - z2) / (c1 - c2)。"""
    return (z1 - z2) * pow((c1 - c2) % Q, Q - 2, Q) % Q
