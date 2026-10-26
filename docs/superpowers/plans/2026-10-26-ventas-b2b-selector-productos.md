# Venta B2B y selector de productos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Registrar el método comercial previsto y seleccionar productos mediante una tabla paginada sin fallas de desborde.

**Architecture:** La API persiste un valor enumerado en `sales`; el formulario lo conserva en su borrador local. La interfaz reutiliza el patrón `fetchPage` para clientes y productos, y contiene el desplazamiento dentro del cuerpo del modal.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, React, TypeScript, CSS, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-26-ventas-b2b-selector-productos-design.md`

## Global Constraints

- El método previsto no crea movimientos de Tesorería.
- Los datos siguen aislados por `tenant_id` en los endpoints existentes.
- Las tablas no deben generar desplazamiento horizontal en escritorio.

## Review Focus

- Método inválido debe rechazarse por esquema.
- Productos repetidos no deben poder agregarse.
- La paginación debe conservar la consulta activa.
- Cliente y producto deben cerrar el modal al seleccionar.
- En móvil el desplazamiento de la tabla debe permanecer dentro del modal.

### Task 1: Persistencia del método B2B

**Files:** `app/models/entities.py`, `app/schemas/transactions.py`, `app/api/routes/sales.py`, migración y `tests/test_document_payment_terms.py`.

- [ ] Escribir y ejecutar una prueba roja para método permitido/no permitido.
- [ ] Añadir campo, migración y asignación al crear la venta.
- [ ] Ejecutar la prueba y la suite backend.

### Task 2: Formulario y modal de productos

**Files:** `src/components/SalesPage.tsx`, `src/styles.css`.

- [ ] Sustituir la alta automática de líneas por selección paginada.
- [ ] Añadir método previsto al borrador y a la solicitud.
- [ ] Confinar tabla y paginador dentro del modal.

### Task 3: Verificación visual

- [ ] Compilar frontend.
- [ ] Validar en navegador la apertura, búsqueda, paginación y selección sin enviar una venta.
