from fastapi import FastAPI

from app.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title=(settings or Settings()).app_name)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
