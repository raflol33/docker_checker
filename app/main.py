from fastapi import FastAPI, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import HTTPException
from contextlib import asynccontextmanager
from .database import init_db
from .auth import ensure_admin_user

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    await ensure_admin_user()
    yield
    # Shutdown

app = FastAPI(title="Docker Manager", lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Exception handler for 401 -> Redirect to login for browser
@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def unauthorized_exception_handler(request: Request, exc: HTTPException):
    # Check if request accepts html
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse(url="/login")
    # Otherwise return normal JSON error
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": exc.detail})

from .routes import auth, dashboard

app.include_router(dashboard.router)
app.include_router(auth.router)
