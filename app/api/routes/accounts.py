from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import AccountPayable, AccountReceivable, Customer, Supplier
from app.schemas.accounts import AccountPayableOut, AccountReceivableOut
from app.schemas.pagination import paginate
from app.security.auth import Actor, require_permission

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/counterparts/{kind}")
def counterpart_names(kind: str, actor: Actor = Depends(require_permission("accounts:read")), db: Session = Depends(get_db)):
    from fastapi import HTTPException
    model = {"customers": Customer, "suppliers": Supplier}.get(kind)
    if model is None:
        raise HTTPException(status_code=404, detail="Tipo de contraparte no encontrado")
    # FI needs identifiers and display names, not access to SD/MM master data.
    return [{"id": str(identifier), "name": name} for identifier, name in db.execute(select(model.id, model.name).where(model.tenant_id == actor.tenant_id).order_by(model.name))]


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
