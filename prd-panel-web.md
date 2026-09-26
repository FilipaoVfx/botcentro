# PRD — Panel web de operación y monitoreo de botRepos

| Campo | Valor |
|---|---|
| Producto | botRepos Legislativo Colombia — Panel de operaciones |
| Documento | Product Requirements Document |
| Versión | 1.0 |
| Fecha | 2026-09-26 |
| Estado | Propuesta detallada para implementación |
| Documento complementario | [SRS del panel](./srs-panel-web.md) |
| Público | Equipo interno de operación, datos, ingeniería y administración |
| Idioma / zona horaria inicial | Español / America/Bogota |
| Responsables | Producto, ingeniería, datos y operación: por asignar |

## 1. Propósito y contexto

Construir un panel web para observar y operar todos los flujos instrumentados del bot legislativo: adquisición desde fuentes, normalización, resolución de entidades, procesamiento de documentos, indexación, consultas, generación de respuestas y entrega en Telegram.

El panel debe responder de forma inmediata: **qué está funcionando, qué está ejecutándose, qué está esperando, qué falló, qué información está desactualizada, cuánto cuesta y qué acción puede resolverlo**. Debe permitir pasar de una señal global a la ejecución, trabajo, documento, consulta o evidencia que la explica.

Se basa en los archivos proporcionados por el usuario, `prd.md` y `srs.md`, versión 1.0 de 2026-09-25, leídos desde `C:\Users\Filipo\Documents\code`. Amplía especialmente PRD-01, PRD-02, PRD-05, PRD-09 a PRD-14 y SRS-F24 a SRS-F26 del sistema base. No reemplaza sus requisitos de trazabilidad, privacidad, permisos de fuentes ni integridad.

Los documentos base describen una arquitectura propuesta. No se ha inspeccionado una implementación ni comprobado que las tablas, APIs o métricas ya existan. Este PRD define el comportamiento objetivo del panel; sus capacidades deben conectarse con servicios reales antes de habilitarse. No se realizará investigación externa de proveedores ni se fijan versiones o tarifas en este documento.

## 2. Problema y resultado esperado

La arquitectura del bot distribuye trabajo entre conectores, planificador, colas, workers, base de datos, almacenamiento, OCR, modelos e índices. Revisar cada componente por separado dificulta identificar dónde se interrumpe un flujo y si una respuesta contiene datos antiguos.

Un indicador verde de infraestructura no demuestra que los documentos se estén publicando. Un worker con heartbeat no demuestra que esté progresando. Una respuesta generada no demuestra que Telegram la haya recibido. El panel debe representar estas diferencias y mostrar la antigüedad de su propia telemetría.

El resultado es una consola interna que permite diagnóstico y acciones acotadas sin editar tablas manualmente ni reconstruir relaciones a partir de logs dispersos.

## 3. Objetivos de producto

| ID | Objetivo | Criterio propuesto de éxito |
|---|---|---|
| PW-OBJ-01 | Entender la situación general | ≥90% de usuarios piloto identifica el componente afectado en ≤2 minutos en escenarios guiados |
| PW-OBJ-02 | Localizar la causa operativa | ≥80% llega a trabajo, intento y error relevante en ≤5 minutos desde una alerta |
| PW-OBJ-03 | Ver actividad reciente | p95 ≤5 segundos desde recepción de un evento durable hasta su representación en una sesión conectada |
| PW-OBJ-04 | Operar con trazabilidad | 100% de comandos registra actor, ámbito, motivo, selección de recursos y resultado |
| PW-OBJ-05 | Evitar falsas certezas | 100% de escenarios de desconexión o telemetría vencida se muestran como tales; ninguno como saludable |
| PW-OBJ-06 | Cubrir el flujo completo | Todas las etapas P0 del inventario tienen estado, métrica de frescura y enlace de diagnóstico o ausencia explícita de instrumentación |
| PW-OBJ-07 | Preservar el bot | La carga del panel no aumenta >10% el p95 del bot en la prueba comparativa definida en el SRS |
| PW-OBJ-08 | Reducir errores operativos | Cero comandos duplicados con efecto adicional y cero accesos entre ámbitos en pruebas de aceptación |

