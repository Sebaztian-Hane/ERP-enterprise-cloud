"""Optional industrial master data; inventory is always posted separately."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MaterialProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    description: str | None = Field(default=None, max_length=2000)
    material_type: Literal["merchandise", "raw_material", "semi_finished", "finished", "consumable", "service"] = "merchandise"
    barcode: str | None = Field(default=None, max_length=64)
    brand: str | None = Field(default=None, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=160)
    manufacturer_part_number: str | None = Field(default=None, max_length=80)
    net_weight: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    gross_weight: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=3)
    weight_unit: Literal["KG", "G", "T"] | None = None
    country_of_origin: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    hs_code: str | None = Field(default=None, pattern=r"^\d{6,10}$")
    lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    minimum_order_quantity: int | None = Field(default=None, ge=0, le=999999999)
    reorder_point: int | None = Field(default=None, ge=0, le=999999999)
    safety_stock: int | None = Field(default=None, ge=0, le=999999999)
    storage_conditions: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_weights(self):
        if (self.net_weight is not None or self.gross_weight is not None) and not self.weight_unit:
            raise ValueError("weight_unit es obligatorio al indicar pesos")
        if self.net_weight is not None and self.gross_weight is not None and self.gross_weight < self.net_weight:
            raise ValueError("gross_weight no puede ser menor que net_weight")
        return self
