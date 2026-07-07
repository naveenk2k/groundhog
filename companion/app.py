"""Groundhog companion: FastAPI app.

This is the foundation piece (issue #1): boot, health check, and secret-header
authentication. Transcript fetching, embeddings, corpus storage, and the
Claude call are separate issues (#2, #3, #5) that will add routes here.
"""

from fastapi import FastAPI

from companion.auth import SecretAuthMiddleware

app = FastAPI(title="Groundhog companion")
app.add_middleware(SecretAuthMiddleware)


@app.get("/health")
async def health() -> dict:
    """Liveness check. Deliberately unauthenticated - see auth.py."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict:
    """Placeholder authenticated route, useful for verifying the secret works
    end-to-end until #2/#3/#5 add the real pipeline endpoints."""
    return {"status": "ok"}
