# 保存可重放的复核批次

首版没有真人复核记录，因此本目录不包含已确认样本。不要直接复制下面的示例并冒充真人审阅。

完成实际审阅后，用 `YYYY-MM-DD_姓名或批次.json` 保存决定（文件名排序即重放顺序）：

```json
{
  "schema_version": 1,
  "records": [
    {
      "sample_id": "syn-001",
      "expected_revision": 1,
      "actor": "填写实际复核者",
      "reason": "填写实际核对结果",
      "human": true,
      "review_status": "verified"
    }
  ]
}
```

需要修订时可加 `summary`、`category`、`tag_names`。`human` 记录操作者声明，不是身份认证。执行：

```powershell
python -m loomdb apply-reviews data/reviews/你的批次.json
python -m loomdb check
python -m loomdb export
python -m loomdb evaluate --output exports/retrieval_eval.json
python scripts/verify_delivery.py
```

批次中任一决定冲突则整批回滚；已成功执行的同一请求再次重放会跳过，也不会覆盖后来的人工作业。请求哈希按 JSON 内容计算，与空格和文件名无关。新增批次和发布快照一起提交，保留原批次文件。
