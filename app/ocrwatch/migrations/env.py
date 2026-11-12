from alembic import context

from ocrwatch.db import Base

conn = context.config.attributes["connection"]
context.configure(
    connection=conn,
    target_metadata=Base.metadata,
    render_as_batch=conn.dialect.name == "sqlite",
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
