from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.orm import Session


DEFAULT_ACCOUNTS = (
    ("1010", "Caja y bancos", "asset"),
    ("1020", "Caja chica", "asset"),
    ("1210", "Cuentas por cobrar comerciales", "asset"),
    ("2010", "Inventarios", "asset"),
    ("4010", "IGV por pagar", "liability"),
    ("4011", "IGV crédito fiscal", "asset"),
    ("4210", "Cuentas por pagar comerciales", "liability"),
    ("7010", "Ventas", "income"),
    ("6590", "Otros gastos de gestión", "expense"),
)


def ensure_default_chart(db: Session, tenant_id: UUID) -> None:
    for code, name, account_type in DEFAULT_ACCOUNTS:
        db.execute(text("""
            insert into chart_of_accounts(tenant_id, code, name, account_type)
            values(:tenant, :code, :name, :account_type)
            on conflict(tenant_id, code) do nothing
        """), {"tenant": str(tenant_id), "code": code, "name": name, "account_type": account_type})


def post_entry(
    db: Session, *, tenant_id: UUID, actor_id: UUID, number: str, description: str,
    source_type: str, source_id: UUID,
    lines: list[tuple[str, Decimal, Decimal] | tuple[str, Decimal, Decimal, UUID]],
    entry_date: date | None = None,
) -> None:
    """Create one immutable balanced entry. Duplicate source records are idempotent."""
    debit = sum((line[1] for line in lines), Decimal("0"))
    credit = sum((line[2] for line in lines), Decimal("0"))
    if debit.quantize(Decimal("0.01")) != credit.quantize(Decimal("0.01")):
        raise ValueError("El asiento no está balanceado")
    ensure_default_chart(db, tenant_id)
    entry_id = db.execute(text("""
        insert into journal_entries(tenant_id, number, entry_date, description, source_type, source_id, created_by)
        values(:tenant, :number, :entry_date, :description, :source_type, :source_id, :actor)
        on conflict(tenant_id, source_type, source_id) do nothing returning id
    """), {"tenant": str(tenant_id), "number": number, "entry_date": entry_date or date.today(), "description": description,
           "source_type": source_type, "source_id": str(source_id), "actor": str(actor_id)}).scalar()
    if not entry_id:
        return
    for line in lines:
        code, line_debit, line_credit = line[:3]
        cost_center_id = line[3] if len(line) == 4 else None
        account_id = db.execute(text("select id from chart_of_accounts where tenant_id=:tenant and code=:code"), {"tenant": str(tenant_id), "code": code}).scalar_one()
        if cost_center_id:
            valid_center = db.execute(text("""
                select 1 from cost_centers
                where tenant_id=:tenant and id=:cost_center
            """), {"tenant": str(tenant_id), "cost_center": str(cost_center_id)}).first()
            if not valid_center:
                raise ValueError("El centro de costo contable no pertenece a la empresa")
        db.execute(text("""
            insert into journal_lines(tenant_id, journal_entry_id, account_id, cost_center_id, debit, credit)
            values(:tenant, :entry, :account, :cost_center, :debit, :credit)
        """), {"tenant": str(tenant_id), "entry": str(entry_id), "account": str(account_id), "cost_center": str(cost_center_id) if cost_center_id else None, "debit": line_debit, "credit": line_credit})


def split_tax(total: Decimal, rate_percent: Decimal = Decimal("18")) -> tuple[Decimal, Decimal]:
    """Split a tax-inclusive amount using the document's immutable rate."""
    rate = Decimal(rate_percent)
    net = (total / (Decimal("1") + rate / Decimal("100"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return net, total - net


def reverse_entry(
    db: Session, *, tenant_id: UUID, actor_id: UUID, number: str, description: str,
    original_source_type: str, original_source_id: UUID, reversal_source_type: str,
) -> None:
    """Post an auditable, balanced reversal instead of editing a posted entry."""
    original = db.execute(text("""
        select id from journal_entries where tenant_id=:tenant and source_type=:type and source_id=:source and status='posted'
    """), {"tenant": str(tenant_id), "type": original_source_type, "source": str(original_source_id)}).scalar()
    if not original:
        return
    lines = db.execute(text("""select a.code, l.debit, l.credit from journal_lines l
        join chart_of_accounts a on a.id=l.account_id where l.journal_entry_id=:entry order by l.id"""), {"entry": str(original)}).mappings().all()
    post_entry(
        db, tenant_id=tenant_id, actor_id=actor_id, number=number, description=description,
        source_type=reversal_source_type, source_id=original_source_id,
        lines=[(line["code"], Decimal(str(line["credit"])), Decimal(str(line["debit"]))) for line in lines],
    )
