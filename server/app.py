"""FastAPI entry point for the Eyal Espresso server simulator."""

from fastapi import FastAPI

app = FastAPI(title="Eyal Espresso Server Simulator")


@app.get("/health")
def health() -> dict[str, str]:
    """Return a minimal health payload."""
    return {"status": "ok"}
