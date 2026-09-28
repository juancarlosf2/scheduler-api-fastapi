"""Workflows host workspace endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_host, get_session
from app.features.hosts.models import Host
from app.features.workflows.models import Workflow
from app.features.workflows.workspace_schemas import WorkflowView

router = APIRouter(prefix="/v1/hosts/me", tags=["host workspace"])


@router.get("/workflows", response_model=list[WorkflowView])
def list_workflows(
    host: Host = Depends(get_current_host), session: Session = Depends(get_session)
):
    return list(
        session.scalars(
            select(Workflow)
            .where(Workflow.host_id == host.id)
            .order_by(Workflow.created_at.desc())
        )
    )
