# Registro de decisiones (PRD §17)

| ID | Decisión | Estado | Fecha | Evidencia / notas |
|---|---|---|---|---|
| DEC-02 | Cobertura: máximo histórico disponible por fuente («amplía lo más que puedas»). SRC-01: desde 2017-01-01 (inicio real de la serie) | Aprobada por el responsable | 2026-09-28 | Reemplaza la propuesta inicial (cuatrienio 2026–2030). No se promete completitud: la fuente solo cubre lo que registró la app |
| DEC-04 | Infraestructura: InsForge (PostgreSQL 15 + pgvector), workers Python en `insforge compute` | Aprobada | 2026-09-26 | ADR 0001 |
| DEC-05 | Índice vectorial: pgvector con índice HNSW parcial por modelo | Aprobada | 2026-09-26 | Migración `indice-vectorial-e5-small` |
| DEC-06 | Embeddings: `intfloat/multilingual-e5-small` (384 dimensiones, coseno), local con FastEmbed/ONNX | **Provisional** | 2026-09-26 | [docs/embeddings.md](embeddings.md). Se confirma con el corpus de evaluación del SRS (§16.1) antes del piloto |
| DEC-06b | OCR: Tesseract (español) en los workers | Aprobada | 2026-09-26 | Proveedor de pago solo si la calidad medida no alcanza |
| DEC-07 | Presupuesto: 5 USD/día y 100 USD/mes (global), alerta al 80 % y bloqueo al 100 % | Aprobada | 2026-09-26 | Los embeddings locales se registran con costo 0 |
| DEC-10a | SRC-01 activada con perfil v1 aprobado por el responsable (redistribución: desconocido) | Aprobada | 2026-09-27 | docs/fuentes/SRC-01.md; auditoría source.activate |
| DEC-10 | SRC-02 no se automatiza sin autorización (protección anti-bots) | Aprobada | 2026-09-26 | [docs/fuentes/SRC-02.md](fuentes/SRC-02.md) |