Estas metas son objetivos de aceptación, no resultados medidos. El piloto propuesto dura 14 días con al menos cinco participantes que cubran los roles de operación, datos e ingeniería.

## 4. Usuarios y permisos de producto

| Perfil | Necesidades | Acceso principal |
|---|---|---|
| Observador interno | Ver salud, avance, volumen y costos autorizados | Lectura de métricas y metadata saneada |
| Operador | Diagnosticar fallos y recuperar trabajo | Ejecuciones, workers, colas, comandos acotados e incidentes |
| Revisor de datos | Resolver calidad, conflictos y ambigüedades | Evidencia autorizada, cuarentena y decisiones de revisión |
| Ingeniero | Entender cuellos de botella y regresiones | Trazas, versiones, logs saneados y métricas técnicas |
| Administrador | Gobernar acceso, configuración y presupuestos | Roles, políticas operativas y acciones de mayor alcance |

Los permisos de contenido sensible se conceden aparte del rol operativo. Ser administrador de infraestructura no otorga automáticamente acceso al texto completo de conversaciones. Cada usuario opera solo en entornos y ámbitos autorizados.

## 5. Principios de diseño

1. **Estado verificable:** cada estado debe indicar su origen y cuándo se observó.
2. **Diagnóstico progresivo:** resumen → fuente/flujo → ejecución → trabajo → intento → evidencia.
3. **Control separado de observación:** ver un proceso no implica poder alterarlo.
4. **Acción con resultado comprobable:** aceptar una orden no equivale a completarla.
5. **Progreso honesto:** usar porcentaje solo cuando exista denominador válido; de lo contrario, contadores.
6. **Lectura por defecto:** priorizar diagnóstico; las mutaciones son acciones explícitas con impacto visible.
7. **Continuidad:** el bot y sus controles críticos continúan aunque el panel esté cerrado o caído.
8. **Reutilización:** el panel consume hechos operativos y evidencia existentes, sin crear otra base legislativa.
9. **Privacidad práctica:** datos saneados por defecto; acceso adicional controlado y auditado.
10. **Cobertura explícita:** una integración futura o no instrumentada se muestra como tal, nunca con métricas ficticias.

## 6. Alcance

### 6.1 P0 — Panel operativo inicial

- Acceso privado, roles, selección de entorno y aislamiento de ámbitos.
- Resumen de salud, actividad, frescura, incidentes y presupuesto.
- Mapa de flujos con enlaces a etapas, contadores y bloqueos.
- Fuentes, cobertura, última comprobación y programación.
- Ingestas actuales e históricas con detalle de ejecución y árbol de trabajos.
- Procesos activos: jobs, intentos, workers, heartbeat, leases y progreso.
- Colas, trabajos demorados, reintentos y dead-letter queue.
- Documentos: extracción, OCR, chunks, embeddings y publicación.
- Consultas: rutas SQL/documental/híbrida, tiempos, evidencias y resultados.
- Telegram: recepción, deduplicación, generación y entrega diferenciadas.
- Calidad: cuarentena, conflictos y revisión con evidencia.
- Logs saneados, trazas y navegación por identificadores.
- Alertas internas, incidentes, reconocimiento y silencios temporales.
- Costos estimados, confirmados, reservas y límites.
- Comandos de alcance individual: ingesta manual acotada, pausa/reanudación de admisión, cancelación cooperativa, reintento y reprocesamiento admitidos por el backend.
- Auditoría de accesos sensibles, comandos y cambios de configuración.
- Actualización en vivo con señal de conexión y modo de consulta periódica alternativo.

### 6.2 P1 — Mejoras posteriores al núcleo

