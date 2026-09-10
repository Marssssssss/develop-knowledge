# SKILL_RULES.md — 技能使用规则

本项目的 Skills 与 Marketplace Skill 安装/使用规范。所有 Skill 的添加必须先过一遍这里的规则。

## 一、Skill 添加原则

1. **不重复**：能用现有 Skill 解决的不装新 Skill。打开 `~/.workbuddy/skills/` 先扫一遍。
2. **任务驱动**：只在用户提出明确需求时才安装 Skill，不要"提前备货"。
3. **小而精**：一个 Skill 对应一个清晰的能力边界，不要追求大而全。
4. **来源审查**：来自第三方市场的 Skill，**必须**先调用 `skills-security-check` 过审计；P0 风险**绝不**安装，P1 风险需用户明示同意。

## 二、本项目建议 Skills

| Skill | 用途 | 状态 |
| --- | --- | --- |
| `wb-finance-skill` | 金融知识辅助（如涉及股票/期权等） | 按需 |
| `tencent-docs-routing` | 写 Office 文档 | 按需 |
| `tencent-pptx` | 生成 PPT | 按需 |
| `tencent-docx` | 写 Word | 按需 |
| `tencent-docs-sheet-generation` | 生成 Excel | 按需 |
| `find-skills` | 能力不足时检索 Marketplace | 兜底 |

## 三、Skill 落盘规则

- 项目级 Skill：放 `D:\开发研究\.workbuddy\skills\`（团队成员共享）
- 用户级 Skill：放 `~/.workbuddy/skills/`（跨项目复用）
- 默认优先用户级，除非团队需要。

## 四、与 Agent 规则的边界

| 维度 | Skill 规则（本文件） | Agent 规则 |
| --- | --- | --- |
| 关注点 | 装哪些 Skill、怎么装 | 怎么用工具、调用顺序 |
| 触发时机 | 用户提出新能力需求 | 每次执行任务 |
| 责任人 | 用户确认 | Agent 自动遵守 |

详见 [AGENT_RULES.md](./AGENT_RULES.md)。