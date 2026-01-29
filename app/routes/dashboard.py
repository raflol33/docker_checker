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
    
    # Validations...
    # Return empty list initially to allow user to select host.
    # The template should handle empty state gracefully.
    
    all_containers = []
    errors = []
    
    # loop = asyncio.get_running_loop()
    
    # for host in hosts:
    #     try:
    #         containers = await DockerService.list_containers(host, loop)
    #         all_containers.extend(containers)
    #     except Exception as e:
    #         errors.append(f"Ошибка подключения к {host.name}: {str(e)}")
            
    return templates.TemplateResponse("index.html", {
        "request": request, 
        "user": user, 
        "hosts": hosts, 
        "containers": all_containers,
        "errors": errors
    })

@router.get("/containers/list", response_class=HTMLResponse)
async def list_containers_filtered(
    request: Request,
    host_name: Optional[str] = None,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    loop = asyncio.get_running_loop()
    containers = []
    
    if host_name:
        # Fetch for single host
        result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
        host = result.scalar_one_or_none()
        if host:
            try:
                containers = await DockerService.list_containers(host, loop)
            except Exception:
                pass # Return empty or handle error
    else:
        # Fetch all
        result = await db.execute(select(DockerHost))
        hosts = result.scalars().all()
        for host in hosts:
            try:
                c_list = await DockerService.list_containers(host, loop)
                containers.extend(c_list)
            except Exception:
                pass
                
    return templates.TemplateResponse("partials/container_rows.html", {
        "request": request,
        "containers": containers
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

@router.post("/containers/{host_name}/{container_id}/stop")
async def stop_container(
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
        await DockerService.stop_container(host, container_id, loop)
        return {"status": "stopped", "id": container_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/containers/{host_name}/{container_id}/start")
async def start_container(
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
        await DockerService.start_container(host, container_id, loop)
        return {"status": "started", "id": container_id}
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

from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/ws/logs/{host_name}/{container_id}")
async def websocket_logs(
    websocket: WebSocket,
    host_name: str,
    container_id: str,
    tail: str = "100", # default tail for ws
    db: AsyncSession = Depends(get_db) 
):
    await websocket.accept()
    
    # We need to get host inside the socket handler (or pass params)
    # Depends(get_db) works in websocket? Yes.
    
    try:
        result = await db.execute(select(DockerHost).where(DockerHost.name == host_name))
        host = result.scalar_one_or_none()
        
        if not host:
            await websocket.send_text("Error: Host not found")
            await websocket.close()
            return

        loop = asyncio.get_running_loop()
        
        # Stream logs
        async for line in DockerService.stream_logs(host, container_id, tail, loop):
            try:
                await websocket.send_text(line)
            except WebSocketDisconnect:
                break
            except Exception:
                break
                
    except Exception as e:
        try:
            await websocket.send_text(f"Connection error: {str(e)}")
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass

from fastapi.responses import RedirectResponse
