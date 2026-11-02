from fastapi import APIRouter, Depends
from app.security.action_confirmation import enforce_action_confirmation
from app.api.routes import enterprise
from app.api.routes import migration_projects
from app.api.routes import accounts, auth, customers, deliveries, documents, imports, inventory, legal_entities, notifications, operations, payments, platform, products, purchases, rag, reconciliation, reports, sales, suppliers, tenants, treasury, workflow

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(enforce_action_confirmation)])
api_router.include_router(auth.router)
api_router.include_router(platform.router)
api_router.include_router(customers.router)
api_router.include_router(products.router)
api_router.include_router(suppliers.router)
api_router.include_router(inventory.router)
api_router.include_router(sales.router)
api_router.include_router(deliveries.router)
api_router.include_router(purchases.router)
api_router.include_router(payments.router)
api_router.include_router(documents.router)
api_router.include_router(reports.router)
api_router.include_router(tenants.router)
api_router.include_router(legal_entities.router)
api_router.include_router(accounts.router)
api_router.include_router(workflow.router)
api_router.include_router(treasury.router)
api_router.include_router(reconciliation.router)
api_router.include_router(operations.router)
api_router.include_router(notifications.router)
api_router.include_router(rag.router)
api_router.include_router(imports.router)
api_router.include_router(enterprise.router)
api_router.include_router(migration_projects.router)
