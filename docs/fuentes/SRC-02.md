# SRC-02 — Senado: Sección de Leyes

Ficha de descubrimiento (H0). Verificada el 2026-09-26.

## Acceso

- **Portal:** `https://leyes.senado.gov.co/`. Tiene buscadores de proyectos de ley, proyectos de acto legislativo, leyes y actos, con filtros por legislatura (1990-1991 a 2026-2027), autor, comisión y palabra clave. Las columnas son: N.º Senado, N.º Cámara, cuatrienio, título, autor, comisión, estado y detalle.
- **Endpoints usados por la página** (`js/app.js`): `api/search_pdly.php`, `api/get_detalle_pdly.php`, `api/search_pal.php`, `api/get_detalle_pal.php`, `api/search_lys.php`, `api/get_detalle_lys.php`, `api/search_actos.php` y `api/get_detalle_actos.php`.
- **Protección anti-bots:** la página ejecuta un reto JavaScript de FortiWeb (`/fwbbot_check?token=…`) antes de permitir las consultas.
- **robots.txt:** no existe (404).
- **Informes estadísticos por legislatura:** PDFs públicos en `/Informes estadisticos/AAAA-AAAA.pdf`.

## Decisión

**No se automatiza la adquisición desde esta fuente mientras no haya autorización.** La protección anti-bots es una señal explícita del operador del sitio. Resolver su reto o imitar un navegador para evitarla sería eludir un control de acceso, y eso contradice la regla de incorporación del PRD (§7, DEC-10: condiciones de uso por fuente). Estado: `candidate`, sin perfil de uso.

## Vías propuestas

1. **Solicitar acceso a la Sección de Leyes del Senado.** Pedir la API documentada o una exportación periódica, con permiso explícito para uso automatizado, límites de frecuencia y un contacto técnico. Si lo conceden, la autorización se registra como evidencia en el perfil de uso (`evidence_url`) y el conector usa los endpoints `search_*/get_detalle_*`.
2. **Mientras tanto, cubrir la identidad de los proyectos con fuentes permitidas:**
   - SRC-01 ya trae la numeración oficial en `project_name`, incluida la doble Senado–Cámara;
   - SRC-06 (Buscador Legislativo de Cámara) está pendiente de descubrimiento;
   - buscar en datos.gov.co conjuntos oficiales del Congreso.
3. **Informes estadísticos PDF.** Son documentos públicos enlazados, útiles como contexto, pero su descarga también pasa por el mismo sitio. Se valida junto con la solicitud.

## Borrador de solicitud

> Asunto: Solicitud de acceso automatizado a la información de proyectos de ley
>
> Somos el equipo de botRepos Legislativo, un servicio de consulta ciudadana sobre el trámite legislativo con citas verificables a fuentes oficiales. Solicitamos autorización y, si existe, documentación para consultar de forma automatizada y con baja frecuencia (por ejemplo, una actualización cada 6 horas) la información de proyectos de ley y de acto legislativo publicada en leyes.senado.gov.co, o una exportación periódica equivalente. Citaremos siempre la fuente y el enlace original, y no republicaremos los documentos completos sin autorización. Contacto técnico: juanku2003@gmail.com.
