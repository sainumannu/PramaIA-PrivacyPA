from functools import wraps
from fastapi import HTTPException, status
from typing import Optional, Callable
from backend.services.license_service import LicenseService


def require_license(required_module: Optional[str] = None):
    """
    Decorator per proteggere endpoint che richiedono licenza valida.
    
    Usage:
        @router.get("/api/protected")
        @require_license(required_module="albo")
        async def protected_endpoint():
            ...
    
    Args:
        required_module: modulo specifico richiesto (opzionale)
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Verifica licenza (usa cache se disponibile)
            license_svc = LicenseService(db=None)
            result = await license_svc.verify_license(required_module=required_module)
            
            if not result.valid:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error": "License validation failed",
                        "code": result.code,
                        "message": result.message,
                    },
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


async def check_license_or_fail(required_module: Optional[str] = None) -> None:
    """
    Utility per verificare licenza in modo imperativo.
    Lancia HTTPException se non valida.
    
    Usage:
        async def some_function():
            await check_license_or_fail(required_module="protocollo")
            # ... procedi se licenza OK
    """
    license_svc = LicenseService(db=None)
    result = await license_svc.verify_license(required_module=required_module)
    
    if not result.valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "License validation failed",
                "code": result.code,
                "message": result.message,
            },
        )
