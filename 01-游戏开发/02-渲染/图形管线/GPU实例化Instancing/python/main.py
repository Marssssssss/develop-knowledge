# -*- coding: utf-8 -*-
"""GPU 实例化渲染(Instancing)软件模拟。

依据 LearnOpenGL "Instancing" 归纳的原理:
  - 渲染顶点很快,但每次 draw call 前的准备工作(CPU→GPU 总线)很慢,
    数千次 draw call 会先于 GPU 成为瓶颈;
  - glDrawArraysInstanced/glDrawElementsInstanced 把 N 次调用合并为 1 次;
  - gl_InstanceID 从 0 起为每个实例递增,是顶点着色器的实例标识;
  - uniform 数组方案受 uniform 上限约束;instanced array 通过
    glVertexAttribDivisor(attr, 1) 让顶点属性"每实例更新一次";
  - mat4 变换矩阵要占 4 个连续顶点属性位置(每属性最大一个 vec4)。
本模拟用"draw call 记账 + 属性取数模拟"复刻上述机制。
"""

# ---------- draw call 记账模型 ----------

CPU_PREP_US = 25.0   # 每次 draw call 的 CPU 端准备开销(绑定 VAO/纹理/设置 uniform 等)
GPU_VTX_NS = 5.0     # 每顶点的 GPU 顶点着色耗时(纳秒,建模用)


class DrawCallLog:
    """记录一次 draw call:实例数、顶点数、CPU 准备耗时。"""

    def __init__(self):
        self.calls = []

    def draw(self, vertex_count, instance_count):
        self.calls.append((vertex_count, instance_count, CPU_PREP_US))
        # 顶点着色器执行次数 = 每实例的顶点数 × 实例数
        return vertex_count * instance_count

    def total_cpu_us(self):
        return sum(c[2] for c in self.calls)

    def total_vtx(self):
        return sum(c[0] * c[1] for c in self.calls)

    def total_time_ms(self):
        return (self.total_cpu_us() + self.total_vtx() * GPU_VTX_NS / 1000.0) / 1000.0


# ---------- 顶点属性流:模拟 divisor 机制 ----------

class VertexStream:
    """一组属性流:per-vertex(divisor=0)或 per-instance(divisor=1)。

    模拟 GPU 硬件的取数规则:divisor=0 的属性在每个顶点前进一格,
    divisor=1 的属性在每个实例前进一格 → 顶点着色器对
    (顶点 j, 实例 i) 读到的就是 vertex_data[j] / instance_data[i]。
    """

    def __init__(self, vertex_data, instance_data):
        self.vertex_data = vertex_data      # per-vertex 属性(如位置、颜色)
        self.instance_data = instance_data  # per-instance 属性(如偏移、矩阵)

    def fetch(self, vertex_index, instance_index):
        """GPU 为 (j, i) 组合取数的等价模拟,并检查越界。"""
        if not (0 <= vertex_index < len(self.vertex_data)):
            raise IndexError("vertex attr out of range: %d" % vertex_index)
        if not (0 <= instance_index < len(self.instance_data)):
            raise IndexError("instance attr out of range: %d" % instance_index)
        return self.vertex_data[vertex_index], self.instance_data[instance_index]


def instanced_draw(stream, instance_count, log):
    """一次 glDrawElementsInstanced:模拟逐实例逐顶点的取数序列。

    返回顶点着色器看到的前若干 (j, i) 取样,供断言核对 gl_InstanceID。
    """
    vcount = len(stream.vertex_data)
    log.draw(vcount, instance_count)
    samples = []
    for i in range(instance_count):
        # 硬件按 (j, i) 取数:vertex 属性随 j 前进,instance 属性只在 i 变化时前进
        for j in range(vcount):
            v, inst = stream.fetch(j, i)
            if j == 0:  # 每实例只采一个样,避免样本爆炸
                samples.append((i, v, inst))
    return samples


# ---------- 实例数据:小行星带 ----------

def asteroid_belt(count, seed=42):
    """随机生成 count 个实例变换:半径 50 的圆环上随机分布(LearnOpenGL 场景)。"""
    import random
    rng = random.Random(seed)
    out = []
    for i in range(count):
        angle = i / count * 2 * 3.14159265
        radius = 50.0 + rng.uniform(-25.0, 25.0)
        out.append({
            "offset": (radius * __import__("math").cos(angle),
                      rng.uniform(-5.0, 5.0),
                      radius * __import__("math").sin(angle)),
            "scale": rng.uniform(0.05, 0.25),
        })
    return out


