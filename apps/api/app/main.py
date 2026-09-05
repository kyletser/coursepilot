from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent import build_openai_chat_adapter
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.errors import install_exception_handlers
from app.migration_state import required_database_revision
from app.retrieval import (
    BGEM3EmbeddingAdapter,
    BGERerankerAdapter,
    LexicalIndexManager,
    PostgresPgVectorDenseRetriever,
)
from app.routers import (
    auth,
    chat,
    courses,
    documents,
    evaluation,
    graph,
    health,
    learning,
)
from app.security import PasswordService, TokenService


def create_app(
    settings: Settings | None = None, *, engine: AsyncEngine | None = None
) -> FastAPI:
    app_settings = settings or get_settings()
    app_engine = engine or create_engine(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await app_engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        debug=app_settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.db_engine = app_engine
    session_factory = create_session_factory(app_engine)
    application.state.session_factory = session_factory
    application.state.password_service = PasswordService(app_settings)
    application.state.token_service = TokenService(app_settings)
    embedding_adapter = BGEM3EmbeddingAdapter(
        app_settings.embedding_model,
        allow_download=app_settings.model_allow_download,
        cache_folder=app_settings.hf_hub_cache,
    )
    application.state.embedding_adapter = embedding_adapter
    application.state.dense_retriever = PostgresPgVectorDenseRetriever(
        session_factory,
        embedding_adapter,
        dialect_name=app_engine.dialect.name,
    )
    application.state.lexical_index_manager = LexicalIndexManager(
        app_settings.index_root
    )
    application.state.reranker = BGERerankerAdapter(
        app_settings.reranker_model,
        allow_download=app_settings.model_allow_download,
        cache_folder=app_settings.hf_hub_cache,
    )
    application.state.chat_adapter = build_openai_chat_adapter(app_settings)
    application.state.required_db_revision = (
        required_database_revision()
        if app_settings.readiness_check_migrations
        else None
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> Response:
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    install_exception_handlers(application)
    application.include_router(health.router)
    application.include_router(auth.router)
    application.include_router(courses.router)
    application.include_router(documents.router)
    application.include_router(graph.router)
    application.include_router(chat.router)
    application.include_router(learning.router)
    application.include_router(evaluation.router)
    return application


app = create_app()
