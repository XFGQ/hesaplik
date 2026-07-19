import os

from dotenv import load_dotenv

load_dotenv()

import asyncpg
from dotenv import load_dotenv

load_dotenv()
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text

DSN = os.getenv("TEST_DSN", "postgresql+asyncpg://hesaplik:hesaplik@localhost:5432/hesaplik_test")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    eng = create_async_engine(DSN, echo=False)
    with open("db/schema.sql", encoding="utf-8") as f:
        ddl = f.read()
    raw = await asyncpg.connect(DSN.replace("postgresql+asyncpg://", "postgresql://"))
    await raw.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await raw.execute(ddl)
    await raw.close()
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def session(engine):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
        for t in ("audit_log", "raw_messages", "archived_transactions",
                  "transaction_lines", "transactions",
                  "price_history", "product_aliases", "products",
                  "person_aliases", "persons"):
            await s.execute(text(f"TRUNCATE {t} RESTART IDENTITY CASCADE"))
        await s.commit()
