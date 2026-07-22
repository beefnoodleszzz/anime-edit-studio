# Material Rights

状态规则：

- `approved`：允许进入终版导出
- `review`：允许预览，不允许终版导出
- `blocked`：不允许进入终版导出

常用命令：

```bash
anime source-register <asset_id> --source-url <url> --license <license> --commercial true --status approved
anime source-audit --project <project_id>
anime rights-report <project_id>
```