- Acciones masivas con selección congelada, vista previa y resultados por recurso.
- Backfills amplios con simulación de alcance y presupuesto.
- Exportaciones asíncronas de reportes operativos saneados.
- Vistas guardadas, comparación de ventanas y reportes de evaluación de modelos/parsers.
- Calendario avanzado de mantenimiento y rutas externas de notificación autorizadas.
- Drenaje administrado de workers y herramientas adicionales de capacidad cuando el runtime las soporte.

### 6.3 P2 — Evolución

- Recomendaciones de diagnóstico y correlación de incidentes asistidas por modelos, siempre revisables.
- Observación de normativa, videos, noticias, X y comunidades cuando esas fuentes estén implementadas en el bot.
- Proyecciones avanzadas de costo/capacidad y automatizaciones operativas sujetas a requisitos específicos.

### 6.4 Fuera de alcance

- Reconstruir el bot, sus conectores o su modelo legislativo como parte del frontend.
- Terminal web, ejecución arbitraria de SQL, shell o código sobre producción.
- Eliminar evidencia, purgar colas indiscriminadamente o matar procesos sin protocolo de coordinación.
- Editor general de hechos legislativos por celdas sin observación, evidencia y auditoría.
- Panel público ciudadano, CRM, perfiles políticos de usuarios o analytics comerciales.
- Mostrar secretos, credenciales, tokens o conversaciones completas a cualquier operador.
- Desplegar versiones del bot o administrar infraestructura completa desde el MVP.
- Enviar mensajes a usuarios finales o volver a enviar entregas ambiguas de Telegram sin política específica.

## 7. Inventario de flujos supervisados

| Flujo | Etapas visibles | Pregunta operativa que resuelve |
|---|---|---|
| Adquisición | Programación → descubrimiento → descarga → captura | ¿Se están consultando las fuentes y llegando datos? |
| Datos estructurados | Parseo → validación → normalización → entidades → publicación | ¿Qué registros no pueden publicarse y por qué? |
| Documentos | Descarga → extracción → OCR selectivo → segmentación → chunks | ¿Qué documento está detenido y en qué página/etapa? |
| Indexación | Elegibilidad → embedding → actualización del índice → búsqueda | ¿Qué contenido aún no puede recuperarse? |
| Consulta | Recepción → autorización → intención → SQL/búsqueda → contexto → respuesta → validación | ¿Dónde se fue el tiempo o faltó evidencia? |
| Entrega | Bandeja de salida → segmentos → intentos → confirmación/estado incierto | ¿Se generó la respuesta y se entregó? |
| Revisión | Detección → asignación → evidencia → decisión → derivados | ¿Qué requiere intervención humana? |
| Operación | Alerta → incidente → diagnóstico → comando → verificación → cierre | ¿Qué se hizo y surtió efecto? |

Los eventos legislativos del sistema base no son eventos operativos. El panel muestra los primeros como contexto/evidencia y registra los segundos en un canal separado.

## 8. Arquitectura de información y navegación

Barra superior persistente: entorno, ámbito, rango temporal, zona horaria, búsqueda por ID y estado de conexión. El entorno de producción debe identificarse con texto, no solo color. Cambiar de entorno limpia selecciones y comandos pendientes del anterior.

Menú principal:

1. Resumen.
2. Flujos.
3. Fuentes.
4. Ingestas.
5. Procesos activos.
6. Colas y reintentos.
7. Documentos e índice.
8. Consultas y Telegram.
9. Calidad y revisión.
10. Alertas e incidentes.
11. Costos.
12. Auditoría.
13. Configuración, según permisos.

Las tablas y fichas deben permitir enlaces directos, filtros en la URL sin contenido sensible, ordenación y paginación. El historial tiene rango temporal; la vista «Procesos activos» representa el estado actual y lo indica para evitar que un filtro histórico oculte procesos en marcha.

## 9. Especificación de pantallas

### 9.1 Resumen

Tarjetas: salud de servicios, fuentes fuera de frescura, trabajos activos, pendientes listos, fallidos definitivos, incidencias abiertas y gasto del período. Cada tarjeta muestra `actualizado a`, unidad y acceso al detalle.

