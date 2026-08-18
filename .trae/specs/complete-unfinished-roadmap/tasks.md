# Tasks

> 严格按依赖顺序执行。阶段 1→3 为 Ledger 主线（当前最高优先级，真实数据阻断），阶段 4→5 在主线稳定后开展。
> 说明：`dry_run` 预览迁移已存在（`ledger_migration_runs` 中 `PREVIEW, migrated=18118`），阶段 1-2 负责把"预览"变成"真实 apply"。

## 阶段 1 · Ledger 迁移前置（数据来源与路径统一）
- [x] Task 1: 统一运行时索引路径（消除双库分歧）
  - [x] 1.1 审计所有 `get_ai_index_dir()` / workspace 索引库使用点（`core/path_resolver.py`、`services/indexer.py`、`services/ledger_*`），确认真实活动库统一到 AppData `%APPDATA%/No0_AI_V4/data/library_index.db`
  - [x] 1.2 实现"双库并存"启动检测与告警：workspace `.ai_index/library_index.db` 与权威库不一致时输出告警并统一到权威路径
  - [x] 1.3 单元测试：路径解析在开发/打包两模式下指向唯一权威路径
- [x] Task 2: 补齐 `book_b10eaeb7` 来源契约
  - [x] 2.1 找回/重新登记原始文档：核对 `C:\Users\11482\AppData\Roaming\No0_AI_V4\library\book_b10eaeb7\` 与项目 `历史资料/`（`library_manifest.json` 中 `historical_materials:*` 文档）的可映射来源
  - [x] 2.2 将来源文档登记为 `ledger_documents`，切分/登记段落 `ledger_passages`
  - [x] 2.3 为 18,117 张 `book_b10eaeb7` 卡建立真实 `source_anchor`（指向 passage）与 `source_document_id`
  - [x] 2.4 对无法溯源到原始文档的旧卡：显式标记为 `derived/draft`（不生成 authoritative claim）
  - [x] 2.5 校验：迁移后 `ledger_documents>0`、`ledger_passages>0`、无悬挂 claim
- [x] Task 3: 修复迁移干跑语义（`services/ledger_migration.py`）
  - [x] 3.1 `dry_run=True` 时 `migrated` 字段不再虚增（如实反映"未 apply"）
  - [x] 3.2 单测：dry_run 结果与 `ledger_claims` 实际计数一致

## 阶段 2 · Ledger 真实迁移与验证
- [x] Task 4: 正式迁移 apply 与 outbox drain
  - [x] 4.1 执行 `migrate_legacy_cards(dry_run=False)`（在来源契约满足后），写入真实 `ledger_claims`
  - [x] 4.2 全量 outbox 事件 drain 至 `APPLIED`，无 `DEAD_LETTER` 残留
  - [x] 4.3 单测/脚本验证：`ledger_claims≈cards`（可证明映射）、`ledger_outbox=0`
- [x] Task 4b（阻塞修复）: 旧 cards 表冷数据回填 + 投影 fingerprint 调整
  - [x] 4b.1 从冷 `.card` JSON 回填旧 schema cards 表缺失的 `content`/`source_chapter`/`detail_path`/`create_time`（幂等 ALTER + 分批回填，真实内容优先取 `original_fragment`）
  - [x] 4b.2 `_project_card_to_ledger` 的 claim fingerprint 加入 `card_id`（使每张卡成为独立 claim，模板占位卡不再 fingerprint 碰撞）
  - [x] 4b.3 单测：回填幂等、fingerprint 含 card_id、旧库 schema（无 content 列）下迁移不报 UNIQUE 冲突
  - [x] 4b.4 重新执行真实迁移 apply + drain，验证 `ledger_claims≈cards`、`ledger_outbox=0`
- [x] Task 5: 逐条 reconciliation 与双读 soak
  - [x] 5.1 运行/完善 `LedgerReconciliation`：旧卡与 ledger claim 逐条对齐，产出差异报告
  - [x] 5.2 启用 `dual_read_enabled` 并做真实数据双读比对（`LedgerDualRead`），记录一致性率
  - [x] 5.3 双读 soak 运行（观察期）并形成结论
- [x] Task 6: backup/restore drill 与 readiness 全绿
  - [x] 6.1 `LedgerBackup` 验证包含文档/段落/claims 的完整备份与恢复 drill
  - [x] 6.2 `LedgerReadiness.report()` 全项通过（迁移完成、outbox 清空、数量一致、双读一致、备份可恢复）
  - [x] 6.3 单测：readiness 在任一 blocker 时 fail-closed

## 阶段 3 · 灰度切换（两步）
- [x] Task 7: 第一步灰度（写库）
  - [x] 7.1 `config.yaml`：`ledger.read_mode=ledger`、`ledger.authoritative=true`，保留 `allow_legacy_card_projection=true`
  - [x] 7.2 冒烟回归：创作/检索/量化主链路在 authoritative 模式下工作（应用真实 boot，/health/ready 200，library/cards/search 200 返回 ledger claims 数据）
- [x] Task 7b（测试隔离修复）: 修复 `authoritative=true` 全局切换暴露的 2 个测试隔离回归
  - [x] 7b.1 `test_authoritative_readiness.py::test_readiness_blocks_card_claim_count_mismatch`：在用例内固定 `ledger.authoritative=false`（monkeypatch config_manager），恢复原"card/claim 计数 mismatch 阻断"语义测试
  - [x] 7b.2 `test_governance_api.py::test_rule_approval_reads_and_commits_real_database`：在 create_app() 前隔离 config（monkeypatch 或环境变量 `AIILIE_LEDGER_AUTHORITATIVE=false`），避免 bootstrap 对无迁移的 tmp 库执行 readiness 门禁
  - [x] 7b.3 重跑全部相关测试全绿
- [x] Task 8: 第二步关闭旧 direct card write（稳定后）
  - [x] 8.1 迁移剩余业务 writer（`services/indexer.py` 的 `insert_card/save_card` 及 `api/behavior_plugins.py` 等）到 `LedgerRepository`/`LedgerOutbox`
  - [x] 8.2 全量回归测试通过；旧 direct card write 移除（`allow_legacy_card_projection` 仅保留只读投影）

## 阶段 4 · SkillGovernance 观察期闭环
- [x] Task 9: SkillGovernance 状态与归因补全
  - [x] 9.1 退休/恢复完整状态机校验
  - [x] 9.2 效果统计按 `candidate_id@version` 归因
  - [x] 9.3 行为插件（QUANTIFIED/LEARNED）重启后从 Governance 完整重建
- [x] Task 10: 默认装配与旧投影对账
  - [x] 10.1 `feature.novel_multi_agent_enable` 默认装配评估与接线
  - [x] 10.2 旧投影（`universal_skills`/InfoCard/行为插件 override）与治理源全量对账，消除兼容写路径

## 阶段 5 · 其余独立计划收尾
- [x] Task 11: 受限代码执行隔离补全
  - [x] 11.1 Windows 实机 CPU/内存/进程树限制测试 + 子进程/孙进程逃逸测试
  - [x] 11.2 磁盘硬配额（替代 staging 监控）、操作系统级网络断网策略
  - [x] 11.3 任务状态持久化恢复（粗粒度→可恢复）
- [x] Task 12: 多文件受控自动修复
  - [x] 12.1 多文件 change set、每文件 base digest、跨文件冲突检测、原子多文件提交
  - [x] 12.2 测试驱动有限轮次自动修复 + 每轮 snapshot 与失败反馈闭环
- [x] Task 13: 独立构建服务
  - [x] 13.1 builder 服务：隔离源码/输出卷、CPU/内存/磁盘/进程限制
  - [x] 13.2 lockfile/dependency digest、产物 manifest + provenance、失败非零返回
  - [x] 13.3 签名/扫描/导入主应用前验证；Windows CI 验证 EXE 构建
- [x] Task 14: Poetry 研究闭环
  - [x] 14.1 作品/版本持久化 + Ledger provenance
  - [x] 14.2 平仄规则、中古音/普通话/平水韵分离、韵部候选
  - [x] 14.3 五绝/七绝/律诗/词牌格律验证、典故候选与回链、版本校勘、评测集
  - [x] 14.4 前端研究界面、生成/改写器
- [x] Task 15: Docling 可选生产接入
  - [x] 15.1 依赖锁定、模型工件预下载/离线模式、CPU/页数/超时/并发限制
  - [x] 15.2 结构化 provenance、表格/页码/版面 golden tests、生产 readiness、镜像分离
- [x] Task 16: 真实 Cosign/Sigstore 发布链路
  - [x] 16.1 `.sig`/bundle 绑定、证书 identity、issuer 校验、Rekor/Fulcio provenance
  - [x] 16.2 artifact digest 绑定验证、CI 发布签名、部署前强制验证、Windows 产物签名回归
- [x] Task 17: 发布基线与 CI 扩展
  - [x] 17.1 前端 typecheck 门禁修复（`npm run type-check` 清零）
  - [x] 17.2 CI 增加 Windows runner（Windows Job Object + EXE 打包验证）
  - [x] 17.3 未跟踪/已修改文件提交清理与发布基线归档

# Task Dependencies
- Task 2 依赖 Task 1（路径统一前置，避免对错库迁移）
- Task 3 依赖 Task 1、Task 2（干跑语义修复需在真实来源就绪后验证）
- Task 4 依赖 Task 2、Task 3（来源契约满足前禁止 apply）
- Task 5 依赖 Task 4
- Task 6 依赖 Task 4、Task 5
- Task 7 依赖 Task 6（readiness 全绿才灰度）
- Task 8 依赖 Task 7（灰度稳定后关闭旧写路径）
- Task 9、Task 10 依赖 Task 7（Ledger 稳定后开展，可并行）
- Task 11 依赖 Task 8（发布基线稳定后）；Task 12–17 无强依赖，可在阶段 5 内并行，但 Task 17 的 CI 扩展应吸收 Task 11/13/16 的产物
