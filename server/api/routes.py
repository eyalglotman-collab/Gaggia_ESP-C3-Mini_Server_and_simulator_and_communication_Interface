"""API route registration helpers."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.sim.controller_state import SimulatorState, simulator_runtime

router = APIRouter(prefix="/api", tags=["simulator"])


class ConnectRequest(BaseModel):
    client_ip_address: str = Field(..., min_length=1, max_length=64)


class SetStateRequest(BaseModel):
    state: SimulatorState


@router.get("/simulator")
def get_simulator_snapshot() -> dict[str, object]:
    return asdict(simulator_runtime.get_snapshot())


@router.post("/connect")
def connect_to_client(request: ConnectRequest) -> dict[str, object]:
    try:
        return asdict(simulator_runtime.connect_to_client(request.client_ip_address))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/state")
def set_simulator_state(request: SetStateRequest) -> dict[str, object]:
    try:
        return asdict(simulator_runtime.set_state(request.state))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/brew/start")
def start_brew() -> dict[str, object]:
    try:
        return asdict(simulator_runtime.start_brew())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/brew/stop")
def stop_brew() -> dict[str, object]:
    return asdict(simulator_runtime.go_idle())