Gráficos: entradas y salidas por etapa; tiempo en cola; latencia del bot; errores por causa; costo por categoría. Mostrar ventanas de 15 minutos, 1 hora, 24 horas y 7 días. Los conteos actuales no se mezclan con acumulados históricos.

Lista priorizada «Requiere atención»: impacto, origen, antigüedad, responsable y acción disponible. Un resumen puede estar saludable en recepción del bot y degradado en OCR; no se reduce todo a un único semáforo engañoso.

### 9.2 Mapa de flujos

Representar nodos y conexiones del pipeline realmente habilitado. Cada nodo muestra actividad, entrada, salida, pendientes, fallos y antigüedad de telemetría. Los tramos estructurados y documentales se ramifican; no todos los ítems pasan por OCR o embeddings.

Seleccionar un nodo abre sus trabajos y métricas filtradas. Seleccionar un flujo concreto muestra sus pasos y dependencias. El mapa debe tener una alternativa accesible en tabla. No se necesita un editor visual de workflows.

### 9.3 Fuentes

Tabla: nombre, autoridad, estado administrativo, salud observada, cobertura, último intento, último éxito, último cambio detectado, próxima ejecución, errores y retraso. La ausencia de novedades puede ser normal si hubo comprobaciones exitosas.

Ficha: configuración saneada, perfil de uso, frecuencia, historial de runs, limitaciones conocidas y documentos/casos asociados. Acciones según permiso: iniciar carga acotada, pausar nuevas admisiones, reanudar, solicitar validación. Una fuente no se activa si incumple los requisitos de uso del sistema base.

### 9.4 Ingestas y detalle de ejecución

Tabla: `run_id`, fuente, modo, rango, origen programado/manual, estado, inicio, duración, progreso, errores y costo. Filtros por fuente, estado, modo, rango y actor.

Detalle con pestañas: resumen, etapas, trabajos, registros, documentos, errores, costos y auditoría. Mostrar cursor/checkpoint saneado, dependencias y contador de descubiertos, nuevos, sin cambios, publicados, rechazados y pendientes. Indicar si quedan tareas derivadas aunque la adquisición haya terminado.

### 9.5 Procesos activos

Separar **trabajos lógicos**, **intentos de ejecución** y **workers**. Un proceso puede estar vivo sin avanzar; un job puede conservar estado `running` aunque el worker haya dejado de reportar.

Columnas: tipo, etapa, `job_id`, ejecución padre, worker, intento, inicio, duración, última señal, último avance, lease, progreso, consumo disponible y estado de observación. CPU/memoria son opcionales si hay instrumentación; no se estiman a partir del heartbeat.

Acciones: abrir traza, inspeccionar bloqueo y solicitar cancelación cooperativa donde esté soportada. El botón debe explicar si la etapa solo puede terminar en un punto seguro.

### 9.6 Colas y reintentos

Mostrar listos, programados para más tarde, en ejecución, esperando retry y dead-letter como categorías diferentes. Métricas: edad del más antiguo elegible, tasa de entrada/salida y trabajadores disponibles. Una cola vacía puede significar falta de adquisición, no salud garantizada.

Detalle del ítem: error original, intentos, próxima fecha elegible, límite de reintentos, dependencias y contexto mínimo. El reintento individual ofrece alcance y costo potencial. Los botones no omiten `Retry-After`, permisos de fuente ni límites del proveedor.

### 9.7 Documentos e índice

Buscador por documento, revisión, hash, fuente o proyecto. Tabla con estado por etapa, páginas extraídas/OCR, calidad, chunks y embeddings pendientes/publicados.

Ficha con linaje: fuente → captura → archivo → revisión → extracción → chunks → índice → consultas que lo usaron, sujeto a retención. Mostrar versión de parser/OCR/modelo, permisos, retiros y errores. Las vistas de páginas y fragmentos exigen autorización; la metadata puede estar disponible sin derecho a redistribuir el original.

