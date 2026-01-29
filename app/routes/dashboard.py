import asyncio
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from ..database import DockerHost, AsyncSessionLocal
from ..auth import get_current_user, get_db
from ..docker_service import DockerService
from typing import Optional

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # List all hosts
    result = await db.execute(select(DockerHost))
    hosts = result.scalars().all()
    
    # In a real async world, we might want to fetch containers for all hosts in parallel
    # For now, let's just pass hosts and let HTMX load containers lazy or load here.
    # To make "Unified table", we probably want to fetch them all here or render a skeleton.
    # Let's fetch them all here for simplicity of the first render.
    
    all_containers = []
    errors = []
    
    loop = asyncio.get_running_loop()
    
    for host in hosts:
        try:
            containers = await DockerService.list_containers(host, loop)
            all_containers.extend(containers)
        except Exception as e:
            errors.append(f"Ошибка подключения к {host.name}: {str(e)}")
            
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "user": user, 
        "hosts": hosts, 
        "containers": all_containers,
        "errors": errors
    })

@router.post("/hosts/add")
async def add_host(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    ip: str = Form(None),
    port: int = Form(None),
    ssh_user: str = Form(None),
    ssh_password: str = Form(None),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Determine if local (only one local host allowed usually, but logic allows multiple pointing to same?)
    # Validations...
    new_host = DockerHost(
        name=name,
        type=type,
        ip=ip,
        port=port,
        ssh_user=ssh_user,
        ssh_password=ssh_password,
        ssh_key_path="/root/.ssh/id_rsa" if type == 'ssh' and not ssh_password else None # simplified assumption for now
    )
    db.add(new_host)
    await db.commit()
    # Return updated host list or redirect
    return RedirectResponse("/", status_code=303) # 303 for See Other after generic POST

@router.post("/hosts/{host_id}/delete")
async def delete_host(host_id: int, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(DockerHost).where(DockerHost.id == host_id))
    await db.commit()
    return RedirectResponse("/", status_code=303)

@router.post("/containers/{host_name}/{container_id}/restart")
async def restart_container(
    host_name: str, 
    container_id: str, 
    user=Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Хост не найден")
        
    loop = asyncio.get_running_loop()
    try:
        await DockerService.restart_container(host, container_id, loop)
        return {"status": "restarted", "id": container_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/containers/{host_name}/{container_id}/logs")
async def get_logs(
    request: Request,
    host_name: str, 
    container_id: str, 
    tail: str = "1000", 
    since: Optional[str] = None,
    until: Optional[str] = None,
    search: Optional[str] = None,
    download: bool = False,
    user=Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Хост не найден")
        
    loop = asyncio.get_running_loop()
    logs = await DockerService.get_logs(host, container_id, tail, since, until, search, loop)
    
    if download:
        filename = f"logs_{container_id}.txt"
        return StreamingResponse(
            iter([logs]), 
            media_type="text/plain", 
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/plain; charset=utf-8"
            }
        )
    
    return templates.TemplateResponse("logs.html", {
        "request": request, 
        "logs": logs, 
        "container_id": container_id,
        "tail": tail,
        "since": since,
        "until": until,
        "search": search,
        "host_name": host_name
    })

@router.get("/images/{host_name}")
async def get_images(
    request: Request,
    host_name: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Хост не найден")
        
    loop = asyncio.get_running_loop()
    try:
        images = await DockerService.list_images(host, loop)
        return templates.TemplateResponse("images.html", {"request": request, "images": images, "host_name": host_name})
    except Exception as e:
         return templates.TemplateResponse("images.html", {"request": request, "images": [], "error": str(e), "host_name": host_name})

@router.delete("/images/{host_name}/{image_id}")
async def delete_image_route(
    host_name: str,
    image_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Хост не найден")
        
    loop = asyncio.get_running_loop()
    try:
        await DockerService.delete_image(host, image_id, loop)
        return {"status": "deleted", "id": image_id}
    except Exception as e:
        # Return 500 so HTMX can handle error? Or return error message in a snippet?
        # HTMX default behavior on error is nothing unless configured.
        # Let's return 200 with error header or just 500.
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/compose/action")
async def compose_action(
    host_name: str = Form(...), 
    path: str = Form(...), 
    action: str = Form(...), # up or down
    user=Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Хост не найден")
    
    loop = asyncio.get_running_loop()
    try:
        output = await DockerService.run_compose(host, path, action, loop)
        return {"status": "success", "output": output}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

from fastapi.responses import RedirectResponse
