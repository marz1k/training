"""
Робота з БД через async SQLAlchemy.

Один код працює і з SQLite (локально), і з Postgres (на хостингу) —
залежно від змінної оточення DATABASE_URL.

  локально:  DATABASE_URL не заданий -> sqlite+aiosqlite:///workouts.db
  прод:      DATABASE_URL=postgresql://user:pass@host/db
             (драйвер asyncpg підставляється автоматично)
"""
import os
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, select, delete, func, TypeDecorator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(TypeDecorator):
    """Гарантує aware-UTC datetime на читанні (SQLite повертає naive, PG — aware)."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def _normalize_db_url(url: str | None) -> str:
    if not url:
        return "sqlite+aiosqlite:///workouts.db"
    # Neon/Supabase/Railway часто дають префікс postgres:// або postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


DATABASE_URL = _normalize_db_url(os.getenv("DATABASE_URL"))

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    exercise: Mapped[str] = mapped_column(String(120), index=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    sets: Mapped[int] = mapped_column(Integer)
    total_reps: Mapped[int] = mapped_column(Integer)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    est_1rm: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(timezone.utc), index=True
    )


class Reminder(Base):
    __tablename__ = "reminders"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Integer, default=1)  # 0/1 для сумісності SQLite/PG
    threshold_days: Mapped[int] = mapped_column(Integer, default=3)
    last_reminded_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def add_entry(user_id: int, parsed) -> Entry:
    async with Session() as s:
        entry = Entry(
            user_id=user_id,
            exercise=parsed.exercise,
            weight=parsed.weight,
            sets=parsed.sets,
            total_reps=parsed.total_reps,
            volume=parsed.volume,
            est_1rm=parsed.est_1rm,
            raw=parsed.raw,
            created_at=parsed.created_at or datetime.now(timezone.utc),
        )
        s.add(entry)
        await s.commit()
        await s.refresh(entry)
        return entry


async def entries_since(user_id: int, since: datetime) -> list[Entry]:
    async with Session() as s:
        res = await s.execute(
            select(Entry)
            .where(Entry.user_id == user_id, Entry.created_at >= since)
            .order_by(Entry.created_at)
        )
        return list(res.scalars())


async def recent_entries(user_id: int, limit: int = 15) -> list[Entry]:
    async with Session() as s:
        res = await s.execute(
            select(Entry)
            .where(Entry.user_id == user_id)
            .order_by(Entry.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars())


async def exercise_history(user_id: int, exercise: str, limit: int = 30) -> list[Entry]:
    async with Session() as s:
        res = await s.execute(
            select(Entry)
            .where(Entry.user_id == user_id, Entry.exercise.like(f"%{exercise.lower()}%"))
            .order_by(Entry.created_at.desc())
            .limit(limit)
        )
        return list(res.scalars())


async def exercise_series(user_id: int, exercise: str) -> list[Entry]:
    """Повна історія вправи в хронологічному порядку (для графіка)."""
    async with Session() as s:
        res = await s.execute(
            select(Entry)
            .where(Entry.user_id == user_id, Entry.exercise.like(f"%{exercise.lower()}%"))
            .order_by(Entry.created_at)
        )
        return list(res.scalars())


async def all_entries(user_id: int) -> list[Entry]:
    async with Session() as s:
        res = await s.execute(
            select(Entry).where(Entry.user_id == user_id).order_by(Entry.created_at)
        )
        return list(res.scalars())


async def delete_last(user_id: int) -> Entry | None:
    async with Session() as s:
        res = await s.execute(
            select(Entry)
            .where(Entry.user_id == user_id)
            .order_by(Entry.created_at.desc())
            .limit(1)
        )
        entry = res.scalar_one_or_none()
        if entry:
            await s.delete(entry)
            await s.commit()
        return entry


async def distinct_exercises(user_id: int) -> list[str]:
    async with Session() as s:
        res = await s.execute(
            select(Entry.exercise).where(Entry.user_id == user_id).distinct()
        )
        return sorted(res.scalars())


# ---- нагадування ----

async def all_user_ids() -> list[int]:
    async with Session() as s:
        res = await s.execute(select(Entry.user_id).distinct())
        return list(res.scalars())


async def last_entry(user_id: int) -> Entry | None:
    async with Session() as s:
        res = await s.execute(
            select(Entry)
            .where(Entry.user_id == user_id)
            .order_by(Entry.created_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()


async def get_reminder(user_id: int) -> Reminder:
    async with Session() as s:
        rem = await s.get(Reminder, user_id)
        if rem is None:
            rem = Reminder(user_id=user_id, enabled=1, threshold_days=3)
            s.add(rem)
            await s.commit()
            await s.refresh(rem)
        return rem


async def set_reminder(user_id: int, *, enabled: bool | None = None,
                       threshold_days: int | None = None,
                       mark_reminded: bool = False) -> Reminder:
    async with Session() as s:
        rem = await s.get(Reminder, user_id)
        if rem is None:
            rem = Reminder(user_id=user_id, enabled=1, threshold_days=3)
            s.add(rem)
        if enabled is not None:
            rem.enabled = 1 if enabled else 0
        if threshold_days is not None:
            rem.threshold_days = threshold_days
        if mark_reminded:
            rem.last_reminded_at = datetime.now(timezone.utc)
        await s.commit()
        await s.refresh(rem)
        return rem
