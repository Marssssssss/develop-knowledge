# DevSecOps

> 2026-09-12 巡检类目自动拓展新增（AGENT_RULES §一.5）：补全 06-DevOps 维度下"安全左移"主流方向。

## 简介

- DevSecOps = 把安全作为**工程属性嵌入流水线**，而非发布前的最后一道人工门禁；核心思想是 **shift left**（左移：在第一处 commit 就开始安全检查，此时修复成本最低）。
- OpenTelemetry 时代的安全控制越来越多以"安全即代码"（policy as code）形式落进 CI/CD：扫描器在 PR/构建/部署各阶段自动产出信号，人只处理例外。
- 框架锚点：**OWASP DSOMM**（DevSecOps 成熟度模型）与 **OWASP DSOVS**（DevSecOps 验证标准：7 阶段 39 控制 4 成熟度级）、NIST SP 800-218（SSDF，美国联邦 attestation 基础）。

## OWASP DSOVS 七阶段控制面（依据官方 assessment 页）

| 阶段 | 代表控制 |
| --- | --- |
| Organisation | 风险评估 / 安全培训 / Security Champion / 安全上报 |
| Requirements | 合规策略 / 安全需求与标准 / 安全用户故事 / 安全缺陷跟踪 |
| Design | 安全架构评审 / 威胁建模（STRIDE） |
| Code/Build | SAST / 硬编码密钥检测 / SCA / 容器扫描 / 依赖管理 / 许可证合规 |
| Test | DAST / IAST / 渗透测试 / 安全测试覆盖 |
| Release/Deploy | 制品签名 / 密钥管理 / IaC 安全部署 / 合规扫描 / 策略执行 |
| Operate/Monitor | 环境与应用加固 / 安全日志 / 漏洞披露 / 证书管理 / 攻击面管理 |

## 核心概念

- **SAST**（静态应用安全测试）：不执行代码，扫源码中的注入/XSS/弱加密等模式；跑在 commit/PR 阶段（工具如 Semgrep、CodeQL）。
- **SCA**（软件成分分析）：解析第三方依赖树，生成 **SBOM** 后对照 CVE/许可证库；现代应用 80% 是第三方代码（OWASP Dependency-Check、Snyk）。
- **密钥检测 vs 密钥管理**：DSOVS 明确二者互补——扫描（CODE-002）抓已提交的密钥，管理（REL-003）从源头让密钥根本不需要硬编码（Vault 动态密钥 / secretless）。
- **SBOM**（软件物料清单）：CycloneDX/SPDX 格式的组件清单，是供应链安全的库存基础；配合 Grype/Trivy 做"清单 × CVE 库"比对。
- **SLSA**：构建完整性/来源证明分级框架；配合 Sigstore/cosign 做制品签名。
- **成熟度分级**（DSOVS 每控制 4 级）：0 无工具 → 1 按需手动扫描 → 2 接入流水线自动扫描并回传构建 → 3 发现自动进 issue 跟踪并持续调优。

## 待研究

- [ ] SAST 语义规则引擎原理（Semgrep 模式匹配 / CodeQL 数据流）
- [ ] SBOM 格式对比与漏洞关联（CycloneDX vs SPDX）
- [ ] 容器镜像扫描与签名（Trivy / cosign / SLSA provenance）
- [ ] 密钥扫描算法（熵检测 + 模式匹配）与 pre-commit 防线
- [ ] IaC 策略即代码（OPA/Rego 最小实现）
- [ ] DAST 原理（爬虫 + 活动注入探测）
- [ ] 威胁建模 STRIDE 与攻击树

## 参考资料（实际阅读过的权威来源）

- [OWASP DevSecOps Verification Standard — 官方 assessment 页](https://owasp.org/www-project-devsecops-verification-standard/assessment) — 7 阶段 39 控制全景、每控制 4 级成熟度定义
- [OWASP DSOVS CODE-005 Software Composition Analysis (SCA)](https://owasp.org/www-project-devsecops-verification-standard/assessment/control.html?code=CODE-005) — SCA 四级成熟度、SBOM 在流水线中的位置、Dependency-Check CPE/CVE 关联原理
- [OWASP DSOVS CODE-004 SAST](https://owasp.org/www-project-devsecops-verification-standard/assessment/control.html?code=CODE-004) — SAST 成熟度分级与 Semgrep 规则模型
- [OWASP DSOVS REL-003 Secret Management](https://owasp.org/www-project-devsecops-verification-standard/assessment/control.html?code=REL-003) — 密钥管理三级演进（集中存储 → 自动注入+轮换 → 动态/无密钥）
- [OWASP London Chapter: DSOMM from Theory to Enforcement](https://owasp.org/www-chapter-london/assets/slides/DSOMM_from_Theory_to_Enforcement_-_Raz_Probstein.pdf) — DSOMM 维度/子维度与 Level 1-3 实践清单、各语言 OSS 工具映射（GoSec/Semgrep/OSV-Scanner/Gitleaks 等）
