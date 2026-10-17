from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, AccountReceivable
from app.schemas.accounts import AccountPayableOut, AccountReceivableOut
from app.security.auth import Actor, require_permission

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/receivable", response_model=list[AccountReceivableOut])
def list_receivable(actor: Actor = Depends(require_permission("accounts:read")), db: Session = Depends(get_db)):
    return db.scalars(select(AccountReceivable).where(AccountReceivable.tenant_id == actor.tenant_id).order_by(AccountReceivable.created_at.desc())).all()


@router.get("/payable", response_model=list[AccountPayableOut])
def list_payable(actor: Actor = Depends(require_permission("accounts:read")), db: Session = Depends(get_db)):
    return db.scalars(select(AccountPayable).where(AccountPayable.tenant_id == actor.tenant_id).order_by(AccountPayable.created_at.desc())).all()
