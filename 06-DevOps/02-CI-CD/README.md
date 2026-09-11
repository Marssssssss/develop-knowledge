# CI/CD

持续集成 / 持续交付:流水线调度、构建缓存、部署策略、声明式交付。

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 048 | [DAG流水线调度/](./DAG流水线调度/) | Kahn 拓扑排序 + needs 依赖语义 + 关键路径 + 失败传播 | C / Python / Go |
| 049 | [内容寻址缓存/](./内容寻址缓存/) | hashFiles 键派生 + restore-keys 前缀回退 + LRU/TTL 淘汰 | C / Python / Go |
| 050 | [蓝绿与金丝雀发布/](./蓝绿与金丝雀发布/) | Recreate/Rolling/BlueGreen/Canary 四策略 + 分析门控回滚 | C / Python / Go |

## 待研究

- [ ] GitHub Actions workflow YAML 全量语法（on 触发 / matrix 策略 / 表达式与上下文）
- [ ] GitLab CI 流水线（rules/include/parent-child pipeline）
- [ ] Jenkins Pipeline（声明式 vs 脚本式 Jenkinsfile）
- [ ] Argo CD 声明式部署与 GitOps 同步（reconcile/drift/self-heal）
- [ ] 制品管理与语义化版本（artifact registry / 发布晋升通道）
