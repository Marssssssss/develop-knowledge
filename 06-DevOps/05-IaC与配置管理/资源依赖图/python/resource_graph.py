"""Terraform 资源依赖图与拓扑排序

来源:
- Terraform Internals: Dependency Graph
  (docs.hashicorp.com/terraform/internals/v1.15.x/graph)
  "Terraform builds a dependency graph and uses it to perform operations"
- HashiCorp 2016 演讲 "Applying Graph Theory to Infrastructure as Code"
  (kindatechnical.com/terraform-fundamentals 引述 7 步流水线)
- KodeKloud Notes "Understanding the Resource Graph"
  "By default Terraform runs up to 10 operations in parallel"

核心:
1. 资源是 vertex,reference / depends_on 是 directed edge
2. 拓扑排序必须无环 (validate)
3. 拓扑排序 "分层" = 并行批次,每层内可并行执行(默认 -parallelism=10)
4. 应用顺序 = 创建(正序)、销毁(反序)
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class Graph:
    """邻接表形式的 DAG;edges[A] = [B, C] 表示 A → B, A → C(B 依赖 A)"""
    edges: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    nodes: set[str] = field(default_factory=set)

    def add_node(self, n: str):
        self.nodes.add(n)

    def add_edge(self, frm: str, to: str):
        """frm → to(创建顺序:frm 先,to 后)"""
        self.add_node(frm)
        self.add_node(to)
        self.edges[frm].append(to)

    def in_degree(self) -> dict[str, int]:
        deg = {n: 0 for n in self.nodes}
        for frm, targets in self.edges.items():
            for t in targets:
                deg[t] = deg.get(t, 0) + 1
        return deg

    def has_cycle(self) -> bool:
        """Kahn 算法:出队节点数 < 总节点数 = 有环"""
        deg = self.in_degree()
        q = deque([n for n, d in deg.items() if d == 0])
        visited = 0
        while q:
            n = q.popleft()
            visited += 1
            for t in self.edges.get(n, []):
                deg[t] -= 1
                if deg[t] == 0:
                    q.append(t)
        return visited < len(self.nodes)

    def topo_layers(self) -> list[list[str]]:
        """拓扑排序 + 分层(每层 = 一个并行批次)

        一致性:同层内按节点名字典序排,避免不同运行顺序不同
        (按 KodeKloud "file ordering does not control execution order"
        但同层确定性便于复现)
        """
        deg = self.in_degree()
        # 复制入度,初始层 = 所有无依赖节点,排序后入队
        layers = []
        current = sorted([n for n, d in deg.items() if d == 0])
        while current:
            layers.append(current)
            next_layer = []
            for n in current:
                for t in self.edges.get(n, []):
                    deg[t] -= 1
                    if deg[t] == 0:
                        next_layer.append(t)
            current = sorted(next_layer)
        if sum(len(l) for l in layers) < len(self.nodes):
            raise ValueError("cycle detected; cannot topologically sort")
        return layers

    def apply_order(self) -> list[str]:
        """flatten layers 为单序 (terraform apply 串行视角)"""
        return [n for layer in self.topo_layers() for n in layer]

    def destroy_order(self) -> list[str]:
        """apply_order 的完全逆序(destroy 反向)"""
        return list(reversed(self.apply_order()))

    def parallel_schedule(self, max_parallel: int = 10) -> list[list[str]]:
        """按层调度,每层最多 -parallelism=N 个并发 worker
        (Terraform 默认 10;超过则跨多批)
        """
        layers = self.topo_layers()
        schedule = []
        for layer in layers:
            # 把该层切成 max_parallel 的小块
            for i in range(0, len(layer), max_parallel):
                schedule.append(layer[i:i + max_parallel])
        return schedule


# -----------------------------------------------------------------------------
# Demo 1: 经典菱形 4 节点
# -----------------------------------------------------------------------------

def demo_diamond():
    """aws_vpc → aws_subnet + aws_sg → aws_instance
    隐式依赖(通过 attribute ref)与 depends_on 等价:
        F 建在最前,A 与 B 平行,C 等 A B 都完成才能建
    """
    g = Graph()
    # 隐式依赖(常见)
    g.add_edge("aws_vpc.main", "aws_subnet.public")   # subnet.vpc_id = aws_vpc.main.id
    g.add_edge("aws_vpc.main", "aws_security_group.web")
    g.add_edge("aws_subnet.public", "aws_instance.web")
    g.add_edge("aws_security_group.web", "aws_instance.web")
    assert not g.has_cycle(), "diamond 应无环"
    print("--- Demo 1: 经典菱形 (VPC / Subnet+SG / Instance) ---")
    for i, layer in enumerate(g.topo_layers()):
        print(f"  Layer {i+1} (可并行): {layer}")
    print()


# -----------------------------------------------------------------------------
# Demo 2: depends_on 显式依赖 (no attribute ref)
# -----------------------------------------------------------------------------

def demo_explicit():
    """EC2 实例需要 IAM role policy 先 ready 才能 assume,
    但实例的属性不直接引用 policy. → depends_on 唯一选项.

    spec 提示:"Use depends_on only as a last resort, since it makes
    Terraform plan more conservatively, replacing more resources than necessary"
    """
    g = Graph()
    # IAM role instance profile 建实例的 profile_name 引用 role.name
    g.add_edge("aws_iam_role.app", "aws_iam_instance_profile.app")
    # 实例 → profile 是隐式
    g.add_edge("aws_iam_instance_profile.app", "aws_instance.app")
    # depends_on("policy must be attached before instance") 即实例显式等 policy
    g.add_edge("aws_iam_role_policy_attachment.s3_access", "aws_instance.app")
    # role 是 policy_attachment 的隐式依赖
    g.add_edge("aws_iam_role.app", "aws_iam_role_policy_attachment.s3_access")

    print("--- Demo 2: depends_on 显式依赖 ---")
    for i, layer in enumerate(g.topo_layers()):
        print(f"  Layer {i+1}: {layer}")
    print(f"  apply  顺序 = {g.apply_order()}")
    print(f"  destroy 顺序 = {g.destroy_order()}")
    print()


# -----------------------------------------------------------------------------
# Demo 3: 环检测
# -----------------------------------------------------------------------------

def demo_cycle():
    """A → B → C → A 显式环"""
    g = Graph()
    g.add_edge("A", "B")
    g.add_edge("B", "C")
    g.add_edge("C", "A")
    print("--- Demo 3: 环检测 ---")
    print(f"  has_cycle = {g.has_cycle()}")
    try:
        g.topo_layers()
        print("  错误:未检测到环!")
    except ValueError as e:
        print(f"  topo_layers 抛 ValueError: {e}")
    print()


# -----------------------------------------------------------------------------
# Demo 4: 并行调度 + parallelism 上限
# -----------------------------------------------------------------------------

def demo_parallelism():
    """11 个无依赖资源 + 1 个聚合依赖 → 第二层 1 节点,第一层 11 节点
    默认 parallelism=10 → 第一层切成 2 批(10 + 1),共 3 批
    """
    g = Graph()
    for i in range(11):
        g.add_edge(f"res_{i}", "aggregator")
    print("--- Demo 4: parallelism=10 跨批 ---")
    sched = g.parallel_schedule(max_parallel=10)
    for i, batch in enumerate(sched):
        print(f"  batch {i+1}: {batch}")
    print()


# -----------------------------------------------------------------------------
# Demo 5: 真实案例:5 模块 50 资源随机制造
# -----------------------------------------------------------------------------

def stress_test():
    """20 节点,DAG 随机生成,含并行分支,统计层数"""
    import random
    random.seed(42)
    nodes = [f"node_{i}" for i in range(20)]
    g = Graph()
    for n in nodes:
        g.add_node(n)
        # 每个 node 依赖 0..2 个前面 node (索引 < 当前)
        deps = random.sample([nodes[j] for j in range(nodes.index(n))], k=min(2, nodes.index(n)))
        for d in deps:
            g.add_edge(d, n)
    assert not g.has_cycle()
    layers = g.topo_layers()
    sched = g.parallel_schedule(max_parallel=10)
    print("--- Demo 5: 20 节点随机 DAG ---")
    print(f"  顶点数 = {len(g.nodes)}, 边数 = {sum(len(v) for v in g.edges.values())}")
    print(f"  分层数 = {len(layers)}")
    print(f"  批次数 (-parallelism=10) = {len(sched)}")
    print(f"  层大小分布: {[len(l) for l in layers]}")
    print()


# -----------------------------------------------------------------------------

def main():
    print("=== Terraform 资源依赖图与拓扑排序 demo ===\n")
    demo_diamond()
    demo_explicit()
    demo_cycle()
    demo_parallelism()
    stress_test()


if __name__ == "__main__":
    main()
