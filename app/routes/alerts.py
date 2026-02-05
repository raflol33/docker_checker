from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from ..database import AlertConfig, AlertHistory
from ..auth import get_current_user, get_db
from ..alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["Alerts"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def alerts_settings_page(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Show alerts settings page"""
    result = await db.execute(select(AlertConfig).limit(1))
    config = result.scalar_one_or_none()
    
    # Get recent history
    history_result = await db.execute(
        select(AlertHistory).order_by(AlertHistory.timestamp.desc()).limit(20)
    )
    history = history_result.scalars().all()
    
    return templates.TemplateResponse("alerts.html", {
        "request": request,
        "user": user,
        "config": config,
        "history": history
    })


@router.post("/save")
async def save_alert_config(
    request: Request,
    enabled: bool = Form(False),
    telegram_enabled: bool = Form(False),
    telegram_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    email_enabled: bool = Form(False),
    email_smtp_host: str = Form(""),
    email_smtp_port: int = Form(587),
    email_from: str = Form(""),
    email_to: str = Form(""),
    email_password: str = Form(""),
    webhook_enabled: bool = Form(False),
    webhook_url: str = Form(""),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Save alert configuration"""
    result = await db.execute(select(AlertConfig).limit(1))
    config = result.scalar_one_or_none()
    
    if config:
        config.enabled = enabled
        config.telegram_enabled = telegram_enabled
        config.telegram_token = telegram_token or None
        config.telegram_chat_id = telegram_chat_id or None
        config.email_enabled = email_enabled
        config.email_smtp_host = email_smtp_host or None
        config.email_smtp_port = email_smtp_port
        config.email_from = email_from or None
        config.email_to = email_to or None
        config.email_password = email_password or None
        config.webhook_enabled = webhook_enabled
        config.webhook_url = webhook_url or None
    else:
        config = AlertConfig(
            enabled=enabled,
            telegram_enabled=telegram_enabled,
            telegram_token=telegram_token or None,
            telegram_chat_id=telegram_chat_id or None,
            email_enabled=email_enabled,
            email_smtp_host=email_smtp_host or None,
            email_smtp_port=email_smtp_port,
            email_from=email_from or None,
            email_to=email_to or None,
            email_password=email_password or None,
            webhook_enabled=webhook_enabled,
            webhook_url=webhook_url or None
        )
        db.add(config)
    
    await db.commit()
    return RedirectResponse("/alerts", status_code=303)


@router.post("/test")
async def test_alert(
    request: Request,
    channel: str = Form("telegram"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Send a test alert"""
    result = await db.execute(select(AlertConfig).limit(1))
    config = result.scalar_one_or_none()
    
    if not config:
        return {"success": False, "message": "No configuration found"}
    
    success = False
    
    if channel == "telegram" and config.telegram_token and config.telegram_chat_id:
        success = await AlertService.send_telegram(
            config.telegram_token,
            config.telegram_chat_id,
            "✅ <b>Test Alert</b>\n\nDocker Manager notification is working!"
        )
    elif channel == "email" and config.email_smtp_host:
        success = await AlertService.send_email(
            config.email_smtp_host,
            config.email_smtp_port or 587,
            config.email_from or "",
            config.email_to or "",
            config.email_password or "",
            "🐳 Docker Manager Test Alert",
            "<h2>Test Alert</h2><p>Docker Manager notification is working!</p>"
        )
    elif channel == "webhook" and config.webhook_url:
        success = await AlertService.send_webhook(
            config.webhook_url,
            {"type": "test", "message": "Docker Manager notification is working!"}
        )
    
    return {"success": success, "channel": channel}


@router.get("/history")
async def get_alert_history(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get alert history as JSON"""
    result = await db.execute(
        select(AlertHistory).order_by(AlertHistory.timestamp.desc()).limit(50)
    )
    history = result.scalars().all()
    
    return [{
        "id": h.id,
        "container_name": h.container_name,
        "host_name": h.host_name,
        "event_type": h.event_type,
        "sent_via": h.sent_via,
        "timestamp": h.timestamp.isoformat()
    } for h in history]
