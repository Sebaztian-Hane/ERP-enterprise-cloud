from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, AccountReceivable
from app.schemas.accounts import AccountPayableOut, AccountReceivableOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/receivable")
def list_receivable(
    actor: Actor = Depends(require_permission("accounts:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(AccountReceivable).where(AccountReceivable.tenant_id == actor.tenant_id).order_by(AccountReceivable.created_at.desc(), AccountReceivable.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="receivables", page=page or 1, page_size=page_size or 25)


@router.get("/payable")
def list_payable(
    actor: Actor = Depends(require_permission("accounts:read")),
    db: Session = Depends(get_db),
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
):
    statement = select(AccountPayable).where(AccountPayable.tenant_id == actor.tenant_id).order_by(AccountPayable.created_at.desc(), AccountPayable.id)
    if not isinstance(page, int) and not isinstance(page_size, int):
        return db.scalars(statement).all()
    return paginate(db, statement, key="payables", page=page or 1, page_size=page_size or 25)
