# Dayseal（日封台）

收银流水按营业日归集，日结封账后锁定；长短款可查。

- 前端 http://127.0.0.1:5700
- API http://127.0.0.1:10700

## 启动

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 10700 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 5700
```

数据库位置可用 `DAYSEAL_DATA` 指定（默认仓库根目录下的 `data/`），Docker 镜像使用 `/data`。

## 封账原子性

封账由四类写入组成，全部落在同一个 `BEGIN IMMEDIATE` 事务里：

1. `close_batches` 批次头（`status='sealed'`）
2. `close_snapshots` 批明细汇总快照（笔数、系统合计、流水摘要 SHA-256）
3. `variance_rows` 长短款
4. `close_day_locks` 日锁定

全局只贯彻一种失败策略：**整笔回滚**。任一步失败（非法实盘、校验失败、磁盘/约束错误等），
上述四类写入都不留下；接口返回 500，`detail` 里带失败原因；流水保持可改；总览的
`close_batch_count` 只统计 `status='sealed'` 的批次，因此不会增加。

写入口（新增/修改/删除流水、封账预检）只认「日锁定 + 对应 sealed 批次同时存在」。
所以失败回滚后不会出现「对流水返回 409 却查不到 sealed 现行批」的情况。
数据库层另有部分唯一索引兜底：一个营业日最多一个 sealed 批次，一个批次最多一条快照/长短款。

### 故障注入

`DAYSEAL_FAULT_INJECT` 可在指定步骤后抛错，用于验证回滚：

| 取值 | 失败点 |
| --- | --- |
| `after_batch` | 写入批次头之后 |
| `after_snapshot` | 写入快照之后 |
| `after_variance` | 写入长短款之后 |
| `after_lock` | 写入日锁定之后 |
| `before_commit` | 提交之前 |

示例：`DAYSEAL_FAULT_INJECT=after_lock uvicorn app.main:app ...`

### 升级旧库

旧版本封账只写批次头与长短款，没有快照和日锁定。启动时 `seed_if_empty()` 会：

- 为每个已封营业日回填一条日锁定（取该日最新的 sealed 批次）
- 尝试建立「每个营业日最多一个 sealed 批次」的唯一索引

回填后历史已封日继续拒绝流水写入；重复启动不会重复插入。

### 完整性残留检查

`GET /api/close/integrity` 返回各表计数以及以下不一致项，正常应全为 0：

- `sealed_batches_without_lock` / `locks_without_sealed_batch`
- `sealed_batches_without_snapshot` / `sealed_batches_without_variance`
- `orphan_snapshots` / `orphan_variances`

### 测试

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests -q
```

测试覆盖：五个注入点各自回滚后四张表计数不变、无假 sealed、失败后流水仍可写且随后可成功封账、
非法实盘七种取值、并发双封只得一个 200 + 一个 409、失败后不存在「409 但无 sealed 批」。
