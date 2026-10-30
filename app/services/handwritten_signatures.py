"""Validation helpers for visual handwriting retained with internal approvals."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from xml.etree import ElementTree


MAX_HANDWRITTEN_SIGNATURE_LENGTH = 60_000
_UNSAFE_SVG = re.compile(r"<(?:script|foreignObject|iframe|object|embed)|\bon\w+\s*=|\b(?:href|xlink:href)\s*=", re.IGNORECASE)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_handwritten_signature_svg(value: str) -> str:
    """Accept only a bounded, path-based SVG captured by the approval UI."""
    signature = value.strip()
    if len(signature) > MAX_HANDWRITTEN_SIGNATURE_LENGTH:
        raise ValueError("La firma manuscrita excede el tamaño permitido")
    if not signature.startswith("<svg") or "</svg>" not in signature:
        raise ValueError("La firma manuscrita debe ser un SVG válido")
    if _UNSAFE_SVG.search(signature):
        raise ValueError("La firma manuscrita contiene contenido no seguro")
    try:
        root = ElementTree.fromstring(signature)
    except ElementTree.ParseError as exc:
        raise ValueError("La firma manuscrita debe ser un SVG válido") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError("La firma manuscrita debe ser un SVG válido")
    paths = list(root)
    if not paths or any(_local_name(path.tag) != "path" or set(path.attrib) != {"d"} or not path.attrib["d"].strip() for path in paths):
        raise ValueError("La firma manuscrita solo puede contener trazos")
    if not re.search(r"<path\b[^>]*\bd\s*=\s*[\"'][^\"']+", signature, re.IGNORECASE):
        raise ValueError("La firma manuscrita debe contener al menos un trazo")
    return signature


def approval_is_signed(approval: Mapping[str, Any] | None) -> bool:
    """A physical count requires both an approved request and internal evidence."""
    return bool(approval and approval.get("status") == "approved" and approval.get("signature_id"))