def mat4_columns(transform):
    """mat4 占 4 个顶点属性位置:模拟"4 个 vec4 列"的拆分。"""
    ox, oy, oz = transform["offset"]
    s = transform["scale"]
    return [
        (s, 0.0, 0.0, 0.0),   # 第 0 列:location 3
        (0.0, s, 0.0, 0.0),   # 第 1 列:location 4
        (0.0, 0.0, s, 0.0),   # 第 2 列:location 5
        (ox, oy, oz, 1.0),    # 第 3 列:location 6(平移)
    ]


# ---------- 自检 ----------

def main():
    # 1) divisor 取数:per-vertex 属性随顶点前进,per-instance 随实例前进
    vs = VertexStream(
        vertex_data=[(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)],  # 四边形 4 顶点
        instance_data=[(float(i),) for i in range(100)],
    )
    log = DrawCallLog()
    samples = instanced_draw(vs, 100, log)
    # gl_InstanceID 从 0 开始:第 43 个实例的 ID 是 42(LearnOpenGL 原文口径)
    assert samples[42] == (42, (0.0, 0.0), (42.0,)), samples[42]
    assert len(log.calls) == 1 and log.calls[0][1] == 100  # 一次调用画 100 实例
    # 任意 (j, i):vertex 属性只跟 j 有关,instance 属性只跟 i 有关
    for j in range(4):
        for i in (0, 37, 99):
            v, inst = vs.fetch(j, i)
            assert v == vs.vertex_data[j]
            assert inst == (float(i),)

    # 2) uniform 数组上限 vs instanced array:模拟 Khronos 的保证下限
    #    GL_MAX_VERTEX_UNIFORM_COMPONENTS 的保证下限是 1024(见 OpenGL 4.6 规范表)
    MAX_UNIFORM_COMPONENTS = 1024
    vec2_per_instance = 2  # 每实例一个 vec2 偏移
    max_uniform_instances = MAX_UNIFORM_COMPONENTS // vec2_per_instance
    assert max_uniform_instances == 512
    # uniform 方案装不下 1000 个实例,instanced array 只受显存约束(无此上限)
    belt_1000 = asteroid_belt(1000)
    assert len(belt_1000) > max_uniform_instances

    # 3) draw call 记账:逐个绘制 vs 实例化(LearnOpenGL 小行星带口径)
    ROCK_VERTICES = 576  # 岩石模型顶点数(教程原文)
    n_asteroids = 1000

    naive = DrawCallLog()
    for _ in range(n_asteroids):
        naive.draw(ROCK_VERTICES, 1)   # 1000 次调用 + 1 次行星 = 1001
    naive.draw(ROCK_VERTICES, 1)
    assert len(naive.calls) == 1001

    inst = DrawCallLog()
    inst.draw(ROCK_VERTICES, n_asteroids)  # 1000 实例合 1 次
    inst.draw(ROCK_VERTICES, 1)            # 行星
    assert len(inst.calls) == 2

    # 顶点处理量完全相同(都做 576×1000 次顶点着色)
    assert naive.total_vtx() == inst.total_vtx() == ROCK_VERTICES * (n_asteroids + 1)
    # CPU 端准备开销:1001×25μs ≈ 25ms vs 2×25μs = 50μs,相差 500 倍
    assert naive.total_cpu_us() > inst.total_cpu_us() * 100

    # 4) 教程规模数字:100000 实例 × 576 顶点 ≈ 5700 万顶点,仍只需 2 次 draw call
    big = DrawCallLog()
    big.draw(ROCK_VERTICES, 100000)
    big.draw(ROCK_VERTICES, 1)
    assert len(big.calls) == 2
    assert big.total_vtx() == 576 * 100001  # ≈ 57.6M 顶点/帧

    # 5) mat4 实例属性的 4 列拆分:第 3 列携带平移,前 3 列携带缩放
    t = {"offset": (1.0, 2.0, 3.0), "scale": 0.5}
    cols = mat4_columns(t)
    assert len(cols) == 4
    assert cols[3] == (1.0, 2.0, 3.0, 1.0)
    assert cols[0][0] == 0.5 and cols[1][1] == 0.5 and cols[2][2] == 0.5
    assert cols[0][1] == 0.0 and cols[3][0] == 1.0  # 非对角与平移互不串位

    # 6) 端到端耗时建模:draw call 次数主导小场景帧时间
    t_naive = naive.total_time_ms()
    t_inst = inst.total_time_ms()
    assert t_naive > t_inst, (t_naive, t_inst)

    print("ALL TESTS PASSED")
    print("naive: %d draw calls, cpu prep = %.1f ms" % (len(naive.calls), naive.total_cpu_us() / 1000.0))
    print("instanced: %d draw calls, cpu prep = %.3f ms" % (len(inst.calls), inst.total_cpu_us() / 1000.0))
    print("100k asteroids: %d draw calls, %d vertices/frame" % (len(big.calls), big.total_vtx()))


if __name__ == "__main__":
    main()
