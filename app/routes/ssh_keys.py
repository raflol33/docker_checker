from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet
import base64
import hashlib
import os

from ..database import SSHKey, DockerHost
from ..auth import get_current_user, get_db

router = APIRouter(prefix="/ssh-keys", tags=["SSH Keys"])
templates = Jinja2Templates(directory="app/templates")


def get_encryption_key(password: str) -> bytes:
    """Derive encryption key from password"""
    # Use SHA256 to create a 32-byte key from password
    key = hashlib.sha256(password.encode()).digest()
    return base64.urlsafe_b64encode(key)


def encrypt_private_key(private_key: str, password: str) -> str:
    """Encrypt private key with password"""
    fernet = Fernet(get_encryption_key(password))
    return fernet.encrypt(private_key.encode()).decode()


def decrypt_private_key(encrypted_key: str, password: str) -> str | None:
    """Decrypt private key with password"""
    try:
        fernet = Fernet(get_encryption_key(password))
        return fernet.decrypt(encrypted_key.encode()).decode()
    except:
        return None


@router.get("", response_class=HTMLResponse)
async def list_ssh_keys(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all SSH keys"""
    result = await db.execute(select(SSHKey).order_by(SSHKey.created_at.desc()))
    keys = result.scalars().all()
    
    return templates.TemplateResponse("ssh_keys.html", {
        "request": request,
        "user": user,
        "keys": keys,
        "generated_key": None
    })


@router.post("/add")
async def add_ssh_key(
    request: Request,
    name: str = Form(...),
    private_key: str = Form(...),
    encryption_password: str = Form(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Add a new SSH key (encrypted)"""
    # Extract public key from private key
    public_key = None
    try:
        private_key_obj = serialization.load_pem_private_key(
            private_key.encode(),
            password=None,
            backend=default_backend()
        )
        public_key_obj = private_key_obj.public_key()
        public_key = public_key_obj.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        ).decode()
    except Exception:
        pass
    
    # Encrypt the private key
    encrypted_private_key = encrypt_private_key(private_key, encryption_password)
    
    new_key = SSHKey(
        name=name,
        private_key=encrypted_private_key,
        public_key=public_key
    )
    db.add(new_key)
    await db.commit()
    
    return HTMLResponse(
        content="<script>window.location.href='/ssh-keys';</script>",
        status_code=200
    )


@router.post("/generate", response_class=HTMLResponse)
async def generate_ssh_key(
    request: Request,
    name: str = Form(...),
    encryption_password: str = Form(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate a new RSA key pair - shows private key ONCE"""
    # Generate RSA key
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
        backend=default_backend()
    )
    
    # Private key (plain text - shown once)
    private_key_plain = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    ).decode()
    
    # Public key
    public_key = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH
    ).decode()
    
    # Encrypt private key for storage
    encrypted_private_key = encrypt_private_key(private_key_plain, encryption_password)
    
    new_key = SSHKey(
        name=name,
        private_key=encrypted_private_key,
        public_key=public_key
    )
    db.add(new_key)
    await db.commit()
    
    # Return page with the generated key shown ONCE
    result = await db.execute(select(SSHKey).order_by(SSHKey.created_at.desc()))
    keys = result.scalars().all()
    
    return templates.TemplateResponse("ssh_keys.html", {
        "request": request,
        "user": user,
        "keys": keys,
        "generated_key": {
            "name": name,
            "private_key": private_key_plain,
            "public_key": public_key
        }
    })


@router.delete("/{key_id}")
async def delete_ssh_key(
    key_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Delete an SSH key"""
    result = await db.execute(select(SSHKey).where(SSHKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if key:
        hosts_result = await db.execute(
            select(DockerHost).where(DockerHost.ssh_key_id == key_id)
        )
        hosts_using = hosts_result.scalars().all()
        
        if hosts_using:
            return {"error": f"Key is used by {len(hosts_using)} host(s)"}
        
        await db.delete(key)
        await db.commit()
    
    return {"success": True}


@router.get("/{key_id}/public")
async def get_public_key(
    key_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get public key for copying"""
    result = await db.execute(select(SSHKey).where(SSHKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        return {"error": "Key not found"}
    
    return {"public_key": key.public_key or "No public key available"}


@router.post("/{key_id}/decrypt")
async def decrypt_key(
    key_id: int,
    password: str = Form(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Decrypt and return private key (requires password)"""
    result = await db.execute(select(SSHKey).where(SSHKey.id == key_id))
    key = result.scalar_one_or_none()
    
    if not key:
        return {"error": "Key not found"}
    
    decrypted = decrypt_private_key(key.private_key, password)
    
    if decrypted is None:
        return {"error": "Неверный пароль"}
    
    return {"private_key": decrypted}
