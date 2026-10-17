from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.security.auth import Actor, require_permission

router=APIRouter(prefix="/treasury",tags=["treasury"])
class AccountIn(BaseModel):
    code:str=Field(min_length=1,max_length=40); name:str=Field(min_length=1,max_length=120)
    account_type:str=Field(pattern="^(cash|bank)$"); currency:str=Field(default="MXN",min_length=3,max_length=3)
class MovementIn(BaseModel):
    cash_account_id:UUID; movement_type:str=Field(pattern="^(inflow|outflow)$"); amount:Decimal=Field(gt=0)
    payment_id:UUID|None=None; reference:str|None=None; note:str|None=None
@router.get("/accounts")
def accounts(actor:Actor=Depends(require_permission("treasury:read")),db:Session=Depends(get_db)):
    return db.execute(text("select * from cash_accounts where tenant_id=:t order by name"),{"t":str(actor.tenant_id)}).mappings().all()
@router.post("/accounts",status_code=status.HTTP_201_CREATED)
def create_account(data:AccountIn,actor:Actor=Depends(require_permission("treasury:write")),db:Session=Depends(get_db)):
    try:
        row=db.execute(text("insert into cash_accounts(tenant_id,code,name,account_type,currency) values(:t,:code,:name,:type,:currency) returning *"),{"t":str(actor.tenant_id),"code":data.code,"name":data.name,"type":data.account_type,"currency":data.currency}).mappings().one(); db.commit(); return row
    except Exception as exc: db.rollback(); raise HTTPException(409,"Código duplicado") from exc
@router.post("/movements",status_code=status.HTTP_201_CREATED)
def movement(data:MovementIn,actor:Actor=Depends(require_permission("treasury:write")),db:Session=Depends(get_db)):
    row=db.execute(text("insert into cash_movements(tenant_id,cash_account_id,payment_id,movement_type,amount,reference,note) select :t,:a,:p,:type,:amount,:ref,:note where exists(select 1 from cash_accounts where id=:a and tenant_id=:t) returning *"),{"t":str(actor.tenant_id),"a":str(data.cash_account_id),"p":str(data.payment_id) if data.payment_id else None,"type":data.movement_type,"amount":data.amount,"ref":data.reference,"note":data.note}).mappings().first()
    if not row: raise HTTPException(404,"Cuenta no encontrada")
    db.commit(); return row
