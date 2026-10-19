"""Privacy-conscious automatic business insights backed by Gemini.

Only aggregated financial/operational values leave the ERP. Customer names,
documents, identifiers and line-item detail are deliberately excluded.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from app.core.config import Settings

_CACHE: dict[str, "CachedInsight"] = {}


@dataclass
class CachedInsight:
    data: dict[str, Any]
    operation_count: int
    created_at: float


def _number(value: Decimal | int | float | str) -> float:
    return float(value or 0)


def generate_business_insight(
    *, settings: Settings, tenant_id: UUID, start_date: str, end_date: str,
    operation_count: int, sales_total: Decimal, purchases_total: Decimal,
    receipts_total: Decimal, payments_total: Decimal, receivable_total: Decimal,
    payable_total: Decimal, low_stock_count: int,
) -> dict[str, Any]:
    minimum = max(1, settings.insight_min_operations)
    if operation_count < minimum:
        return {"status": "waiting", "minimum_operations": minimum, "operation_count": operation_count,
                "message": f"El análisis automático se habilitará al registrar al menos {minimum} operaciones confirmadas en el periodo."}

    cache_key = f"{tenant_id}:{start_date}:{end_date}"
    cached = _CACHE.get(cache_key)
    if cached and operation_count - cached.operation_count < max(1, settings.insight_refresh_min_operations):
        return {**cached.data, "cached": True, "operation_count": operation_count}
    if not settings.gemini_api_key:
        return {"status": "unavailable", "message": "El análisis automático aún no está configurado en el servidor."}

    metrics = {
        "periodo": {"inicio": start_date, "fin": end_date}, "operaciones_confirmadas": operation_count,
        "ventas": _number(sales_total), "compras": _number(purchases_total),
        "cobros": _number(receipts_total), "pagos": _number(payments_total),
        "por_cobrar": _number(receivable_total), "por_pagar": _number(payable_total),
        "productos_bajo_minimo": low_stock_count,
    }
    prompt = """Eres un analista financiero de un ERP peruano. Analiza únicamente estas métricas agregadas, sin inventar datos. Devuelve JSON estricto con headline (máx. 70 caracteres), summary (máx. 230 caracteres), recommendation (máx. 180 caracteres), severity (positive|info|attention). Da una recomendación específica y prudente. No menciones IA, Gemini, prompts ni datos ausentes. Métricas:\n""" + json.dumps(metrics, ensure_ascii=False)
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 220, "responseMimeType": "application/json"}}
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_insight_model}:generateContent",
                headers={"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"}, json=payload,
            )
            response.raise_for_status()
            raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(raw)
        if not all(isinstance(data.get(key), str) and data[key].strip() for key in ("headline", "summary", "recommendation")):
            raise ValueError("Respuesta de insight incompleta")
        data = {"status": "ready", "headline": data["headline"].strip(), "summary": data["summary"].strip(),
                "recommendation": data["recommendation"].strip(), "severity": data.get("severity") if data.get("severity") in {"positive", "info", "attention"} else "info"}
        _CACHE[cache_key] = CachedInsight(data=data, operation_count=operation_count, created_at=time.time())
        return {**data, "cached": False, "operation_count": operation_count}
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return {"status": "unavailable", "message": "No fue posible generar el análisis automático por ahora."}
