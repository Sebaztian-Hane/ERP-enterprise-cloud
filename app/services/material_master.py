"""Shared material import/export contract. New attributes never mutate stock."""

from app.schemas.materials import MaterialProfile

# key, group, description, example. One definition feeds XLSX, CSV and the UI.
MATERIAL_FIELDS = [
    ("sku", "Identificación", "Código único en la empresa. Obligatorio, hasta 64 caracteres.", "MAT-001"),
    ("name", "Identificación", "Nombre comercial. Obligatorio, hasta 160 caracteres.", "Alambre galvanizado"),
    ("category", "Identificación", "Categoría; se crea si aún no existe.", "Metales"),
    ("unit", "Identificación", "Código de unidad activa de Configuración. unit se interpreta como NIU.", "NIU"),
    ("sale_price", "Valoración", "Precio de venta no negativo, hasta 2 decimales. Moneda operativa de la empresa.", "100.00"),
    ("average_cost", "Valoración", "Costo inicial no negativo; no genera existencias ni asientos.", "80.00"),
    ("tracking_mode", "Inventario", "none, lot o serial. Valores vacíos usan none.", "lot"),
    ("cost_method", "Valoración", "weighted_average o fifo. No ejecuta ni certifica el costeo.", "weighted_average"),
    ("is_active", "Identificación", "true o false. Vacío usa true.", "true"),
    ("description", "Identificación", "Descripción técnica, hasta 2000 caracteres.", "Alambre para uso industrial"),
    ("material_type", "Identificación", "merchandise, raw_material, semi_finished, finished, consumable o service. Clasificación informativa.", "raw_material"),
    ("barcode", "Identificación", "Código de barras como texto para conservar ceros iniciales.", "0001234567890"),
    ("brand", "Fabricación", "Marca, hasta 80 caracteres.", "Marca industrial"),
    ("manufacturer", "Fabricación", "Fabricante, hasta 160 caracteres.", "Fabricante de ejemplo"),
    ("manufacturer_part_number", "Fabricación", "Código de pieza del fabricante como texto.", "FAB-001"),
    ("net_weight", "Logística", "Peso neto no negativo, hasta 3 decimales. Requiere weight_unit.", "1.500"),
    ("gross_weight", "Logística", "Peso bruto mayor o igual al neto.", "1.600"),
    ("weight_unit", "Logística", "KG, G o T.", "KG"),
    ("country_of_origin", "Logística", "Código de país de dos letras mayúsculas, por ejemplo PE.", "PE"),
    ("hs_code", "Logística", "Partida arancelaria de 6 a 10 dígitos como texto.", "7217200000"),
    ("lead_time_days", "Planificación", "Plazo de abastecimiento en días enteros, de 0 a 3650.", "7"),
    ("minimum_order_quantity", "Planificación", "Cantidad mínima de pedido en unidades enteras. Dato de planificación.", "10"),
    ("reorder_point", "Planificación", "Punto de reposición global informativo; no reemplaza mínimos por almacén.", "50"),
    ("safety_stock", "Planificación", "Reserva de seguridad informativa; no reserva ni crea stock.", "20"),
    ("storage_conditions", "Logística", "Condiciones de almacenamiento, hasta 500 caracteres.", "Mantener seco y bajo techo"),
]

MATERIAL_COLUMNS = [field[0] for field in MATERIAL_FIELDS]
PROFILE_FIELDS = set(MaterialProfile.model_fields)


def material_template():
    return {
        "label": "Materiales y productos",
        "version": "2.0",
        "columns": MATERIAL_COLUMNS,
        "required_columns": ["sku", "name", "category", "unit", "sale_price", "average_cost"],
        "example": [field[3] for field in MATERIAL_FIELDS],
        "fields": [
            {"key": key, "group": group, "description": description, "required": key in {"sku", "name"}}
            for key, group, description, _ in MATERIAL_FIELDS
        ],
    }