### 9.8 Consultas y Telegram

Lista saneada con `query_id`, intención, estado de respuesta, estado de entrega, tiempos, tokens/costo cuando existan y validación de citas. Usuario/chat se muestra pseudonimizado.

Detalle tipo cronología: update recibido, autorización, búsqueda, generación, validación y cada intento de entrega. Distinguir `insufficient_evidence`, aclaración y conflicto como resultados funcionales; no contarlos automáticamente como errores de servicio.

No presentar el historial como buzón para contactar usuarios. En el MVP solo se admite reintento de entrega inequívocamente fallida y reconciliada por el backend; un timeout ambiguo muestra estado incierto y procedimiento de diagnóstico.

### 9.9 Calidad y revisión

Colas por cambio de esquema, identidad ambigua, conflicto de fuentes, OCR, cobertura e integridad. Mostrar prioridad, edad, asignación, evidencias y decisiones previas.

El revisor puede reclamar el caso, comparar evidencia, registrar decisión/motivo y ver reprocesamiento derivado. Si otra persona ya resolvió el caso, debe actualizarse antes de aceptar una segunda decisión. Revertir significa crear una nueva decisión auditada.

### 9.10 Alertas e incidentes

Separar condición detectada, alerta deduplicada e incidente de atención. Mostrar severidad, alcance, primer/último evento, ocurrencias, responsable y procedimiento.

Acciones: reconocer, asignar, comentar, silenciar temporalmente y cerrar con motivo. Reconocer no resuelve la causa; silenciar no apaga la medición ni vuelve verde el recurso. Las reglas corren en backend aun sin sesiones abiertas. Notificaciones externas solo a destinos internos previamente configurados y autorizados, en P1.

### 9.11 Costos

Desglose por adquisición, OCR, embeddings, generación, almacenamiento y otros cargos disponibles; fuente, proveedor, modelo y período. Mostrar moneda, precio/versionado de tarifa, consumo estimado, confirmado y reservas activas.

Separar gasto acumulado de proyección. Costos no conciliados se identifican. Presupuestos de 80%/100% reflejan los controles ya especificados para el bot. El panel no es quien aplica el límite: muestra y configura el control autoritativo del backend.

### 9.12 Auditoría y configuración

Auditoría filtrable por actor, recurso, entorno, comando, cambio y resultado. Mostrar solicitud, aceptación, efecto y finalización, junto a decisiones rechazadas relevantes.

Configuración permite modificar valores admitidos por contrato: frecuencias, límites, umbrales y presupuestos. Debe mostrar valor actual, propuesto, validación e impacto. Los secretos solo tienen estado de configuración y referencia; no se revelan. Gestión de acceso requiere administrador.

## 10. Requisitos de producto verificables

P0 es obligatorio para el lanzamiento operativo. P1 puede entregarse después sin simular disponibilidad. P2 no se implementa como parte del MVP.

