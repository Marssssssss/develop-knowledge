# CI/CD

持续集成 / 持续交付:流水线调度、构建缓存、部署策略、声明式交付。

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 048 | [DAG流水线调度/](./DAG流水线调度/) | Kahn 拓扑排序 + needs 依赖语义 + 关键路径 + 失败传播 | C / Python / Go |
| 049 | [内容寻址缓存/](./内容寻址缓存/) | hashFiles 键派生 + restore-keys 前缀回退 + LRU/TTL 淘汰 | C / Python / Go |
| 050 | [蓝绿与金丝雀发布/](./蓝绿与金丝雀发布/) | Recreate/Rolling/BlueGreen/Canary 四策略 + 分析门控回滚 | C / Python / Go |
| 292 | [GitHubActions工作流语义/](./GitHubActions工作流语义/) | `on` 过滤器语义（`*` 不跨 `/`、`**` 跨、量词 `?`/`+`）+ **顺序即语义**（最后命中者胜出）+ matrix include 两阶段并入 + 表达式宽松相等类型转换表 | Python / Go |
| 293 | [GitLabCI规则与父子流水线/](./GitLabCI规则与父子流水线/) | `rules` 首次匹配即生效且属性随之携带 + `rules:changes` 在非推送类流水线恒真 + `include` 150 个上限/30 秒预算 + 父子流水线最多两层 | Python / Go |
| 294 | [Jenkins声明式流水线/](./Jenkins声明式流水线/) | `post` 十条件固定执行顺序（`cleanup` 恒最后）+ agent timeout 是否计分配时间的区间差异 + `beforeOptions>beforeInput>beforeAgent` + matrix 两段式求值 | Python / Go |
| 295 | [ArgoCD同步与漂移检测/](./ArgoCD同步与漂移检测/) | `automated` 三态（null 等同开启）+ `(commit, 参数)` 唯一尝试键 + `prune`/`allowEmpty` 默认安全阀 + `ignoreDifferences` 去噪（**core 组 group 是空串**） | Python / Go |
| 296 | [制品晋升与语义化版本/](./制品晋升与语义化版本/) | SemVer 2.0.0 优先级全序（构建元数据不参与）+ OCI digest 语法（小写十六进制）与内容寻址 + tag 可变/digest 不可变 + referrers 回退 tag 方案 | Python / Go |

## 待研究

- [x] GitHub Actions workflow YAML 全量语法（on 触发 / matrix 策略 / 表达式与上下文）→ demo 292
- [x] GitLab CI 流水线（rules/include/parent-child pipeline）→ demo 293
- [x] Jenkins Pipeline（声明式 vs 脚本式 Jenkinsfile）→ demo 294
- [x] Argo CD 声明式部署与 GitOps 同步（reconcile/drift/self-heal）→ demo 295
- [x] 制品管理与语义化版本（artifact registry / 发布晋升通道）→ demo 296
- [ ] Argo Rollouts 渐进式交付（分析模板 AnalysisTemplate / 指标门控与自动回滚）
- [ ] 制品签名与构建溯源（cosign / SLSA provenance / in-toto 证明链）
