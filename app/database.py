from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, Text, Boolean, ForeignKey, DateTime
from datetime import datetime
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

class SSHKey(Base):
    __tablename__ = "ssh_keys"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    private_key: Mapped[str] = mapped_column(Text)  # Encrypted in production
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relationship to hosts using this key
    hosts: Mapped[list["DockerHost"]] = relationship("DockerHost", back_populates="ssh_key")

class Environment(Base):
    __tablename__ = "environments"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    
    # Relationship to hosts
    hosts: Mapped[list["DockerHost"]] = relationship("DockerHost", back_populates="environment")

class DockerHost(Base):
    __tablename__ = "docker_hosts"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(20)) # 'local' or 'ssh'
    
    # Environment relationship
    environment_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("environments.id"), nullable=True)
    environment: Mapped["Environment | None"] = relationship("Environment", back_populates="hosts")
    
    # Connection details
    ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssh_user: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    # SSH Key relationship (preferred over path)
    ssh_key_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("ssh_keys.id"), nullable=True)
    ssh_key: Mapped["SSHKey | None"] = relationship("SSHKey", back_populates="hosts")
    ssh_key_password: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Password to decrypt key
    
    # Legacy fields (kept for backward compatibility)
    ssh_key_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_password: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ContainerMetric(Base):
    """Store historical container metrics for charts"""
    __tablename__ = "container_metrics"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    container_id: Mapped[str] = mapped_column(String(64), index=True)
    host_name: Mapped[str] = mapped_column(String(100), index=True)
    cpu_percent: Mapped[float] = mapped_column(default=0.0)
    mem_percent: Mapped[float] = mapped_column(default=0.0)
    mem_usage: Mapped[int] = mapped_column(Integer, default=0)  # bytes
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AlertConfig(Base):
    """Alert notification configuration"""
    __tablename__ = "alert_configs"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Telegram settings
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    
    # Email settings
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    email_smtp_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    email_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Webhook settings
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AlertHistory(Base):
    """History of sent alerts"""
    __tablename__ = "alert_history"
    
    id: Mapped[int] = mapped_column(primary_key=True)
    container_id: Mapped[str] = mapped_column(String(64))
    container_name: Mapped[str] = mapped_column(String(255))
    host_name: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(50))  # 'down', 'up', 'unhealthy'
    message: Mapped[str] = mapped_column(Text)
    sent_via: Mapped[str] = mapped_column(String(50))  # 'telegram', 'email', 'webhook'
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