| ID | Prioridad | Requisito | Aceptación |
|---|---|---|---|
| PW-01 | P0 | Acceso por rol, entorno y ámbito | Usuario no autorizado no ve recursos, trazas ni acciones |
| PW-02 | P0 | Resumen operativo | Métricas con unidad, ventana, origen y fecha; enlaces al detalle |
| PW-03 | P0 | Flujos de punta a punta | Navegación desde fuente o consulta hasta trabajo/evidencia pertinente |
| PW-04 | P0 | Fuentes y frescura | Último intento/éxito/cambio separados y cobertura desconocida explícita |
| PW-05 | P0 | Seguimiento de ingestas | Etapas, contadores, estado parcial y tareas derivadas visibles |
| PW-06 | P0 | Procesos activos y workers | Vida, avance y lease diferenciados; señal vencida no aparece saludable |
| PW-07 | P0 | Colas y dead-letter | Retraso elegible, intentos, errores y reintento individual trazable |
| PW-08 | P0 | Documentos e indexación | Linaje por revisión y estado desde extracción hasta búsqueda |
| PW-09 | P0 | Consultas y entrega | Resultado del bot separado de entrega Telegram y su incertidumbre |
| PW-10 | P0 | Calidad y revisión | Decisiones con evidencia, concurrencia controlada e historia |
| PW-11 | P0 | Logs y trazas | Búsqueda por IDs, versiones y errores sin exponer secretos |
| PW-12 | P0 | Alertas e incidentes | Dedupe, asignación, silencios con vencimiento y resolución verificable |
| PW-13 | P0 | Costos y presupuesto | Estimados, confirmados y reservas sin doble conteo; límites del backend visibles |
| PW-14 | P0 | Acciones operativas individuales | Precondiciones, motivo, idempotencia y resultado comprobado |
| PW-15 | P0 | Auditoría | Cadena completa desde actor hasta efecto, incluso si falla la ejecución |
| PW-16 | P0 | Actualización en vivo confiable | Reconexión recupera estado; datos antiguos se marcan y nunca se inventan ceros |
| PW-17 | P0 | Navegación y accesibilidad | Filtros compartibles sin secretos, teclado, tabla alternativa y lectura móvil |
| PW-18 | P0 | Configuración controlada | Validación de cambios, permisos, versión previa y trazabilidad |
| PW-19 | P1 | Acciones masivas/backfill amplio | Vista previa exacta, selección congelada y resultado por recurso |
| PW-20 | P1 | Exportación y vistas guardadas | Archivos saneados con filtros/fecha, permisos y vencimiento |
| PW-21 | P1 | Evaluaciones y versiones | Comparación de recetas/modelos con corpus y resultados reproducibles |
| PW-22 | P2 | Nuevos tipos de fuente | Se habilitan solo con instrumentación y políticas de la fuente integradas |

## 11. Flujos de uso prioritarios

### 11.1 Una fuente dejó de actualizarse

El operador entra desde «Fuentes fuera de frescura», compara último intento y último éxito, abre el último run, identifica error de credencial o esquema y consulta el procedimiento. Si corrige la causa fuera del panel, puede iniciar una ingesta acotada. La alerta se recupera al observar comprobación exitosa suficiente; pulsar «reintentar» no la cierra.

### 11.2 Hay documentos acumulados en OCR

El operador abre el nodo OCR, filtra cola elegible y compara profundidad, antigüedad, capacidad de workers y progreso. Si un worker tiene señal vigente pero no avanza, la UI indica estancamiento sospechado. Solicita cancelación solo si el backend la admite; espera confirmación y entonces reintenta el trabajo. Se conserva cada intento y los derivados válidos previos.

### 11.3 El bot generó una respuesta que no llegó

El operador busca `query_id`, verifica generación y validación, y abre la bandeja de salida. Si hay error definitivo recuperable, solicita reintento de entrega usando la respuesta ya guardada. Si el proveedor pudo haber recibido el mensaje, el estado es incierto: no hay reenvío automático que duplique mensajes.

### 11.4 El costo está acercándose al límite

El administrador abre costos, identifica estimaciones pendientes y reservas por operación. Puede pausar nuevas admisiones de una clase costosa o ajustar el presupuesto con motivo y validación. Los trabajos en vuelo se muestran por separado porque podrían generar cargos ya comprometidos.

### 11.5 Un revisor corrige una identidad

El revisor abre el caso y compara evidencia, registra su decisión y observa los trabajos derivados. La ficha no se marca como «aplicada» mientras la actualización siga pendiente. Una decisión concurrente produce aviso y recarga, no una sobrescritura silenciosa.

## 12. Reglas de interacción y acciones

