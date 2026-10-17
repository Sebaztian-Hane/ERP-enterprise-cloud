from datetime import date
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.security.auth import Actor,require_permission
router=APIRouter(prefix="/reconciliation",tags=["reconciliation"])
class Line(BaseModel):
 cash_account_id:UUID; transaction_date:date; description:str; amount:Decimal; reference:str|None=None
@router.post("/statement-lines")
def add(line:Line,actor:Actor=Depends(require_permission("treasury:write")),db:Session=Depends(get_db)):
 row=db.execute(text("insert into bank_statement_lines(tenant_id,cash_account_id,transaction_date,description,amount,reference) select :t,:a,:d,:x,:m,:r where exists(select 1 from cash_accounts where id=:a and tenant_id=:t) returning *"),{"t":str(actor.tenant_id),"a":str(line.cash_account_id),"d":line.transaction_date,"x":line.description,"m":line.amount,"r":line.reference}).mappings().first()
 if not row: raise HTTPException(404,"Cuenta no encontrada")
 db.commit(); return row
@router.post("/statement-lines/{line_id}/match/{movement_id}")
def match(line_id:UUID,movement_id:UUID,actor:Actor=Depends(require_permission("treasury:write")),db:Session=Depends(get_db)):
 row=db.execute(text("update bank_statement_lines b set reconciled=true,cash_movement_id=:m from cash_movements c where b.id=:id and b.tenant_id=:t and c.id=:m and c.tenant_id=:t and c.cash_account_id=b.cash_account_id and abs(c.amount)=abs(b.amount) returning b.*"),{"id":str(line_id),"m":str(movement_id),"t":str(actor.tenant_id)}).mappings().first()
 if not row: raise HTTPException(422,"No se puede conciliar: cuenta o importe no coinciden")
 db.commit(); return row
