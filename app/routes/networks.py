from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import asyncio

from ..database import DockerHost
from ..auth import get_current_user, get_db
from ..docker_service import DockerService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/networks/{host_name}", response_class=HTMLResponse)
async def list_networks(
    request: Request,
    host_name: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all networks for a host"""
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    
    if not host:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": f"Host {host_name} not found"
        })
    
    loop = asyncio.get_running_loop()
    networks = await DockerService.list_networks(host, loop)
    
    return templates.TemplateResponse("partials/networks_modal.html", {
        "request": request,
        "host_name": host_name,
        "networks": networks
    })


@router.post("/networks/{host_name}/prune")
async def prune_networks(
    host_name: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove unused networks"""
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    
    if not host:
        return {"error": "Host not found"}
    
    loop = asyncio.get_running_loop()
    deleted = await DockerService.prune_networks(host, loop)
    
    return {"deleted": deleted}


@router.get("/volumes/{host_name}", response_class=HTMLResponse)
async def list_volumes(
    request: Request,
    host_name: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all volumes for a host"""
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    
    if not host:
        return templates.TemplateResponse("partials/error.html", {
            "request": request,
            "error": f"Host {host_name} not found"
        })
    
    loop = asyncio.get_running_loop()
    volumes = await DockerService.list_volumes(host, loop)
    
    return templates.TemplateResponse("partials/volumes_modal.html", {
        "request": request,
        "host_name": host_name,
        "volumes": volumes
    })


@router.post("/volumes/{host_name}/prune")
async def prune_volumes(
    host_name: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Remove unused volumes"""
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    
    if not host:
        return {"error": "Host not found"}
    
    loop = asyncio.get_running_loop()
    deleted, space = await DockerService.prune_volumes(host, loop)
    
    return {"deleted": deleted, "space_reclaimed": space}
