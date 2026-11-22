# 深度登记 · 门店陈列审计台

- 规模：中型。连锁酒水巡店员从本期柜位名册抽样，用生效陈列图核验独立图证，输出可复核罚额。
- 核心规则：按有效时点和店组权重分层核验，证据不足与已证实的缺品分开，罚则同柜互斥。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 巡店收件 | 核对档件结构、门店编号与真实到访时点 | `GET /api/intake` | `app/modules/intake/csv_reader.py` `app/modules/intake/clock.py` `app/modules/intake/records.py` | `tests/test_intake.py` |
| M2 货架版本 | 查看柜位生效图、交叠与旧商品名 | `GET /api/plans` | `app/modules/plans/windows.py` `app/modules/plans/aliases.py` | `tests/test_plans.py` |
| M3 照片证据 | 复核图证原件、同图复用和清点完备性 | `GET /api/photos` | `app/modules/photos/fingerprints.py` `app/modules/photos/bindings.py` `app/modules/photos/observations.py` | `tests/test_photos.py` |
| M4 抽样覆盖 | 计算各店组柜位分配与待补缺口 | `GET /api/coverage` | `app/modules/coverage/sampling.py` `app/modules/coverage/service.py` | `tests/test_coverage.py` |
| M5 罚则裁决 | 确定缺品、乱陈、排面和价签的处罚 | `GET /api/penalties` | `app/modules/penalties/rules.py` `app/modules/penalties/service.py` | `tests/test_penalties.py` |
| M6 结算勾稽 | 按原件、柜位和金额对账并留存批次 | `GET /api/settlement` `POST /api/settlement/runs` | `app/modules/settlement/reconcile.py` `app/modules/settlement/service.py` `app/store.py` | `tests/test_settlement.py` |
| M7 异议复核 | 留存复核时序、重算净额并导出 | `GET /api/appeals/{run_id}` `POST /api/appeals/{run_id}/events` | `app/modules/appeals/transitions.py` `app/modules/appeals/service.py` | `tests/test_appeals.py` |
| M8 罚则版本与批次 | 按巡检时点选规则版本，逐柜标执行额，留存批次历史 | `GET /api/settlement/runs/{run_id}/batch` `GET /api/settlement/runs` `POST /api/settlement/runs/{run_id}/rollback` | `app/modules/penalties/catalog.py` `app/modules/settlement/service.py` `app/store.py` | `tests/test_rule_versions.py` `tests/test_batches.py` |

## 陷阱登记

