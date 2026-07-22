# Preference Learning

阶段一：少于 30 条有效决策时，使用透明规则权重。

阶段二：达到 30 条以上有效 `use/reject` 决策后，切换到轻量 Logistic Regression。

命令：

```bash
anime preference train
anime preference explain <shot_id>
anime preference reset
anime preference rebuild
```
