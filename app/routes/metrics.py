from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from datetime import datetime, timedelta
from typing import Optional
import json

from ..database import ContainerMetric, DockerHost
from ..auth import get_current_user, get_db

router = APIRouter(prefix="/metrics", tags=["Metrics"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/{host_name}/{container_id}", response_class=HTMLResponse)
async def get_metrics_page(
    request: Request,
    host_name: str,
    container_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Show metrics chart page for a container"""
    return templates.TemplateResponse("partials/metrics_modal.html", {
        "request": request,
        "host_name": host_name,
        "container_id": container_id[:12]
    })


@router.get("/{host_name}/{container_id}/data")
async def get_metrics_data(
    host_name: str,
    container_id: str,
    period: str = Query("24h", regex="^(1h|6h|24h|7d)$"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get historical metrics data for Chart.js"""
    # Calculate time range
    now = datetime.utcnow()
    if period == "1h":
        since = now - timedelta(hours=1)
    elif period == "6h":
        since = now - timedelta(hours=6)
    elif period == "24h":
        since = now - timedelta(hours=24)
    else:  # 7d
        since = now - timedelta(days=7)
    
    # Query metrics
    result = await db.execute(
        select(ContainerMetric)
        .where(
            ContainerMetric.container_id.like(f"{container_id[:12]}%"),
            ContainerMetric.host_name == host_name,
            ContainerMetric.timestamp >= since
        )
        .order_by(ContainerMetric.timestamp.asc())
    )
    metrics = result.scalars().all()
    
    # Format for Chart.js
    labels = []
    cpu_data = []
    mem_data = []
    
    for m in metrics:
        labels.append(m.timestamp.strftime("%H:%M"))
        cpu_data.append(round(m.cpu_percent, 2))
        mem_data.append(round(m.mem_percent, 2))
    
    return {
        "labels": labels,
        "cpu": cpu_data,
        "memory": mem_data,
        "count": len(metrics)
    }


@router.post("/cleanup")
async def cleanup_old_metrics(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove metrics older than 7 days"""
    cutoff = datetime.utcnow() - timedelta(days=7)
    
    result = await db.execute(
        delete(ContainerMetric).where(ContainerMetric.timestamp < cutoff)
    )
    await db.commit()
    
    return {"deleted": result.rowcount}