| TRAP | 样例与现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | `sample_data/field/stores.csv` 是 GB18030 | 乱码 | 解码后保留中文原字段 | `app/modules/intake/csv_reader.py` | `tests/test_intake.py::test_encoded_archive_preserves_multiline_chinese_source` |
| TRAP-02 | `sample_data/field/slots.csv` 带 BOM | 首列失效 | 单独识别 UTF-8 BOM | `app/modules/intake/csv_reader.py` | `tests/test_intake.py::test_bom_segment_headers_and_extra_columns` |
| TRAP-03 | `sample_data/field/stores.csv` 的 088 门店名跨行 | 人为拆成两条 | 保存源物理行区间 | `app/modules/intake/csv_reader.py` | `tests/test_intake.py::test_encoded_archive_preserves_multiline_chinese_source` |
| TRAP-04 | `sample_data/field/slots.csv` 重复表头 | 误抽不存在的柜位 | 分段表头独立计数 | `app/modules/intake/csv_reader.py` | `tests/test_intake.py::test_bom_segment_headers_and_extra_columns` |
| TRAP-05 | `sample_data/field/slots.csv` 抄录多列 | 字段错位 | 列数冲突留原值隔离 | `app/modules/intake/csv_reader.py` | `tests/test_intake.py::test_bom_segment_headers_and_extra_columns` |
| TRAP-06 | `sample_data/field/observations.csv` 包含全角带逗号价签 | 比较失败或金额误读 | 格式检查后转整数分 | `app/modules/intake/values.py` | `tests/test_photos.py::test_alias_and_fullwidth_price_reconcile_to_integer_evidence` |
| TRAP-08 | `sample_data/field/stores.csv` 的 `001` | 数字化丢零 | 字符串门店、柜位键 | `app/modules/intake/records.py` | `tests/test_intake.py::test_store_codes_keep_leading_zero` |
| TRAP-11 | `sample_data/field/visits.csv` 同次 `V002` 有 Z 和门店本地时间 | 当成双次或冲突 | UTC 后归并重复行 | `app/modules/intake/clock.py` | `tests/test_intake.py::test_offsets_and_naive_store_timezone_resolve_to_same_visit` |
| TRAP-12 | `sample_data/field/visits.csv` 的纽约回拨时刻 | 随机选一个偏移 | 缺显式偏移的重叠时刻隔离 | `app/modules/intake/clock.py` | `tests/test_intake.py::test_dst_ambiguity_gap_and_explicit_offsets` |
| TRAP-13 | `sample_data/field/visits.csv` 的较早巡检晚到 | 末行覆盖当前到访 | 按发生时间选定，保留被替代源行 | `app/modules/plans/windows.py` | `tests/test_plans.py::test_older_visit_is_retained_but_not_selected_when_input_is_out_of_order` |
| TRAP-14 | `sample_data/field/plans.csv` 测试副本追加冲突编号 `NEW-212` | 选第一条、最后一条或退回旧版 | 命中冲突窗口时阻断 | `app/modules/plans/windows.py` | `tests/test_plans.py::test_conflicting_plan_id_never_silently_falls_back` |
| TRAP-16 | `sample_data/field/observations.csv` 测试副本追加半行 | 保存部分批次 | 报损坏且零快照，再修原件重跑 | `app/modules/intake/csv_reader.py` `app/modules/settlement/service.py` | `tests/test_settlement.py::test_parse_error_rolls_back_entire_batch_and_restoring_original_allows_retry` |
| TRAP-18 | `sample_data/field/visits.csv` 测试副本分别置空、空行和仅表头 | 都返回相同结果 | 分门别类记空态 | `app/modules/intake/csv_reader.py` | `tests/test_intake.py::test_table_rejects_unclosed_quote_and_distinguishes_empty_states` |
| TRAP-19 | `sample_data/field/observations.csv` 的 `001:A03` 同时少排面且错价 | 累加互斥罚额 | 最高额执行，保留两项证据，同分固定顺序 | `app/modules/penalties/rules.py` | `tests/test_penalties.py::test_highest_applicable_rule_wins_without_summing` `tests/test_penalties.py::test_equal_penalty_rules_use_stable_priority_not_mapping_order` |
| TRAP-20 | `sample_data/field/aliases.csv` 测试副本旧名构成环 | 无限追溯 | 报告环路径 | `app/modules/plans/aliases.py` | `tests/test_plans.py::test_alias_cycle_returns_route` |
| TRAP-22 | `sample_data/field/aliases.csv` 的陈酿多级旧名 | 乱序时登记成不同商品 | 旧名解析到统一编号 | `app/modules/plans/aliases.py` | `tests/test_plans.py::test_alias_chain_is_order_independent` |
| TRAP-23 | `sample_data/field/visits.csv` 和 `plans.csv` 在同一秒切版 | 同秒命中两版 | 左闭右开只用新版 | `app/modules/plans/windows.py` | `tests/test_plans.py::test_exact_rollover_takes_new_plan_only` |
| TRAP-24 | `sample_data/field/campaign.json` 的 5、3、2 权重分 18 柜 | 独立舍入失总额 | 最大余数分到 9、5、4 | `app/modules/coverage/sampling.py` | `tests/test_coverage.py::test_weighted_quotas_allocate_remainder_without_losing_a_slot` |
| TRAP-25 | `sample_data/field/plans.csv` 的两版节日陈列叠期 | 挑一版直接开罚 | 单柜隔离并保留两份原件 | `app/modules/plans/windows.py` | `tests/test_plans.py::test_overlap_is_isolated_with_both_plan_versions` |
| TRAP-26 | `sample_data/field/` 的 `002:A03` 异议测试以旧版本重提 | 覆盖已受理 | 版本锁加唯一请求键 | `app/modules/appeals/transitions.py` `app/store.py` | `tests/test_appeals.py::test_stale_appeal_version_cannot_override_current_stage` |
| TRAP-27 | `sample_data/field/photos/P002.png` 与 `P010.png` 字节相同且绑不同柜位 | 文件名不同便放行 | 实际 SHA-256 验两侧绑定 | `app/modules/photos/fingerprints.py` `app/modules/photos/bindings.py` | `tests/test_photos.py::test_different_file_names_with_equal_bytes_flag_both_bindings` |
| TRAP-28 | `sample_data/field/` 的 `001:A04` 缺图、`002:A03` 已完成清点但缺品 | 都当缺品开罚，或都搁置 | 前者待证且 0 元，后者罚 150 元 | `app/modules/coverage/service.py` `app/modules/penalties/rules.py` | `tests/test_penalties.py::test_missing_photo_and_missing_sku_have_distinct_amounts` |
| TRAP-29 | `sample_data/field/rules.csv` 的 11 月 10 日生效点，巡检跨点 | 整批套旧规（漏收生效后缺口）或整批套新规（改价生效前巡店） | 逐柜按巡检时点选版本计价 | `app/modules/penalties/catalog.py` `app/modules/penalties/service.py` | `tests/test_rule_versions.py::test_each_slot_is_priced_with_the_rule_effective_at_its_visit` |
| TRAP-30 | 规则窗口叠期或留空 | 静默取一版开罚 | 单柜隔离并列出候选版本 | `app/modules/penalties/catalog.py` | `tests/test_rule_versions.py::test_overlapping_rule_windows_isolate_the_slot_instead_of_pricing` `tests/test_rule_versions.py::test_gap_in_rule_windows_isolates_the_slot_as_rule_missing` |
| TRAP-31 | 同一规则版本重跑同一档件 | 追加第二柜行或按新时长改价 | 命中同一 `run_id`，原样复用批次 | `app/store.py` `app/modules/settlement/service.py` | `tests/test_batches.py::test_rerun_under_same_rules_neither_adds_a_slot_nor_reprices` |
| TRAP-32 | 生成中途解析失败 | 写半批并覆盖已交财务的批次 | 零快照、记录失败、现存批次保持 ACTIVE | `app/modules/settlement/service.py` `app/store.py` | `tests/test_batches.py::test_failed_generation_leaves_the_published_batch_untouched` |
| TRAP-33 | 新批次取代旧批次或回滚 | 删除旧批或复活已回滚批次 | 批次只改状态、历史只追加，回滚后重跑返回 409 | `app/store.py` `app/modules/settlement/handler.py` | `tests/test_batches.py::test_rollback_restores_the_prior_batch_and_keeps_the_failed_one_queryable` `tests/test_batches.py::test_rolled_back_batch_cannot_be_silently_republished` |

## 对账

- 抽样目标 18＝17 实际抽中＋1 名册缺额。抽中 17＝8 合规＋3 违规＋3 待证＋3 隔离。
- 原罚额 31 500 分＝15 000 缺品＋5 500 排面不足＋11 000 错品。拆到规则版本：`2026.11-r1` 20 500 分（15 000＋5 500），`2026.11-r2` 11 000 分（错品）。若整批套用旧规会得 29 500 分、漏收生效后缺口；若整批套用新规会把生效前柜位改价。
- 七档原件均按源行号对账。清点表 50＝40 采信＋10 隔离；两条同柜矛盾行有各自来源。
- 异议记录逐柜追加，最终净额从原罚额减去确认的减免。对账支持同计数但错行、元分混用和重复事件等反例。
