import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import inspect, text
from scripts.seed_demo import seed
from src.config.database import Base, engine
from src.config.settings import SEED_DEMO
from src.domain.spec import SPEC
from src.middlewares import errors, headers, rate
from src.routes import distribution, identity, registry, runs, withdrawals
from src.utils.wakeup import broker

log = logging.getLogger(__name__)


def migrate():
    Base.metadata.create_all(engine)
    # 002：既有 runs 表补 revision 列；withdrawals 为新建表，由 create_all 处理。
    if 'revision' not in {column['name'] for column in inspect(engine).get_columns('runs')}:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE runs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1'))


@asynccontextmanager
async def lifespan(_app):
    migrate()
    if SEED_DEMO:
        try:
            log.info('Seeded %s', seed())
        except Exception:
            log.exception('Demo seed failed')
    yield
    engine.dispose()


app = FastAPI(title=SPEC['title'], lifespan=lifespan)
errors.install(app)
headers.install(app)
rate.install(app)
for module in (identity, registry, runs, withdrawals):
    app.include_router(module.router)
if SPEC.get('scheduled'):
    app.include_router(distribution.router)


@app.get('/healthz', include_in_schema=False)
def health():
    with engine.connect() as connection:
        connection.execute(text('SELECT 1'))
    broker.ping()
    return {'status': 'ok'}
