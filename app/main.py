import logging
import uuid

from fastapi import FastAPI, Request

from app.api.routes.documents import router as documents_router
from app.api.routes.playbooks import router as playbooks_router
from app.api.routes.reviews import router as reviews_router
from app.container import build_container
from app.core.config import Settings
from app.core.handlers import register_exception_handlers
from app.utils.response import success_response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Prototype NDA review API. AI-assisted support only; not legal advice.",
    )
    app.state.container = build_container(settings)
    register_exception_handlers(app)
    app.include_router(documents_router)
    app.include_router(playbooks_router)
    app.include_router(reviews_router)

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        # Lets a support request point at one call: the id is returned in the X-Request-ID header.
        response = await call_next(request)
        response.headers["X-Request-ID"] = f"req_{uuid.uuid4().hex}"
        return response

    # Shown in the uvicorn console, so you can see at startup whether Ollama is switched on.
    resolver = app.state.container.resolver
    llm = f"{resolver.provider} (model {resolver.model})" if resolver.configured else "none (LLM fallback off)"
    logging.getLogger("uvicorn.error").info(
        "NDA Review API: LLM provider = %s | .env file = %s", llm, settings.env_file_loaded or "not found"
    )

    @app.get("/health", tags=["system"])
    def health():
        # `llm` shows whether Ollama is switched on and reachable (see README).
        return success_response({"status": "ok", "llm": app.state.container.llm_status()}, "Healthy")

    return app


app = create_app()
