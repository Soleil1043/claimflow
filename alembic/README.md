# alembic/ —— 数据库迁移

SQLAlchemy 2.0 async 双后端（dev=aiosqlite / prod=asyncpg）共用模型
（`services/db/models.py`），迁移经 alembic 管理。

## 使用

```bash
uv run alembic upgrade head      # 升级
uv run alembic downgrade -1      # 回滚一步
uv run alembic revision --autogenerate -m "xxx"   # 生成新迁移
```

## 约定

- 每张业务表的变更一个迁移文件（`versions/`），不合并多表；
- dev 日常建表走 `create_all`（seed），迁移链面向 prod PostgreSQL；
- 旧迁移链在 SQLite 方言不保证可执行（b5f9c3d7e2a4 约束无 batch）——新迁移单测
  走"空库 stamp 上一版 → upgrade → downgrade"口径（T132 先例）；
- LangGraph checkpoint / Store 表由 PostgreSQLSaver/Store 自管，不在此建模（D006）。

当前表结构：核赔 4 张 + 客服 2 张 + mock/支撑 5 张，共 11 张（见
`services/db/models.py` 文件头）。
