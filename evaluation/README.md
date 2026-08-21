# DocFlow AI 固定测试样例

公开仓库默认加载 `fixed-agent-samples-public-v1.json`。该文件包含 19 条全虚构样例，不对应
任何真实单位、项目、人员或业务数据；配套材料位于 `examples/demo-corpus`。

| 能力 | 数量 | 重点覆盖 |
| --- | ---: | --- |
| RAG 问答 | 9 | 日期、多数字事实、条件集合、列表、跨材料汇总和安全拒答 |
| 公文审核 | 5 | 结构、格式、事实一致性、测试手机号、误报对照组 |
| 公文撰写 | 5 | 请示、函、事实门禁、需求门禁和无依据事实拦截 |

## 使用公开样例

1. 在管理中心将 `examples/demo-corpus` 添加为数据源；
2. 完成解析、校验和 Publication 发布；
3. 打开“Agent 固定评测”，先运行本地模式，再按需运行完整云模型链路；
4. 对问答结果同时检查事实覆盖、目标证据页覆盖、引用命中和拒答正确性。

公开样例以文件 SHA-256 作为稳定定位信息。若修改演示语料内容，需要同步更新评测文件中的哈希。

## 私有评测集

开发者可以在本机保留以下文件：

```text
evaluation/fixed-agent-samples-v1.json
evaluation/fixed-agent-samples-expansion-v1.json
```

这两类文件可能与具体 Publication、原始文件名、页面标识或业务事实绑定，已被 `.gitignore`
排除。服务会优先选择与当前 `index_generation_id` 精确匹配的私有评测集；没有匹配项时回退到
公开脱敏集。公开仓库的自动化测试不依赖私有文件。

## 执行模式

- 知识问答：`LOCAL_RETRIEVAL` 或 `FULL_QA`；
- 公文审核：`LOCAL_RULES` 或 `FULL_REVIEW`；
- 公文撰写：`REQUIREMENT_GATE` 或 `FULL_DRAFT`。

本地模式不调用云模型；完整模式会在执行前确认，并记录工作流、模型签名、Token、费用和逐条结果。
对应接口：

```text
GET  /api/v1/agent-evaluations/catalog
GET  /api/v1/agent-evaluations/runs
POST /api/v1/agent-evaluations/runs
```

建议持续观察：

- QA：Recall@5、事实覆盖率、证据页覆盖率、引用正确率、拒答正确率；
- 审核：问题召回率、重复率、严重问题误报率、定位准确率；
- 撰写：必需事实覆盖率、无依据事实率、引用有效率、事实门禁通过率；
- 运行：总耗时、云调用次数、Token、估算成本和降级次数。
