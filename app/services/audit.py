from uuid import UUID
from sqlalchemy.orm import Session
from app.models.entities import AuditLog


def record(db: Session, *, tenant_id: UUID, actor_user_id: UUID, action: str, module: str, entity: str, entity_id: UUID | None = None) -> None:
    db.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor_user_id, action=action, module=module, entity=entity, entity_id=entity_id))