| Acción | Efecto esperado | Límite visible |
|---|---|---|
| Pausar fuente/cola | Impedir nuevas admisiones del alcance definido | No mata trabajos en vuelo ni elimina pendientes |
| Reanudar | Restablecer admisión bajo permisos/límites | No activa fuentes sin política validada |
| Cancelar trabajo | Solicitar parada cooperativa y confirmar resultado | Puede esperar un punto seguro o no ser soportado |
| Reintentar | Nuevo intento elegible, ligado al original | No duplica hechos ni omite restricciones externas |
| Reprocesar documento | Ejecutar receta definida sobre revisión específica | No altera original ni inventa versión legislativa |
| Resolver revisión | Registrar decisión y efectos derivados | Requiere evidencia, motivo y versión esperada |
| Cambiar configuración | Aplicar versión validada | No revela secretos ni elude presupuestos/permisos |

Las acciones de alcance individual rutinarias requieren una interacción explícita y motivo breve, sin una cadena de confirmaciones redundantes. Acciones que afectan muchos recursos, cambian presupuesto o impactan producción presentan una única revisión concreta del alcance antes de ejecutar. Esta es una regla de diseño del producto, no una solicitud de aprobación para redactar estos documentos.

## 13. Estados de interfaz y lenguaje

Cada vista debe contemplar carga, vacío real, filtro sin resultados, error, acceso denegado, datos vencidos, conexión perdida y capacidad no instrumentada. «0 pendientes» solo se muestra con medición válida; sin medición se muestra «sin datos».

Los estados deben combinar texto, icono y color. Evitar porcentajes de avance inventados y tiempos restantes sin base. Mostrar instantes absolutos además de «hace 2 minutos». El modo en vivo se puede pausar visualmente para leer una tabla sin que cambie el orden; esto no pausa ningún proceso del bot.

Diseño de escritorio como prioridad, ancho de referencia de 1.440 px; navegación y acciones esenciales accesibles desde 390 px. Tablas densas usan columnas configurables y desplazamiento, no texto ilegible. El teclado permite llegar a filtros, filas, detalle y acciones; el foco se conserva durante actualizaciones.

## 14. Indicadores y límites de interpretación

- **Trabajos activos:** jobs con intento vigente según estado durable; mostrar aparte señales vencidas.
- **Tiempo en cola:** espera de trabajo elegible, sin confundir programación futura con atraso.
- **Éxito de ejecución:** runs finalizados correctamente / runs terminales del período, con parciales separados.
- **Cobertura:** observados / esperados únicamente con inventario válido y mismo ámbito.
- **Frescura:** antigüedad de comprobación y de publicación por separado.
- **Disponibilidad:** medición externa o de servicio; nunca inferida solo por que el panel cargó.
- **Costo expuesto:** confirmados + estimados no conciliados + reservas activas sin solapamiento.
- **Tiempo de entrega:** desde respuesta lista hasta confirmación de proveedor; entrega incierta permanece aparte.

Los objetivos del bot permanecen: exactas p95 ≤5 s, híbridas ≤15 s, recepción de Telegram ≤1 s, disponibilidad mensual objetivo de 99,5% y ventanas de frescura de su SRS. El panel los muestra sin afirmar que ya se cumplen. No se calcula un p95 general promediando p95 de componentes.

## 15. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Telemetría antigua aparenta salud | Frescura por widget, estado desconocido y monitor independiente del canal |
| Exceso de polling degrada la base del bot | Proyecciones, agregados, límites y actualización incremental |
| Reintentos/cancelaciones duplican trabajo | Comandos idempotentes, estados autoritativos, leases y fencing |
| Pausa se interpreta como detención inmediata | Diferenciar admisión, trabajos en vuelo y cancelación |
| Contenido sensible en logs o trazas | Saneamiento servidor, permisos separados y auditoría de lectura |
| Alertas excesivas | Dedupe, ventanas, mínimos de muestra, recuperación e inhibición de dependencias |
| Datos legislativos se editan sin respaldo | Flujo de revisión basado en evidencia del sistema base |
| Costo presentado como definitivo | Estado estimado/confirmado, moneda y fecha de conciliación |
| Nuevos conectores sin instrumentación | Matriz de capacidades y mensaje explícito de no disponible |
| El panel cae durante una acción | Estado durable del comando y reconciliación al reconectar |

## 16. Entrega, validación y aceptación

