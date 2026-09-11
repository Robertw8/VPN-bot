from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def database(url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(url, pool_pre_ping=True, echo=False)
    return async_sessionmaker(engine, expire_on_commit=False)
