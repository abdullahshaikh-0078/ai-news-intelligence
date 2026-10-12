from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import settings
from app.core.logging import logger
from app.core.exceptions import AppException
from app.api.routes.health import router as health_router
from app.api.routes.sources import router as sources_router
from app.api.routes.ingestion import router as ingestion_router
from app.api.routes.ai import router as ai_router
from app.api.routes.dedup import router as dedup_router
from app.api.routes.stories import router as stories_router
from app.api.routes.ranking import router as ranking_router
from app.api.routes.curation import router as curation_router
from app.api.routes.content import router as content_router
from app.api.routes.overview import router as overview_router
from app.api.routes.pipeline import router as pipeline_router
from app.api.routes.newsletter import router as newsletter_router
from app.api.routes.digests import router as digests_router
from app.api.routes.dispatch import router as dispatch_router
from app.infrastructure.database.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown routines."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [{settings.ENVIRONMENT}]")
    logger.info(f"Configured CORS origins: {settings.CORS_ORIGINS}")
    yield
    logger.info(f"Shutting down {settings.APP_NAME} and disposing database connection pool")
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Automated AI News Intelligence Pipeline & Real-Time REST Platform",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS Middleware setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global Exception Handlers
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """Handle custom application domain and infrastructure errors."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle request schema validation errors cleanly."""
    errors = []
    for err in exc.errors():
        errors.append({
            "loc": err.get("loc", []),
            "msg": err.get("msg", ""),
            "type": err.get("type", ""),
        })
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "The submitted payload failed validation",
                "details": errors,
            }
        },
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    """Sanitize database-level exceptions so internal queries and secrets are never exposed."""
    logger.error(f"Database error during request to {request.url.path}: {exc.__class__.__name__}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": {
                "code": "DATABASE_ERROR",
                "message": "A database operation error occurred. Please try again later.",
                "details": [],
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all handler preventing stack traces or internals from leaking."""
    logger.exception(f"Unhandled exception during request to {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please contact support.",
                "details": [],
            }
        },
    )


# Root-level health & readiness checks
app.include_router(health_router)

# Versioned API Router mount
app.include_router(health_router, prefix=settings.API_V1_STR)
app.include_router(sources_router, prefix=settings.API_V1_STR)
app.include_router(ingestion_router, prefix=settings.API_V1_STR)
app.include_router(ai_router, prefix=settings.API_V1_STR)
app.include_router(dedup_router, prefix=settings.API_V1_STR)
app.include_router(stories_router, prefix=settings.API_V1_STR)
app.include_router(ranking_router, prefix=settings.API_V1_STR)
app.include_router(curation_router, prefix=settings.API_V1_STR)
app.include_router(content_router, prefix=settings.API_V1_STR)
app.include_router(overview_router, prefix=settings.API_V1_STR)
app.include_router(pipeline_router, prefix=settings.API_V1_STR)
app.include_router(newsletter_router, prefix=settings.API_V1_STR)
app.include_router(digests_router, prefix=settings.API_V1_STR)
app.include_router(dispatch_router, prefix=settings.API_V1_STR)


@app.get("/", tags=["Root"])
async def root():
    """Welcome endpoint providing service metadata and discovery links."""
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "docs_url": "/docs",
        "health_url": "/health",
        "ready_url": "/ready",
        "api_v1_prefix": settings.API_V1_STR,
    }
