from uuid import UUID
from sqlalchemy.orm import Session
from app.models.entities import AuditLog
from app.services.rag_sync import queue_sync_event


RAG_SYNC_MODULES = {"customers", "products", "suppliers", "inventory", "sales", "purchases", "payments", "tenants"}


def record(db: Session, *, tenant_id: UUID, actor_user_id: UUID, action: str, module: str, entity: str, entity_id: UUID | None = None) -> None:
    db.add(AuditLog(tenant_id=tenant_id, actor_user_id=actor_user_id, action=action, module=module, entity=entity, entity_id=entity_id))
    if module in RAG_SYNC_MODULES:
        queue_sync_event(db, tenant_id=tenant_id, module=module, entity=entity, entity_id=entity_id, action=action)
