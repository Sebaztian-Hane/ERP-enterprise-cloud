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
@router.get("/statement-lines")
def statement_lines(cash_account_id:UUID|None=None,actor:Actor=Depends(require_permission("treasury:read")),db:Session=Depends(get_db)):
 query="select * from bank_statement_lines where tenant_id=:t"
 params={"t":str(actor.tenant_id)}
 if cash_account_id:
  query+=" and cash_account_id=:a"; params["a"]=str(cash_account_id)
 return db.execute(text(query+" order by transaction_date desc, created_at desc"),params).mappings().all()
@router.get("/movements")
def movements(cash_account_id:UUID|None=None,actor:Actor=Depends(require_permission("treasury:read")),db:Session=Depends(get_db)):
 query="select c.* from cash_movements c where c.tenant_id=:t and not exists(select 1 from bank_statement_lines b where b.cash_movement_id=c.id)"
 params={"t":str(actor.tenant_id)}
 if cash_account_id:
  query+=" and c.cash_account_id=:a"; params["a"]=str(cash_account_id)
 return db.execute(text(query+" order by c.created_at desc"),params).mappings().all()
@router.post("/statement-lines")
def add(line:Line,actor:Actor=Depends(require_permission("treasury:write")),db:Session=Depends(get_db)):
 row=db.execute(text("insert into bank_statement_lines(tenant_id,cash_account_id,transaction_date,description,amount,reference) select :t,:a,:d,:x,:m,:r where exists(select 1 from cash_accounts where id=:a and tenant_id=:t) returning *"),{"t":str(actor.tenant_id),"a":str(line.cash_account_id),"d":line.transaction_date,"x":line.description,"m":line.amount,"r":line.reference}).mappings().first()
 if not row: raise HTTPException(404,"Cuenta no encontrada")
 db.commit(); return row
@router.post("/statement-lines/{line_id}/match/{movement_id}")
def match(line_id:UUID,movement_id:UUID,actor:Actor=Depends(require_permission("treasury:write")),db:Session=Depends(get_db)):
 row=db.execute(text("update bank_statement_lines b set reconciled=true,cash_movement_id=:m from cash_movements c where b.id=:id and b.tenant_id=:t and not b.reconciled and c.id=:m and c.tenant_id=:t and c.cash_account_id=b.cash_account_id and abs(c.amount)=abs(b.amount) and not exists(select 1 from bank_statement_lines used where used.cash_movement_id=c.id) returning b.*"),{"id":str(line_id),"m":str(movement_id),"t":str(actor.tenant_id)}).mappings().first()
 if not row: raise HTTPException(422,"No se puede conciliar: cuenta o importe no coinciden")
 db.commit(); return row
