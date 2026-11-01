"""Business-specific resources, validation fields and permitted lifecycles.

This catalog is the shared contract for the API and forms. It defines actual
operational documents; automatic inventory, payroll and ledger postings are
kept in their existing posting commands, never inferred from a generic status.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, create_model


@dataclass(frozen=True)
class BusinessField:
    key: str
    label: str
    kind: str = "text"
    required: bool = False
    choices: tuple[str, ...] = ()
    reference: str | None = None
    minimum: int | None = None
    maximum: int | None = None


def f(key, label, kind="text", required=False, *, choices=(), ref=None, minimum=None, maximum=None):
    return BusinessField(key, label, kind, required, tuple(choices), ref, minimum, maximum)


@dataclass(frozen=True)
class Resource:
    code: str
    module: str
    label: str
    origin: str
    outcome: str
    fields: tuple[BusinessField, ...]
    transitions: dict[str, tuple[str, ...]]
    initial: str = "draft"
    editable: tuple[str, ...] = ("draft",)
    approvals: tuple[str, ...] = ()
    completion: dict[str, tuple[str, ...]] = field(default_factory=dict)
    limitation: str = "Documento operativo con historial."


MASTER = {"draft": ("active", "cancelled"), "active": ("inactive",), "inactive": ("active",)}
APPROVAL = {"draft": ("submitted", "cancelled"), "submitted": ("approved", "rejected"), "approved": ("closed",), "rejected": (), "cancelled": ()}
WORK = {"draft": ("released", "cancelled"), "released": ("in_progress", "cancelled"), "in_progress": ("completed",), "completed": (), "cancelled": ()}
CASE = {"open": ("in_progress", "cancelled"), "in_progress": ("resolved",), "resolved": ("closed", "in_progress"), "closed": (), "cancelled": ()}

AREAS = [
    ("hcm", "HCM", "Personas y organización", "Recursos humanos", "#hcm"),
    ("sales", "SD", "Ventas y distribución", "Comercial", "#sd"),
    ("purchases", "MM", "Abastecimiento", "Compras", "#mm"),
    ("production", "PP", "Planificación de producción", "Producción", "#pp"),
    ("maintenance", "PM", "Mantenimiento", "Mantenimiento", "#pm"),
    ("quality", "QM", "Gestión de calidad", "Calidad", "#qm"),
    ("inventory", "WM", "Operaciones de almacén", "Almacén", "#wm"),
    ("transport", "TM", "Gestión de transporte", "Transporte", "#tm"),
    ("projects", "PS", "Proyectos", "Proyectos", "#ps"),
    ("controlling", "CO", "Presupuestos y control", "Control de gestión", "#co"),
    ("assets", "AA", "Activos fijos", "Activos", "#aa"),
    ("crm", "CRM", "Oportunidades comerciales", "Comercial", "#crm"),
    ("safety", "EHS", "Seguridad y ambiente", "Seguridad", "#ehs"),
    ("service", "CS", "Servicio al cliente", "Servicio", "#cs"),
    ("operations", "FI", "Contabilidad financiera", "Finanzas", "#contabilidad"),
    ("documents", "DMS", "Gestión documental", "Documentos", "#documentos"),
]

RESOURCES = [
    Resource("employees", "hcm", "Empleados", "Alta de persona y puesto", "Empleado activo o desvinculado", (
        f("department", "Área", required=True), f("position", "Puesto", required=True), f("hire_date", "Fecha de ingreso", "date", True),
        f("work_email", "Correo laboral", "email"), f("contract_type", "Tipo de contrato", "select", True, choices=("indefinite", "fixed_term", "internship")), f("contract_end_date", "Fin de contrato", "date"),
    ), MASTER, approvals=("active",), limitation="Maestro laboral de acceso restringido a Recursos humanos; no calcula nómina."),
    Resource("absences", "hcm", "Ausencias y vacaciones", "Solicitud de empleado", "Ausencia aprobada o rechazada", (
        f("employee_id", "Empleado", "reference", True, ref="employees"), f("start_date", "Desde", "date", True), f("end_date", "Hasta", "date", True),
        f("absence_type", "Tipo", "select", True, choices=("vacation", "leave", "medical_leave")), f("reason", "Motivo", "textarea", True),
    ), APPROVAL, approvals=("approved",), limitation="Registra solicitudes y decisiones; el saldo legal de vacaciones y la nómina requieren reglas propias."),
    Resource("attendance", "hcm", "Registro de jornada", "Empleado y día trabajado", "Jornada revisada", (
        f("employee_id", "Empleado", "reference", True, ref="employees"), f("work_date", "Fecha", "date", True),
        f("hours", "Horas trabajadas", "number", True, minimum=0, maximum=24), f("note", "Observación", "textarea"),
    ), {"draft": ("submitted", "cancelled"), "submitted": ("approved", "rejected")}, approvals=("approved",)),
    Resource("equipment", "maintenance", "Equipos técnicos", "Equipo y ubicación", "Equipo habilitado para mantenimiento", (
        f("serial_number", "Serie"), f("location", "Ubicación", required=True), f("manufacturer", "Fabricante"),
        f("criticality", "Criticidad", "select", True, choices=("low", "medium", "high", "critical")), f("commissioned_on", "Puesta en servicio", "date"),
    ), MASTER),
    Resource("maintenance_notifications", "maintenance", "Avisos de mantenimiento", "Falla reportada", "Diagnóstico y solución registrados", (
        f("equipment_id", "Equipo", "reference", True, ref="equipment"), f("reported_on", "Fecha del aviso", "date", True),
        f("priority", "Prioridad", "select", True, choices=("low", "medium", "high", "critical")), f("symptom", "Síntoma", "textarea", True), f("resolution", "Solución", "textarea"),
    ), CASE, initial="open", editable=("open",), completion={"resolved": ("resolution",)}),
    Resource("maintenance_orders", "maintenance", "Órdenes de mantenimiento", "Equipo y trabajo requerido", "Trabajo ejecutado y cerrado", (
        f("equipment_id", "Equipo", "reference", True, ref="equipment"), f("planned_start", "Inicio previsto", "date", True), f("planned_end", "Fin previsto", "date", True),
        f("technician", "Responsable", required=True), f("work_scope", "Trabajo a realizar", "textarea", True), f("actual_hours", "Horas ejecutadas", "number", minimum=0), f("completion_note", "Resultado del trabajo", "textarea"),
    ), WORK, approvals=("released",), completion={"completed": ("actual_hours", "completion_note")}, limitation="Controla la orden y su ejecución; los repuestos y costos se registran mediante inventario y finanzas."),
    Resource("vehicles", "transport", "Vehículos", "Vehículo y capacidad", "Vehículo disponible", (
        f("license_plate", "Placa", required=True), f("capacity_kg", "Capacidad en kg", "number", True, minimum=1), f("vehicle_type", "Tipo", "select", True, choices=("truck", "van", "trailer", "other")),
    ), MASTER),
    Resource("freight_orders", "transport", "Órdenes de transporte", "Carga y ruta solicitada", "Servicio de transporte planificado", (
        f("origin", "Origen", required=True), f("destination", "Destino", required=True), f("planned_date", "Fecha prevista", "date", True),
        f("weight_kg", "Peso en kg", "number", True, minimum=1), f("cargo_description", "Carga", "textarea", True), f("delivery_id", "Despacho relacionado", "reference", ref="core:deliveries"),
    ), WORK, approvals=("released",), limitation="La orden organiza el transporte. El despacho de mercadería conserva su proceso y control de inventario."),
    Resource("trips", "transport", "Viajes", "Orden de transporte y vehículo", "Viaje ejecutado con evidencia de llegada", (
        f("freight_order_id", "Orden de transporte", "reference", True, ref="freight_orders"), f("vehicle_id", "Vehículo", "reference", True, ref="vehicles"),
        f("driver", "Conductor", required=True), f("departure_date", "Salida prevista", "date", True), f("arrival_date", "Llegada real", "date"), f("proof_reference", "Referencia de entrega", required=False),
    ), {"draft": ("dispatched", "cancelled"), "dispatched": ("delivered",)}, completion={"delivered": ("arrival_date", "proof_reference")}),
    Resource("bills_of_material", "production", "Listas de materiales", "Producto y componentes", "Estructura de fabricación aprobada", (
        f("product_id", "Producto a fabricar", "reference", True, ref="core:products"), f("base_quantity", "Cantidad base", "number", True, minimum=1),
        f("components", "Componentes", "lines", True),
    ), MASTER, approvals=("active",), limitation="Estructura de planificación; no contabiliza consumos ni entradas de producción."),
    Resource("production_orders", "production", "Órdenes de producción", "Lista de materiales activa y demanda", "Plan y cantidad fabricada registrados", (
        f("bom_id", "Lista de materiales", "reference", True, ref="bills_of_material"), f("quantity", "Cantidad planificada", "number", True, minimum=1),
        f("planned_start", "Inicio previsto", "date", True), f("planned_end", "Fin previsto", "date", True), f("actual_quantity", "Cantidad fabricada", "number", minimum=0), f("completion_note", "Resultado", "textarea"),
    ), WORK, approvals=("released",), completion={"completed": ("actual_quantity", "completion_note")}, limitation="Calcula necesidades de componentes al liberar. Consumos, ingresos y costeo se registran por sus procesos de inventario/contabilidad."),
    Resource("inspections", "quality", "Lotes de inspección", "Material y muestra recibida", "Resultado de inspección documentado", (
        f("product_id", "Material", "reference", True, ref="core:products"), f("inspection_date", "Fecha", "date", True), f("inspected_quantity", "Cantidad inspeccionada", "number", True, minimum=1),
        f("accepted_quantity", "Cantidad aceptada", "number", minimum=0), f("rejected_quantity", "Cantidad rechazada", "number", minimum=0), f("disposition", "Disposición", "textarea"),
    ), {"draft": ("in_progress", "cancelled"), "in_progress": ("completed",)}, completion={"completed": ("accepted_quantity", "rejected_quantity", "disposition")}, limitation="Registra resultados; el traslado físico a cuarentena o liberación de stock se controla desde Inventario."),
    Resource("nonconformities", "quality", "No conformidades", "Hallazgo de inspección", "Acción correctiva y cierre", (
        f("inspection_id", "Inspección", "reference", True, ref="inspections"), f("severity", "Severidad", "select", True, choices=("minor", "major", "critical")),
        f("finding", "Hallazgo", "textarea", True), f("corrective_action", "Acción correctiva", "textarea"),
    ), CASE, initial="open", editable=("open",), completion={"resolved": ("corrective_action",)}),
    Resource("projects", "projects", "Proyectos", "Objetivo y responsable", "Proyecto ejecutado y cerrado", (
        f("manager", "Responsable", required=True), f("start_date", "Inicio", "date", True), f("end_date", "Fin", "date", True),
        f("budget", "Presupuesto", "number", True, minimum=0), f("currency", "Moneda", "select", True, choices=("PEN", "USD", "EUR")),
    ), WORK, approvals=("released",)),
    Resource("project_tasks", "projects", "Tareas de proyecto", "Proyecto y tarea asignada", "Tarea completada", (
        f("project_id", "Proyecto", "reference", True, ref="projects"), f("assigned_to", "Responsable", required=True),
        f("due_date", "Vencimiento", "date", True), f("progress", "Avance (%)", "number", True, minimum=0, maximum=100),
    ), {"open": ("in_progress", "cancelled"), "in_progress": ("completed",)}, initial="open", editable=("open", "in_progress")),
    Resource("fixed_assets", "assets", "Activos fijos", "Adquisición y custodio", "Activo registrado o dado de baja", (
        f("acquisition_date", "Fecha de adquisición", "date", True), f("acquisition_cost", "Costo de adquisición", "number", True, minimum=0),
        f("currency", "Moneda", "select", True, choices=("PEN", "USD", "EUR")), f("useful_life_months", "Vida útil (meses)", "integer", True, minimum=1),
        f("location", "Ubicación", required=True), f("custodian", "Custodio", required=True), f("disposal_note", "Motivo de baja", "textarea"),
    ), {"draft": ("active", "cancelled"), "active": ("disposed",)}, approvals=("active", "disposed"), completion={"disposed": ("disposal_note",)}, limitation="Maestro y control de activos. La depreciación fiscal y sus asientos requieren reglas contables configuradas."),
    Resource("budgets", "controlling", "Presupuestos", "Centro de costo y período", "Presupuesto aprobado y cerrado", (
        f("cost_center_id", "Centro de costo", "reference", True, ref="core:cost_centers"), f("start_date", "Desde", "date", True), f("end_date", "Hasta", "date", True),
        f("amount", "Importe", "number", True, minimum=0), f("currency", "Moneda", "select", True, choices=("PEN", "USD", "EUR")),
    ), APPROVAL, approvals=("approved",), limitation="Control de planificación; aprobar un presupuesto no genera pagos ni asientos."),
    Resource("opportunities", "crm", "Oportunidades", "Necesidad comercial", "Oportunidad ganada o perdida", (
        f("company_name", "Empresa o prospecto", required=True), f("estimated_value", "Valor estimado", "number", True, minimum=0),
        f("currency", "Moneda", "select", True, choices=("PEN", "USD", "EUR")), f("probability", "Probabilidad (%)", "integer", True, minimum=0, maximum=100),
        f("expected_close", "Cierre previsto", "date", True), f("lost_reason", "Motivo de pérdida", "textarea"),
    ), {"open": ("qualified", "lost"), "qualified": ("proposal", "lost"), "proposal": ("won", "lost")}, initial="open", editable=("open", "qualified", "proposal"), completion={"lost": ("lost_reason",)}),
    Resource("safety_incidents", "safety", "Incidentes de seguridad", "Evento y ubicación", "Investigación y acción cerradas", (
        f("occurred_on", "Fecha", "date", True), f("location", "Ubicación", required=True), f("severity", "Severidad", "select", True, choices=("low", "medium", "high", "critical")),
        f("description", "Descripción", "textarea", True), f("corrective_action", "Acción preventiva/correctiva", "textarea"),
    ), CASE, initial="open", editable=("open",), completion={"resolved": ("corrective_action",)}, limitation="Registro restringido de seguridad. No sustituye procedimientos ni declaraciones legales de incidentes."),
    Resource("service_tickets", "service", "Solicitudes de servicio", "Solicitud de cliente", "Atención resuelta y cerrada", (
        f("customer_id", "Cliente", "reference", True, ref="core:customers"), f("reported_on", "Fecha", "date", True),
        f("priority", "Prioridad", "select", True, choices=("low", "medium", "high", "critical")), f("description", "Solicitud", "textarea", True), f("resolution", "Solución", "textarea"),
    ), CASE, initial="open", editable=("open",), completion={"resolved": ("resolution",)}),
    Resource("warehouse_tasks", "inventory", "Tareas de almacén", "Material y ubicación de trabajo", "Tarea operativa completada", (
        f("product_id", "Material", "reference", True, ref="core:products"), f("warehouse_id", "Almacén", "reference", True, ref="core:warehouses"),
        f("quantity", "Cantidad", "integer", True, minimum=1), f("task_type", "Actividad", "select", True, choices=("putaway", "picking", "internal_move")),
        f("source_bin", "Ubicación origen", required=True), f("destination_bin", "Ubicación destino", required=True), f("result_note", "Resultado", "textarea"),
    ), {"open": ("assigned", "cancelled"), "assigned": ("completed",)}, initial="open", editable=("open",), completion={"completed": ("result_note",)}, limitation="Dirige tareas; el movimiento de existencias se registra desde Inventario para conservar sus controles."),
    Resource("requisitions", "purchases", "Solicitudes de compra", "Necesidad de material", "Solicitud autorizada y vinculada a una orden", (
        f("product_id", "Material", "reference", True, ref="core:products"), f("quantity", "Cantidad", "integer", True, minimum=1), f("needed_on", "Fecha requerida", "date", True),
        f("department", "Área solicitante", required=True), f("reason", "Justificación", "textarea", True), f("purchase_order_id", "Orden de compra resultante", "reference", ref="core:purchase_orders"),
    ), APPROVAL, approvals=("approved",), completion={"closed": ("purchase_order_id",)}),
    Resource("supplier_quotes", "purchases", "Cotizaciones de proveedor", "Solicitud y oferta recibida", "Oferta seleccionada o descartada", (
        f("requisition_id", "Solicitud de compra", "reference", True, ref="requisitions"), f("supplier_id", "Proveedor", "reference", True, ref="core:suppliers"),
        f("unit_price", "Precio unitario", "number", True, minimum=0), f("currency", "Moneda", "select", True, choices=("PEN", "USD", "EUR")), f("valid_until", "Vigencia", "date", True), f("lead_days", "Plazo en días", "integer", True, minimum=0),
    ), {"received": ("selected", "rejected")}, initial="received", editable=("received",), approvals=("selected",), limitation="Permite comparar ofertas por solicitud y moneda. La orden se formaliza en Órdenes de compra."),
    Resource("sales_contracts", "sales", "Acuerdos comerciales", "Cliente y condiciones", "Acuerdo vigente o cerrado", (
        f("customer_id", "Cliente", "reference", True, ref="core:customers"), f("start_date", "Inicio de vigencia", "date", True), f("end_date", "Fin de vigencia", "date", True),
        f("amount", "Importe acordado", "number", True, minimum=0), f("currency", "Moneda", "select", True, choices=("PEN", "USD", "EUR")), f("terms", "Condiciones", "textarea", True),
    ), {"draft": ("active", "cancelled"), "active": ("closed",)}, approvals=("active",), limitation="Acuerdo operativo; cotización, pedido, entrega, comprobante y cobro conservan sus documentos propios."),
]
RESOURCE_BY_CODE = {resource.code: resource for resource in RESOURCES}


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)


def build_schema(resource: Resource):
    fields: dict[str, Any] = {}
    for item in resource.fields:
        annotation: Any = {"date": date, "reference": UUID, "number": Decimal, "integer": int, "lines": list[Component], "email": EmailStr}.get(item.kind, str)
        constraints: dict[str, Any] = {}
        if item.choices:
            annotation = Literal[item.choices]
        if item.kind in {"number", "integer"}:
            constraints.update(ge=item.minimum if item.minimum is not None else 0, le=item.maximum or 999999999)
            if item.kind == "number": constraints.update(max_digits=14, decimal_places=3, allow_inf_nan=False)
        elif item.kind == "lines": constraints.update(min_length=1, max_length=100)
        elif annotation is str: constraints.update(min_length=1, max_length=2000 if item.kind == "textarea" else 160)
        if not item.required:
            annotation = annotation | None
        fields[item.key] = (annotation, Field(default=... if item.required else None, **constraints))
    return create_model(f"Enterprise_{resource.code}", __config__=ConfigDict(extra="forbid", str_strip_whitespace=True), **fields)


SCHEMAS = {resource.code: build_schema(resource) for resource in RESOURCES}
