# 运行与数据结构

- FastAPI提供异步任务API，SQLite/SQLAlchemy/Alembic保存会话、任务、版本和人工确认。
- 本机Codex app-server通过stdio提供结构化结果；工具包原文按阶段装配，输出经过Pydantic校验。
- 原生HTML/CSS/JavaScript工作台使用任务轮询和SSE同步进度。
- 文档快照在data/deliverables，代码不可变版本在data/executions，检查副本和报告在data/verifications。
- 代码在独立目录生成，检查与采用需显式操作，不自动覆盖原项目、不推进管家阶段。
- 模型名与底层协议在用户界面中转成产品语言；服务器错误使用统一error结构。

API契约在本机运行服务的/api/docs查看。Pages静态展示源码在仓库site目录，纯静态资源不调用本机API。
