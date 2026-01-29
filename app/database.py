from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, Text, Boolean
import os


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data.db")

# Ensure the database allows generic types if needed, though for SQLite strict types are often fine.

engine = create_async_engine(DATABASE_URL, connect_args={"check_same_thread": False})
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

class DockerHost(Base):
    __tablename__ = "docker_hosts"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(20)) # 'local' or 'ssh'
    
    # Connection details
    ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssh_user: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Storing password or key path. For security, passwords should be encrypted, 
    # but for this MVP we might store plain or assume trusted env. 
    # Let's store simple string for now.
    ssh_key_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