| Hito | Entrega | Puerta de salida |
|---|---|---|
| P-H0 | Inventario de capacidades y contratos reales | Diferencias frente al SRS base identificadas |
| P-H1 | Telemetría, correlación y modelo de lectura | Flujos instrumentados y reconciliación comprobada |
| P-H2 | Panel de lectura P0 | Resumen, fuentes, runs, procesos, documentos, consultas y costos con datos reales |
| P-H3 | Acciones y revisión P0 | Permisos, concurrencia, idempotencia y auditoría probados |
| P-H4 | Alertas, seguridad y carga | Fallos/reconexión/aislamiento y no interferencia con bot aprobados |
| P-H5 | Piloto de 14 días | Metas y matriz de aceptación verificadas; procedimientos entregados |

El lanzamiento requiere todos los PW P0, cobertura del inventario P0, cero fallos críticos de acceso o integridad, pruebas de comandos y reconciliación, límites operativos configurados y responsables asignados. Una integración ausente puede mostrarse como no instrumentada durante desarrollo, pero no satisface la cobertura P0 para lanzar su flujo.

Se aceptan P1 diferidos con registro del alcance pendiente. Los dashboards con datos simulados deben llevar etiqueta visible de demostración y nunca considerarse prueba de integración.

## 17. Decisiones pendientes

| ID | Decisión | Propuesta inicial / cierre necesario |
|---|---|---|
| P-DEC-01 | Implementación real del backend | Revisar repositorio y mapear tablas/APIs antes de programar |
| P-DEC-02 | Autenticación | Proveedor corporativo o Supabase Auth; acceso privado, MFA para privilegios elevados |
| P-DEC-03 | Stack web | TypeScript/React como propuesta, adaptada al repositorio existente; sin versión impuesta |
| P-DEC-04 | Canal de actualización | SSE con snapshot y consulta periódica alternativa |
| P-DEC-05 | Proveedor de telemetría | Reutilizar infraestructura disponible; protocolo de eventos independiente |
| P-DEC-06 | Entornos y despliegue | Separación real de datos/credenciales, despliegue independiente del bot |
| P-DEC-07 | Acciones soportadas por runtime | Verificar pausa, cancelación, reintento, leases y fencing antes de habilitar |
| P-DEC-08 | Retención | Heredar límites del bot; detalle operativo inicial de 30 días según SRS del panel |
| P-DEC-09 | Presupuesto y canales de alerta | Valores y destinatarios internos por asignar; sin envíos externos implícitos |
| P-DEC-10 | Umbrales de progreso/alerta | Calibrar por tipo de job, especialmente OCR y llamadas externas |
| P-DEC-11 | Responsables de incidente | Asignar por servicio y fuente antes del piloto |

## 18. Documentos de referencia y trazabilidad

Fuentes documentales leídas: `C:\Users\Filipo\Documents\code\prd.md` y `C:\Users\Filipo\Documents\code\srs.md`. Sus instrucciones de producto se utilizan como contexto de requisitos; no autorizan por sí mismas acciones sobre producción o comunicaciones externas.

| Base | Extensión del panel |
|---|---|
| PRD-01, PRD-02 | Fuentes, ingestas, procesos y colas: PW-04 a PW-07 |
| PRD-05 | Linaje documental e índice: PW-08 |
| PRD-09, PRD-10, PRD-11 | Diagnóstico de consulta y entrega: PW-09, PW-11 |
| PRD-12 / SRS-F24, F25 | Operación y revisión: PW-10, PW-14, PW-15, PW-18 |
| PRD-13 / SRS-N01 a N06 | Acceso, minimización y separación de ámbitos: PW-01 y requisitos transversales |
| PRD-14 / SRS-F26, N14, N17 | Métricas, incidentes, costos y calidad: PW-02, PW-12, PW-13, PW-21 |

El [SRS del panel](./srs-panel-web.md) define contratos, estados, métricas, APIs, modelo operativo, pruebas y correspondencia para cada PW.
