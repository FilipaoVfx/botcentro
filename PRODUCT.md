# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + TypeScript con Vite (elegido por el responsable del proyecto el 2026-09-26). La SPA compilada se sirve desde la API FastAPI existente, que actúa como BFF frente a InsForge.

## Users

En esta etapa, previa a cualquier ingesta, el único usuario es el responsable del proyecto, con rol `admin`. Usa el panel para ver qué ocurre en el sistema antes de activar fuentes y flujos hacia la base. Más adelante habrá operadores, revisores de datos, ingenieros y observadores internos (prd-panel-web.md §4).

## Product Purpose

Consola interna para observar y, en fases posteriores, operar los flujos del bot legislativo: adquisición, normalización, documentos, indexación, consultas y entrega en Telegram. Responde qué funciona, qué se ejecuta, qué espera, qué falló, qué está desactualizado y cuánto cuesta, y permite bajar de la señal global al registro que la explica (prd-panel-web.md §1).

## Positioning

El panel lee el estado autoritativo del backend con la identidad del propio operador, a través de RLS de InsForge. Nunca inventa métricas: distingue «sin datos», «no instrumentado», «vencido» y «desconectado».

## Operating Context

- Uso en escritorio (ancho de referencia 1.440 px) con acceso esencial desde 390 px.
- Español, zona horaria America/Bogota; los instantes se muestran absolutos y relativos.
- Expuesto por un túnel mientras dura el piloto, con acceso privado mediante código por email de InsForge Auth.

## Capabilities and Constraints

- Esta iteración es P0 de solo lectura (hito P-H2). Las acciones (P-H3) se muestran deshabilitadas con su motivo.
- Las capacidades sin instrumentación todavía (workers, intentos, alertas, incidentes, logs) se muestran como no instrumentadas, nunca con cifras.
- Los secretos, tokens y el texto de conversaciones no se muestran.
- Los datos simulados solo se admiten rotulados como demostración. En esta iteración no se usan.

## Evidence on Hand

- Base real en InsForge: catálogo de 2 corporaciones y 14 fuentes candidatas. Aún no hay ejecuciones, trabajos, documentos ni consultas.
- Requisitos en `prd-panel-web.md` y `srs-panel-web.md`.

## Product Principles

1. Estado verificable: cada cifra indica su origen y cuándo se observó.
2. Diagnóstico progresivo: resumen → área → registro.
3. Progreso honesto: porcentajes solo con denominador válido.
4. Observar no es controlar: ver un proceso no implica poder alterarlo.
5. Cobertura explícita: lo no instrumentado se declara como tal.

## Accessibility & Inclusion

- Estados con texto e icono, además de color.
- Navegación completa por teclado, con el foco conservado durante las actualizaciones.
- Cada vista visual tiene su alternativa en tabla (prd-panel-web.md §13).
