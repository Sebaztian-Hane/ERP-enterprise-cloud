from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.entities import Customer
from app.schemas.catalog import CustomerCreate, CustomerOut, CustomerUpdate
from app.security.auth import Actor, require_permission
from app.services.audit import record

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
def list_customers(actor: Actor = Depends(require_permission("customers:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Customer).where(Customer.tenant_id == actor.tenant_id).order_by(Customer.name)).all()


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(data: CustomerCreate, actor: Actor = Depends(require_permission("customers:write")), db: Session = Depends(get_db)):
    customer = Customer(tenant_id=actor.tenant_id, **data.model_dump())
    db.add(customer)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="create", module="customers", entity="customer", entity_id=customer.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de cliente ya existe en esta empresa") from exc
    db.refresh(customer)
    return customer


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: UUID, data: CustomerUpdate, actor: Actor = Depends(require_permission("customers:write")), db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == actor.tenant_id))
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado en la empresa activa")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    record(db, tenant_id=actor.tenant_id, actor_user_id=actor.user_id, action="update", module="customers", entity="customer", entity_id=customer.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El código de cliente ya existe en esta empresa") from exc
    db.refresh(customer)
    return customer
