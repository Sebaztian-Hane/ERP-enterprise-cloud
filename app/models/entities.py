import uuid
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class TimestampedTenantModel(Base):
    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Mexico_City", nullable=False)
    # A tenant can operate in both channels. This selects the default
    # commercial experience; tenant_id remains the data-isolation boundary.
    business_profile: Mapped[str] = mapped_column(String(16), default="hybrid", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"), Index("ix_memberships_user_active", "user_id", "is_active"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LegalEntity(TimestampedTenantModel):
    """A taxpayer/legal issuer inside a SaaS tenant.

    A tenant is the data-isolation boundary; it is not necessarily the company
    that issues a Peruvian fiscal document.  Keeping this distinction explicit
    prevents future CPE numbering and tax configuration from leaking between
    legal entities that share one ERP subscription.
    """

    __tablename__ = "legal_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        UniqueConstraint("tenant_id", "tax_id"),
        Index("ix_legal_entities_tenant_active", "tenant_id", "is_active"),
    )
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str] = mapped_column(String(16), nullable=False)
    tax_regime: Mapped[str | None] = mapped_column(String(80))
    fiscal_address: Mapped[str] = mapped_column(Text, nullable=False)
    ubigeo: Mapped[str | None] = mapped_column(String(6))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class EntityAPISunatCredential(TimestampedTenantModel):
    """Encrypted APISUNAT access scoped to one taxpayer within one tenant.

    The plaintext provider token deliberately never becomes an ORM attribute.
    It is decrypted only in the server process immediately before an APISUNAT
    request, and is not included in audit or provider payload snapshots.
    """

    __tablename__ = "entity_apisunat_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "legal_entity_id"),
        Index("ix_entity_apisunat_credentials_tenant_entity_active", "tenant_id", "legal_entity_id", "is_active"),
    )
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("legal_entities.id", ondelete="CASCADE"), nullable=False,
    )
    issuer_ruc: Mapped[str] = mapped_column(String(11), nullable=False)
    persona_id: Mapped[str] = mapped_column(String(160), nullable=False)
    persona_token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_series: Mapped[str] = mapped_column(String(8), default="F001", nullable=False)
    receipt_series: Mapped[str] = mapped_column(String(8), default="B001", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Branch(TimestampedTenantModel):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "legal_entity_id", "code"),
        Index("ix_branches_tenant_entity_active", "tenant_id", "legal_entity_id", "is_active"),
    )
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("legal_entities.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    ubigeo: Mapped[str | None] = mapped_column(String(6))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TenantModuleSetting(Base):
    __tablename__ = "tenant_module_settings"
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    module_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class DocumentSequence(Base):
    """Tenant-safe operational document numbering configuration.

    CPE series/correlatives remain controlled by the external fiscal provider;
    these sequences identify the ERP's sale and purchase documents.
    """

    __tablename__ = "document_sequences"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("legal_entities.id"))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"))
    module: Mapped[str] = mapped_column(String(40), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    prefix: Mapped[str | None] = mapped_column(String(16))
    next_number: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MembershipModuleSetting(Base):
    __tablename__ = "membership_module_settings"
    __table_args__ = (UniqueConstraint("membership_id", "module_code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenant_memberships.id", ondelete="CASCADE"), nullable=False)
    module_code: Mapped[str] = mapped_column(String(50), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PasswordChangeCode(Base):
    __tablename__ = "password_change_codes"
    __table_args__ = (Index("ix_password_change_codes_user_created", "user_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserSecurityState(Base):
    """Server-side invalidation watermark for Supabase access tokens.

    Supabase access tokens remain cryptographically valid until their expiry,
    even if a refresh token is revoked.  This watermark is checked by our API
    on every authenticated request so a password reset or an explicit
    "close all sessions" action has an immediate effect here.
    """

    __tablename__ = "user_security_states"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    valid_after_iat: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Customer(TimestampedTenantModel):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("tenant_id", "code"), Index("ix_customers_tenant_name", "tenant_id", "name"))
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    tax_id: Mapped[str | None] = mapped_column(String(40))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Category(TimestampedTenantModel):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UnitOfMeasure(Base):
    """Tenant-owned operational/SUNAT unit of measure.

    Product quantities remain meaningful only within the unit selected when the
    product was created.  The composite FK added in migration 0043 prevents a
    product in one tenant from ever referencing a unit belonging to another.
    """

    __tablename__ = "units_of_measure"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Product(TimestampedTenantModel):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku"), Index("ix_products_tenant_name", "tenant_id", "name"))
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("categories.id"))
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Compatibility field for existing API consumers. New writes are derived
    # from unit_code; callers must send unit_id instead of free text.
    unit: Mapped[str] = mapped_column(String(16), default="unit", nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    tracking_mode: Mapped[str] = mapped_column(String(12), default="none", nullable=False)
    cost_method: Mapped[str] = mapped_column(String(20), default="weighted_average", nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Supplier(TimestampedTenantModel):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("tenant_id", "code"), Index("ix_suppliers_tenant_name", "tenant_id", "name"))
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    tax_id: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Warehouse(TimestampedTenantModel):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Inventory(TimestampedTenantModel):
    __tablename__ = "inventories"
    __table_args__ = (UniqueConstraint("tenant_id", "product_id", "warehouse_id"),)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=0, nullable=False)


class InventoryMovement(TimestampedTenantModel):
    __tablename__ = "inventory_movements"
    __table_args__ = (Index("ix_movements_tenant_product_created", "tenant_id", "product_id", "created_at"),)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    reference: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)


class InventoryLot(TimestampedTenantModel):
    __tablename__ = "inventory_lots"
    __table_args__ = (UniqueConstraint("tenant_id", "product_id", "warehouse_id", "lot_number"),)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    lot_number: Mapped[str] = mapped_column(String(100), nullable=False)
    original_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    available_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expiration_date: Mapped[date | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="available", nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class InventorySerial(TimestampedTenantModel):
    __tablename__ = "inventory_serials"
    __table_args__ = (UniqueConstraint("tenant_id", "product_id", "serial_number"),)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_lots.id"))
    serial_number: Mapped[str] = mapped_column(String(120), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="available", nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class InventoryTransfer(TimestampedTenantModel):
    __tablename__ = "inventory_transfers"
    __table_args__ = (UniqueConstraint("tenant_id", "number"),)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    source_warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    destination_warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="completed", nullable=False)
    serial_numbers: Mapped[list[str] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)


class Sale(TimestampedTenantModel):
    __tablename__ = "sales"
    __table_args__ = (UniqueConstraint("tenant_id", "number"), Index("ix_sales_tenant_created", "tenant_id", "created_at"))
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    # The column has existed since the quote/order conversion workflow.  It is
    # mapped explicitly so fulfilment and cancellation cannot accidentally
    # treat a delivered order as a direct, stock-issuing sale.
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id"), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    price_list_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("price_lists.id"))
    payment_term_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_terms.id"))
    payment_term_code: Mapped[str | None] = mapped_column(String(24))
    payment_term_name: Mapped[str | None] = mapped_column(String(120))
    payment_term_days_due: Mapped[int | None] = mapped_column()
    due_date: Mapped[date | None] = mapped_column()
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_centers.id"))
    cost_center_code: Mapped[str | None] = mapped_column(String(32))
    cost_center_name: Mapped[str | None] = mapped_column(String(120))
    tax_rate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tax_rates.id"))
    tax_rate_code: Mapped[str | None] = mapped_column(String(24))
    tax_rate_name: Mapped[str | None] = mapped_column(String(120))
    tax_rate_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    taxable_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Operational context is derived from the selected warehouse at posting time.
    # It is stored on the document so later warehouse reconfiguration cannot
    # alter the legal/audit context of an already posted transaction.
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("legal_entities.id"))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"))
    status: Mapped[str] = mapped_column(String(24), default="posted", nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)


class SaleItem(Base):
    __tablename__ = "sale_items"
    __table_args__ = (UniqueConstraint("tenant_id", "sale_id", "product_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class Purchase(TimestampedTenantModel):
    __tablename__ = "purchases"
    __table_args__ = (UniqueConstraint("tenant_id", "number"), Index("ix_purchases_tenant_created", "tenant_id", "created_at"))
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), unique=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), nullable=False)
    # See Sale: these are immutable posting-time operational references.
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("legal_entities.id"))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"))
    status: Mapped[str] = mapped_column(String(24), default="received", nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    supplier_document_type: Mapped[str | None] = mapped_column(String(8))
    supplier_document_series: Mapped[str | None] = mapped_column(String(8))
    supplier_document_number: Mapped[str | None] = mapped_column(String(16))
    supplier_document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supplier_tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    payment_term_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("payment_terms.id"))
    payment_term_code: Mapped[str | None] = mapped_column(String(24))
    payment_term_name: Mapped[str | None] = mapped_column(String(120))
    payment_term_days_due: Mapped[int | None] = mapped_column()
    due_date: Mapped[date | None] = mapped_column()
    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("cost_centers.id"))
    cost_center_code: Mapped[str | None] = mapped_column(String(32))
    cost_center_name: Mapped[str | None] = mapped_column(String(120))
    tax_rate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tax_rates.id"))
    tax_rate_code: Mapped[str | None] = mapped_column(String(24))
    tax_rate_name: Mapped[str | None] = mapped_column(String(120))
    tax_rate_percent: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    taxable_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (UniqueConstraint("tenant_id", "purchase_id", "product_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    purchase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class ElectronicDocument(TimestampedTenantModel):
    """An APISUNAT CPE generated from one posted sale."""

    __tablename__ = "electronic_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sale_id"),
        UniqueConstraint("tenant_id", "file_name"),
        Index("ix_electronic_documents_tenant_status", "tenant_id", "status"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("legal_entities.id"))
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"))
    document_type: Mapped[str] = mapped_column(String(8), nullable=False)
    series: Mapped[str] = mapped_column(String(8), nullable=False)
    correlative: Mapped[str] = mapped_column(String(16), nullable=False)
    file_name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    provider_document_id: Mapped[str | None] = mapped_column(String(80))
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="PEN", nullable=False)
    xml_url: Mapped[str | None] = mapped_column(Text)
    cdr_url: Mapped[str | None] = mapped_column(Text)
    provider_payload: Mapped[dict | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(Text)


class Payment(TimestampedTenantModel):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("tenant_id", "number"), Index("ix_payments_tenant_created", "tenant_id", "created_at"))
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    payment_type: Mapped[str] = mapped_column(String(16), nullable=False)
    cash_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cash_accounts.id"), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="posted", nullable=False)
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("approval_requests.id"))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    executed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountReceivable(TimestampedTenantModel):
    __tablename__ = "accounts_receivable"
    __table_args__ = (UniqueConstraint("tenant_id", "sale_id"), Index("ix_ar_tenant_status", "tenant_id", "status"))
    sale_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    due_date: Mapped[date] = mapped_column(nullable=False)


class AccountPayable(TimestampedTenantModel):
    __tablename__ = "accounts_payable"
    __table_args__ = (UniqueConstraint("tenant_id", "purchase_id"), Index("ix_ap_tenant_status", "tenant_id", "status"))
    purchase_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchases.id"), nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    balance_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    due_date: Mapped[date] = mapped_column(nullable=False)


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (UniqueConstraint("payment_id", "receivable_id"), UniqueConstraint("payment_id", "payable_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("payments.id", ondelete="CASCADE"), nullable=False)
    receivable_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts_receivable.id"))
    payable_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("accounts_payable.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class Document(TimestampedTenantModel):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("tenant_id", "storage_path"), Index("ix_documents_tenant_created", "tenant_id", "created_at"))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    entity: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AssistantConversation(TimestampedTenantModel):
    """A private, tenant-scoped conversation owned by exactly one ERP member."""

    __tablename__ = "assistant_conversations"
    __table_args__ = (Index("ix_assistant_conversations_member_updated", "tenant_id", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), default="Nueva conversación", nullable=False)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        CheckConstraint("role in ('user', 'assistant')", name="assistant_messages_role_valid"),
        Index("ix_assistant_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
