# Batch queries

Expose one batched tool instead of N single-item tools; dedupe repeated keys so you fetch each
unique item once.

```python
from tokendog_mcp import run_batch
issues = run_batch(ids, fetch_one=get_issue)   # get_issue called once per unique id
```
