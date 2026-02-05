import asyncio
import aiohttp
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

from .database import AlertConfig, AlertHistory, AsyncSessionLocal


class AlertService:
    """Service for sending notifications via Telegram, Email, Webhook"""
    
    @staticmethod
    async def get_config() -> Optional[AlertConfig]:
        """Get alert configuration from database"""
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(select(AlertConfig).limit(1))
            return result.scalar_one_or_none()
    
    @staticmethod
    async def send_telegram(token: str, chat_id: str, message: str) -> bool:
        """Send message via Telegram Bot API"""
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML"
                }) as response:
                    return response.status == 200
        except Exception as e:
            print(f"Telegram error: {e}")
            return False
    
    @staticmethod
    async def send_email(smtp_host: str, smtp_port: int, from_addr: str, 
                         to_addr: str, password: str, subject: str, message: str) -> bool:
        """Send email notification"""
        try:
            def _send():
                msg = MIMEMultipart()
                msg['From'] = from_addr
                msg['To'] = to_addr
                msg['Subject'] = subject
                msg.attach(MIMEText(message, 'html'))
                
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(from_addr, password)
                    server.send_message(msg)
            
            await asyncio.to_thread(_send)
            return True
        except Exception as e:
            print(f"Email error: {e}")
            return False
    
    @staticmethod
    async def send_webhook(url: str, payload: dict) -> bool:
        """Send webhook notification"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    return response.status < 400
        except Exception as e:
            print(f"Webhook error: {e}")
            return False
    
    @staticmethod
    async def send_alert(container_id: str, container_name: str, host_name: str, 
                         event_type: str, message: str):
        """Send alert via all configured channels"""
        config = await AlertService.get_config()
        if not config or not config.enabled:
            return
        
        sent_via = []
        
        # Telegram
        if config.telegram_enabled and config.telegram_token and config.telegram_chat_id:
            emoji = "🔴" if event_type == "down" else "🟢" if event_type == "up" else "⚠️"
            tg_message = f"{emoji} <b>Docker Alert</b>\n\n"
            tg_message += f"<b>Container:</b> {container_name}\n"
            tg_message += f"<b>Host:</b> {host_name}\n"
            tg_message += f"<b>Event:</b> {event_type.upper()}\n"
            tg_message += f"<b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            tg_message += message
            
            if await AlertService.send_telegram(config.telegram_token, config.telegram_chat_id, tg_message):
                sent_via.append("telegram")
        
        # Email
        if config.email_enabled and config.email_smtp_host and config.email_from and config.email_to:
            subject = f"🐳 Docker Alert: {container_name} {event_type.upper()}"
            email_body = f"""
            <h2>Docker Container Alert</h2>
            <p><strong>Container:</strong> {container_name}</p>
            <p><strong>Host:</strong> {host_name}</p>
            <p><strong>Event:</strong> {event_type.upper()}</p>
            <p><strong>Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            <hr>
            <p>{message}</p>
            """
            if await AlertService.send_email(
                config.email_smtp_host, config.email_smtp_port or 587,
                config.email_from, config.email_to, config.email_password or "",
                subject, email_body
            ):
                sent_via.append("email")
        
        # Webhook
        if config.webhook_enabled and config.webhook_url:
            payload = {
                "container_id": container_id,
                "container_name": container_name,
                "host_name": host_name,
                "event_type": event_type,
                "message": message,
                "timestamp": datetime.utcnow().isoformat()
            }
            if await AlertService.send_webhook(config.webhook_url, payload):
                sent_via.append("webhook")
        
        # Save to history
        if sent_via:
            async with AsyncSessionLocal() as db:
                history = AlertHistory(
                    container_id=container_id[:12],
                    container_name=container_name,
                    host_name=host_name,
                    event_type=event_type,
                    message=message,
                    sent_via=",".join(sent_via)
                )
                db.add(history)
                await db.commit()
