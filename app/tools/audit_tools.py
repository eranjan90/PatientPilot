"""Audit logging tool. Every other tool calls this so there is a complete, persisted action
trail for every agent/tool invocation — required for auditability and human review."""
import json

from sqlalchemy.orm import Session

from app.models import AuditEvent


def log_event(
    db: Session,
    actor_label: str,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    actor_id: int | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        event_metadata=json.dumps(metadata or {}, default=str),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def list_recent_events(db: Session, limit: int = 200) -> list[AuditEvent]:
    return db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).all()
