"""Allow application releases before the optional industrial schema is enabled."""

from sqlalchemy import inspect
from sqlalchemy.orm import Session


def material_profiles_available(db: Session) -> bool:
    return inspect(db.connection()).has_table("product_material_profiles")


def supported_material_template(template: dict, db: Session) -> dict:
    if material_profiles_available(db):
        return template
    # Core fields already exist in products. Never advertise columns that would
    # fail at commit on a database that has not received the additive migration.
    columns = template["columns"][:9]
    return {**template, "version": "1.1", "columns": columns, "example": template["example"][:9], "fields": [field for field in template["fields"] if field["key"] in columns]}
