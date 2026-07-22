# Material Rights

`source_records` 只用于**记录来源**(素材从哪来、作品名、集数、BD/Raw/切片站来源),
方便日后回源更高画质版本。**不再作为导出门禁**——渲染不会因权利状态被拦截。

`status` 字段(`approved` / `review` / `blocked`)现在只是来源标注,供人工参考,不影响导出。

正式渲染会按 `shot.id` 从库里把 src 回源到本地母版(`assets.path`),目的是让成片
用最高画质原片而非代理文件。

常用命令(纯记录/查看,不阻断):

```bash
anime source-register <asset_id> --source-url <url> --license <license> --commercial true --status approved
anime source-audit --project <project_id>
anime rights-report <project_id>
```
