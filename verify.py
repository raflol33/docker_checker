import sys
import os

# Add current dir to path
sys.path.append(os.getcwd())

print("Verifying imports...")
try:
    from app.main import app
    from app.database import Base, engine
    from app.auth import get_password_hash
    from app.docker_service import DockerService
    print("Imports successful!")
except ImportError as e:
    print(f"Import failed: {e}")
    sys.exit(1)
except Exception as e:
    print(f"Unexpected error during import verification: {e}")
    sys.exit(1)

print("Verification complete.")
