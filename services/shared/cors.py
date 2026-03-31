import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def install_cors(app: FastAPI) -> None:
    raw_origins = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if raw_origins == "*":
        allow_origins = ["*"]
    else:
        allow_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
