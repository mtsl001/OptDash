"""Entry point — starts the FastAPI server."""
import uvicorn
from optdash.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "optdash.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )
