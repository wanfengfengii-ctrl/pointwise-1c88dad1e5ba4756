# 澄浦监测日批仓

白班采集员把东区泵站和西区排水站的原日报录入 SQLite 源仓。日结员试算并封存正式更正。源仓离线后，夜班复核员将指定冻结件与自己仓的日账核对并接纳。交班负责人按日期核对当前仓账，次班按首执领取当时实际发布的日账。

## 运行

需要 Python 3.11 标准库和控制台物化时提供的 `check_project.py`。从项目根目录执行。

```sh
python3 -B check_project.py
python3 -B -m daily.warehouse demo samples/observations.json samples/correction.json
python3 -B -m daily.warehouse replacement-demo samples/observations.json samples/replacement.json
```

`check_project.py` 运行全部从 V1 到原位替换的回归，并对独立源仓与复核仓做联合核验（断源重开、旧日兼容、拒收、提交失败与同号复领）。`replacement-demo` 用实办结果读取旧首执、拒绝后的现账及重开后可领的新账。

演示在临时目录建立独立的源仓和复核仓，使用固定日期 2026-11-16。东区原报 3000 mL 和 7 L，合计 10 L。西区一条缺读、一条有效零值。源端撤回一次试算，正式加 3 L，并观察东区是否等于 20 L，结果为假。源仓关闭后，同一冻结件在东区已有 12 L 的仓入账为 15 L，在已有 17 L 的仓拒收。演示重开两个复核仓，检查实时账及首执后输出实测结果。

## Python 接口

各仓独立持有 SQLite 文件。调用者负责关闭 `open_warehouse` 返回的连接。公开写入入口管理其事务，应在连接没有外部事务时调用。内部初始化会在同一个事务中复用测点登记。

| 调用 | 输入和结果 |
| --- | --- |
| `daily.warehouse.open_warehouse(path)` | 打开仓连接并启用外键。不自动写入种子。 |
| `daily.warehouse.initialize_warehouse(db, stations=())` | 补齐表结构，可传入 `Station` 序列。相同登记重用，冲突保留原状态。 |
| `intake.stations.register(db, station_id, region, units)` | 登记测点并返回 `Station`。可用单位为 `L` 和 `mL`。无效或冲突为 `RegistrationError`。 |
| `intake.batches.ingest(db, batch_id, source_id, rows)` | 每行含 `reading_id`、`station_id`、`day`、`unit`、`value`。批内读数号唯一。相同源标识与原始有序行重投返回原批。同号异内容或任一无效行引发 `IngestError`。 |
| `daily.supplements.supplement(db, supplement_id, day, region, litres)` | 具名本地补录，增减使用有符号整数升。同号同参重用。同号异参或未知日区引发 `SupplementError`。 |
| `issue.work_batch.correct(db, batch_id, day, operations)` | 建立或继续一批更正，返回持久的 `WorkBatch`。批名固定起始日账的日期、日区范围与量。非法操作或继续编辑已封存批引发 `CorrectionError`。 |
| `issue.freeze.freeze(db, batch_id)` | 封存并返回 `FrozenPackage`，用 `issue.package.encode_package` 得到交换字节。已发行的工作批可重复领取，未知批或未结束的试算引发 `FreezeError`。 |
| `receive.admission.accept(db, acceptance_id, wire)` | 接纳指定冻结字节并返回 `Receipt`。重投沿用接纳号。同号同件直接返回原首执。无效来件、同号异件、未知日区或提交错误引发 `AdmissionError`，核对变化为其子类 `IncompatiblePackage`，实际提交失败为其子类 `CommitError`。普通接纳拒收替换件。 |
| `issue.freeze.freeze_replacement(db, original_batch_id, replacement_batch_id)` | 将同日工作批封定为某普通冻结件的替换件并返回交换字节。同绑重领、重开返回原字节，换绑、缺批、异日、原批无普通件、试算未结束或与普通发行混用引发 `FreezeError`。 |
| `receive.admission.accept_replacement(db, acceptance_id, target_receipt_number, wire)` | 把替换件放回指定普通首执的入账位置并返回新 `Receipt`。同一普通首执最多成功替换一次，替换首执不可作目标，普通接纳与替换接纳共用接纳号。核对翻转引发 `IncompatiblePackage`，提交失败引发 `CommitError`，其余无资格、缺证、坏件引发 `AdmissionError`。 |
| `audit.ledger.read(db, day=...)` 或 `audit.ledger.read(db, receipt_number=...)` | 读取 `AuditView`，包含按日区排序的 `regions` 与 `total_litres`。未建立仓账或未知首执返回 `None`。旧首执视图始终固定。 |

日期使用 `YYYY-MM-DD`。读数的 `value` 为整数或明确的 `None`。整数 mL 必须能精确换成整数升。有效零量仍有出处并计作有效读数。缺读有独立出处，且仅增加缺读数。登记区在当日无贡献时按零参与工作账和接纳计算。当前账表列出有贡献的区。成功发布也可建立一个日期的日账。

`correct` 的 `operations` 是有序字典序列。`{"op": "add", "region": "东区", "litres": 3}` 表示加量，负数表示减量。`{"op": "check", "region": "东区", "name": "等于限量", "target": 20}` 保存当时的真假，同一有效工作批内核对名唯一。`{"op": "begin_trial"}` 开始试算，`{"op": "keep_trial"}` 原位保留其操作，`{"op": "withdraw_trial"}` 撤回整段。试算不嵌套，可跨 `correct` 调用继续。未闭合时可以继续工作，无法发行。正式工作账在 `WorkBatch.values` 中读取，源仓需要实际入账时也使用 `accept`。

复核仓按正式操作顺序在临时账上施加差量，逐点重算等值核对。全部日区相容后才暂存发布，账变、首执及固定核账视图在一次提交中公开。每个分区包含整数升、有效读数数目、缺读数目、原读出处、补录及接纳更正出处。总站合计按同份分区账计算。重开或继续补录后仍可凭首执领回原视图。
