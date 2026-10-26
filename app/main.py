from fastapi import FastAPI

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
    app.include_router(reviews_router)

    @app.get("/health", tags=["system"])
    def health():
        # `llm` shows whether Ollama is switched on and reachable (see README).
        return success_response({"status": "ok", "llm": app.state.container.llm_status()}, "Healthy")

    return app


app = create_app()
