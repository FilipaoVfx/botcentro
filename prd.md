# PRD — Plataforma de inteligencia legislativa y bot de consulta

| Campo | Valor |
|---|---|
| Nombre de trabajo | botRepos Legislativo Colombia |
| Documento | Product Requirements Document — requisitos de producto |
| Versión | 1.0 |
| Fecha | 2026-09-25 |
| Estado | Propuesta detallada para revisión e implementación por fases |
| Idioma principal | Español |
| Especificación complementaria | [srs.md](./srs.md) |
| Responsable de producto | Por asignar |
| Responsables técnicos y de datos | Por asignar |

## 1. Propósito y origen

Este producto convierte información legislativa dispersa de Colombia en un sistema consultable, trazable y actualizado. Su núcleo es la adquisición y organización de datos; el bot de Telegram es la primera interfaz de acceso.

El documento desarrolla el texto de investigación aportado por el solicitante, cuyo planteamiento es: fuentes → ingesta → normalización → Supabase → documentos y embeddings → recuperación → Telegram. No representa una auditoría técnica de las fuentes ni la inspección de un repositorio existente de `botRepos`.

Las fuentes y capacidades mencionadas en el insumo son candidatas. Sus endpoints, esquemas reales, condiciones de reutilización, cobertura y disponibilidad deben verificarse en la fase de descubrimiento. Los números de proyectos, gacetas, fechas, personas y precios usados como ejemplos en el insumo no se convierten aquí en hechos confirmados. Los objetivos de desempeño y capacidad de este PRD son propuestas de aceptación, no mediciones existentes.

## 2. Problema

La información necesaria para entender un proyecto de ley está distribuida entre fichas, gacetas, órdenes del día, actas, votaciones y páginas de Senado y Cámara. Un mismo proyecto puede tener identificadores diferentes en cada corporación, múltiples versiones y publicaciones que llegan con retraso.

Una búsqueda basada únicamente en similitud entre fragmentos de PDF no puede garantizar respuestas exactas a preguntas sobre conteos, votos, fechas, pertenencia a comisiones o estado del trámite. Tampoco distingue por sí sola una declaración pública de un evento legislativo confirmado.

El usuario necesita conocer qué ocurrió, cuándo, con qué evidencia y qué información falta. La plataforma debe conservar la historia documental y poder reconstruir la evidencia usada en cada respuesta.

## 3. Visión y propuesta de valor

Ofrecer un asistente legislativo que permita consultar hechos estructurados, entender documentos y recorrer la historia de proyectos con enlaces verificables. Cuando se incorpore contexto público, lo presentará en una sección diferenciada de los hechos oficiales.

Principios de producto:

1. **Evidencia antes que fluidez:** una respuesta incompleta pero sustentada es preferible a una conclusión inventada.
2. **Datos estructurados para preguntas exactas:** votos, conteos y relaciones se resuelven mediante consultas controladas a la base de datos.
3. **Documentos versionados:** conservar versiones, fechas y ubicaciones de los pasajes usados.
4. **Procedencia visible:** identificar fuente, fecha del dato, última comprobación y cobertura.
5. **Separación de autoridades:** fuentes oficiales, enriquecimiento secundario y conversación pública no se fusionan en una única verdad.
6. **Ausencia no equivale a cero:** no encontrar una votación no demuestra que no ocurrió.
7. **Cobertura declarada:** comunicar el período y los conjuntos efectivamente integrados.
8. **Operación sostenible:** controlar costos, fallos de fuentes y trabajo manual desde el inicio.

## 4. Usuarios y trabajos a resolver

| Usuario | Necesidad | Resultado esperado |
|---|---|---|
| Ciudadano interesado | Entender el avance de una iniciativa | Resumen claro, estado sustentado y documentos clave |
| Periodista | Verificar una afirmación y reconstruir una cronología | Fechas, citas, versiones y advertencias de cobertura |
| Investigador o analista | Consultar votos, participantes y cambios documentales | Resultados exactos, filtros explícitos y trazabilidad |
| Equipo de seguimiento legislativo | Revisar agenda y cambios de proyectos | Agenda actualizada y eventos vinculados a expedientes |
| Administrador de datos | Detectar fallos, duplicados e inconsistencias | Cola de revisión, historial de ingestas y correcciones auditadas |
| Operador del servicio | Mantener disponibilidad y costos | Métricas, alertas operativas y controles de ejecución |

El MVP se dirige a consultas informativas. No define perfiles ideológicos de usuarios, recomendaciones de voto ni servicios de asesoría jurídica personalizada.

## 5. Objetivos y resultados medibles

La aceptación se medirá con un corpus de evaluación congelado y una ventana piloto de 14 días. No existe una línea base de producción; se obtendrá al finalizar descubrimiento.

| ID | Objetivo | Meta propuesta y forma de medición |
|---|---|---|
| OBJ-01 | Responder hechos con precisión | ≥99% de coincidencia exacta en al menos 150 consultas estructuradas con respuesta de referencia |
| OBJ-02 | Sustentar afirmaciones | 100% de afirmaciones factuales incluidas en respuestas con referencia resoluble; ≥95% de sustentación correcta en revisión humana |
| OBJ-03 | Evitar respuestas infundadas | ≥95% de abstención o aclaración correcta en al menos 50 casos de ausencia, ambigüedad o conflicto |
| OBJ-04 | Mantener frescura visible | ≥95% de ítems nuevos del alcance publicados en fuentes sanas disponibles dentro del objetivo de su clase |
| OBJ-05 | Conservar historia | 100% de cambios de contenido detectados con hash distinto conservados como revisiones; 0 sobrescrituras destructivas en pruebas |
| OBJ-06 | Entregar una experiencia útil | ≥80% de éxito en tareas moderadas con al menos 10 usuarios piloto y 5 tareas por usuario |
| OBJ-07 | Operar con fiabilidad | Disponibilidad mensual objetivo de 99,5% de API y recepción del bot; medir aparte las caídas de proveedores |
| OBJ-08 | Controlar gasto | 100% de operaciones externas costosas asociadas a un medidor y presupuesto; ninguna ejecución nueva por encima del límite duro configurado |

Definiciones: una referencia resoluble apunta a evidencia interna existente y a su origen; sustentación correcta significa que el pasaje o registro respalda la afirmación concreta. El denominador de cobertura proviene de un inventario enumerado de la fuente, no del total desconocido de publicaciones del Congreso.

## 6. Alcance por fases

### 6.1 Fase 0 — Descubrimiento verificable

Entregables internos necesarios antes de activar conectores productivos:

- Inventario de fuentes con propietario, URL de entrada, acceso, límites y condiciones de uso.
- Muestras reales de JSON, CSV, HTML y PDF según disponibilidad.
- Identificación de paginación, fechas, IDs, rutas de documentos y señales de cambios.
- Matriz de cobertura: corporación, período, tipo de objeto y limitaciones.
- Prueba de descarga, extracción y reconciliación para ambas corporaciones.
- Estimación inicial de volúmenes, almacenamiento, OCR, embeddings y consultas.
- Corpus de prueba y decisiones registradas sobre presupuesto, retención y despliegue.

El resultado puede reducir una capacidad cuya fuente no ofrezca datos verificables. No se sustituye silenciosamente una fuente oficial por una secundaria.

### 6.2 MVP — Núcleo legislativo

La configuración inicial propuesta cubre proyectos de Senado y Cámara del período legislativo vigente al lanzamiento, más los documentos históricos necesarios para comprender expedientes activos incluidos. El período exacto se registrará con fechas explícitas durante descubrimiento; no se promete un histórico exhaustivo.

Se incluyen:

- Registro y operación de fuentes oficiales prioritarias.
- Personas, partidos, comisiones y relaciones temporales disponibles.
- Proyectos de ley y proyectos de acto legislativo como tipos diferenciados.
- Identificadores Senado/Cámara con conciliación de identidades.
- Historia de eventos con evidencia y estado observado.
- Versiones documentales: textos radicados, ponencias, textos aprobados, gacetas y actas disponibles.
- Agenda por plenaria y comisión, con cambios y cancelaciones.
- Votaciones y asistencias cuando exista evidencia suficiente para cada dato.
- Extracción de texto, OCR cuando sea necesario, búsqueda híbrida y comparación de dos versiones.
- Bot de Telegram para consultas privadas de texto en español.
- Consulta de salud, reprocesamiento y revisión de conflictos mediante interfaz administrativa mínima.
- Adaptador de enriquecimiento de Congreso Visible detrás de una bandera de activación, condicionado a validación de acceso y uso.

La Cámara se incluye desde el primer lanzamiento: al menos fichas de proyectos, relaciones entre corporaciones y documentos disponibles. La profundidad de votaciones o actas puede variar por cobertura y debe exponerse en cada conjunto.

### 6.3 Fase 2 — Contexto normativo y audiovisual

- Referencias normativas y relaciones entre normas, separadas del trámite de proyectos.
- Información sobre vigencia solo con evidencia, fecha de consulta y límites de cobertura.
- Metadata de videos oficiales y enlaces a sesiones.
- Transcripciones únicamente cuando el acceso, uso y método de obtención hayan sido aprobados para la fuente.
- Noticias mediante índices o APIs, inicialmente con título, URL, medio y fecha.

### 6.4 Fase 3 — Narrativa pública y comunidades

- Publicaciones de X mediante servicios autorizados y listas acotadas de cuentas y términos.
- Ingesta de canales o comunidades de Telegram autorizados, separada del bot de consulta.
- Comparación explícita entre afirmaciones públicas y evidencia legislativa.
- Posible seguimiento de proyectos con notificaciones opt-in, sujeto a requisitos propios antes de implementarse.

### 6.5 Fuera del MVP

- Ingesta total de redes sociales, scraping de conversaciones privadas o vigilancia individual.
- Análisis de sentimiento como indicador de apoyo político de la población.
- Predicción de aprobación de proyectos o puntuación automática de congresistas.
- Asesoría jurídica, certificación definitiva de vigencia o interpretación judicial vinculante.
- Republicación masiva de prensa, anotaciones editoriales o contenido sin permisos validados.
- Transcripción automática de todos los videos y descargas audiovisuales indiscriminadas.
- Aplicación móvil nativa, panel público analítico completo o API pública comercial.
- Neo4j obligatorio, Pinecone obligatorio o migración integral de un repositorio no inspeccionado.
- Alertas legislativas a terceros sin suscripción expresa.

## 7. Fuentes y política de incorporación

Las descripciones siguientes derivan del insumo y son hipótesis de integración por validar, no certificaciones de acceso actual.

| ID | Fuente candidata | Uso esperado | Fase | Regla de incorporación |
|---|---|---|---|---|
| SRC-01 | Senado — Datos Públicos | Personas, comisiones, agenda, votos, asistencia e intervenciones según cobertura real | MVP | Verificar API/CSV y semántica de cada campo |
| SRC-02 | Sección de Leyes del Senado | Fichas, proyectos y documentos del trámite | MVP | Mantener enlaces, IDs de origen y revisiones |
| SRC-03 | Gacetas enlazadas por fuentes oficiales | Publicaciones y documentos legislativos | MVP | Descomponer gacetas multiexpediente sin perder páginas |
| SRC-04 | Órdenes del día del Senado | Sesiones previstas y asuntos de agenda | MVP | Publicación de agenda no confirma celebración |
| SRC-05 | Actas y Relatoría del Senado | Sesiones, intervenciones y documentos | MVP | Distinguir texto disponible de transcripción completa |
| SRC-06 | Buscador Legislativo de Cámara | Fichas, números, autores, estados y documentos | MVP | Conciliar expediente entre corporaciones con evidencia |
| SRC-07 | Actas y votaciones de Cámara | Sesiones y resultados disponibles | MVP | No deducir votos individuales de un total |
| SRC-08 | Congreso Visible | Temas, sinopsis y corroboración de vínculos | MVP condicional | Enriquecimiento etiquetado; nunca reemplazo silencioso |
| SRC-09 | Base normativa Secretaría del Senado | Referencias, textos y notas según permisos | Fase 2 | Separar norma pública de contenido editorial agregado |
| SRC-10 | SUIN-Juriscol | Relaciones normativas y evidencia de vigencia | Fase 2 | Validar acceso, cobertura y términos |
| SRC-11 | YouTube | Metadata y enlaces de canales seleccionados | Fase 2 | Cuotas y acceso a subtítulos se validan aparte |
| SRC-12 | GDELT / proveedores de noticias | Índice de cobertura periodística | Fase 2 | URL y metadata por defecto; texto según derechos |
| SRC-13 | X / proveedor autorizado | Declaraciones públicas seleccionadas | Fase 3 | Presupuesto, permisos y política de borrado propios |
| SRC-14 | Telegram autorizado | Discusión de comunidades concretas | Fase 3 | Autorización registrada, aislamiento y retención definida |

Todo conector debe tener un estado: candidato, validado, activo, degradado, suspendido o retirado. Las fuentes suspendidas conservan su evidencia histórica cuando corresponda, pero no aparentan estar actualizadas.

## 8. Requisitos de producto

Prioridades: **P0** bloquea salida del MVP; **P1** mejora requerida durante el piloto salvo excepción documentada; **P2** pertenece a evolución. Cada ID se vincula con requisitos técnicos y pruebas en el SRS.

| ID | Prioridad | Requisito | Criterio de aceptación de producto |
|---|---|---|---|
| PRD-01 | P0 | Administrar fuentes y cobertura | Se puede consultar origen, estado, período cubierto, permisos y última ejecución de cada conector |
| PRD-02 | P0 | Ingerir incrementalmente sin duplicar | Reejecutar una carga idéntica no crea nuevos hechos o documentos; cambios reales conservan historia |
| PRD-03 | P0 | Unificar proyectos y actores | Dos números de corporaciones distintas se vinculan solo con evidencia; las ambigüedades se revisan |
| PRD-04 | P0 | Consultar hechos exactos | Votos, conteos, comisiones y fechas se obtienen de consultas controladas, con filtros y fuente |
| PRD-05 | P0 | Conservar y buscar documentos | Cada resultado muestra versión, fuente y página o ubicación verificable |
| PRD-06 | P0 | Reconstruir la cronología | La ficha de proyecto presenta eventos fechados y distingue agenda prevista de hechos confirmados |
| PRD-07 | P1 | Comparar versiones | El usuario selecciona dos versiones; recibe cambios vinculados a artículos/pasajes y advertencias de extracción |
| PRD-08 | P0 | Consultar agenda | La respuesta interpreta fechas en America/Bogota y muestra reprogramaciones o falta de datos |
| PRD-09 | P0 | Resolver preguntas híbridas con evidencia | El sistema combina SQL y texto cuando se requiere, conservando fuentes por afirmación |
| PRD-10 | P0 | Ofrecer bot de Telegram | Un usuario autorizado puede consultar, aclarar una referencia ambigua y abrir enlaces de evidencia |
| PRD-11 | P0 | Expresar incertidumbre y frescura | Los casos sin cobertura, con conflicto o desactualizados se identifican sin fabricar resultados |
| PRD-12 | P0 | Operar y corregir datos | Un administrador puede revisar conflictos y reprocesar; toda corrección deja un registro auditable |
| PRD-13 | P0 | Proteger acceso, privacidad y uso | Las credenciales y conversaciones no se exponen; los conectores respetan permisos registrados |
| PRD-14 | P0 | Medir calidad y costos | Existen métricas, evaluación de lanzamiento y límites efectivos de consumo |
| PRD-15 | P1 | Incorporar enriquecimiento secundario | La información secundaria está etiquetada y no sobrescribe observaciones oficiales contradictorias |
| PRD-16 | P2 | Separar discusión pública y hechos | Noticias/posts aparecen como atribuciones externas, con autor o medio y fecha |
| PRD-17 | P2 | Incorporar conocimiento normativo y video | Colecciones diferenciadas con trazabilidad, permisos y límites explícitos |

## 9. Flujos principales

### 9.1 Estado de un proyecto

1. Usuario pregunta: «¿Qué está pasando con el PL 249?».
2. El sistema busca coincidencias por número, tipo, período y corporación.
3. Si hay varias, ofrece opciones breves: número, año, corporación y título. No elige por popularidad.
4. Tras resolver identidad, obtiene el último estado sustentado y la cronología relevante.
5. Responde con estado legislativo, últimos eventos, documentos, fecha de actualización y fuentes.
6. Si la fuente no se ha podido consultar recientemente, lo indica. Si hay estados incompatibles, presenta la discrepancia.

### 9.2 Voto de una persona

1. Usuario identifica persona y asunto; el bot pide precisión si existen votaciones diferentes.
2. Se resuelven persona, sesión y objeto de votación: proyecto, artículo, proposición u otro.
3. Una consulta estructurada obtiene el registro nominal y su evidencia.
4. La respuesta muestra voto tal como fue normalizado y, cuando sea útil, el valor original.
5. Si solo hay totales, informa que no hay registro nominal disponible. Ausencia de registro, ausencia a sesión, abstención e impedimento son categorías distintas.

### 9.3 Agenda relativa

1. Usuario pregunta: «¿Qué se discutirá mañana?».
2. El sistema convierte «mañana» a una fecha de Colombia y la muestra explícitamente.
3. Consulta agenda de corporaciones y órganos dentro de la cobertura.
4. Distingue programado, aplazado y cancelado; una programación no se describe como debate realizado.
5. Si no hay entradas, dice que no encontró programación en las fuentes cubiertas, evitando afirmar que no habrá sesiones.

### 9.4 Comparación documental

1. Usuario solicita comparar texto radicado y texto aprobado de un expediente.
2. Si existen varias aprobaciones, selecciona etapa y documento.
3. El sistema presenta fecha y naturaleza de ambas versiones.
4. Genera comparación por estructura documental y cita ambas ubicaciones.
5. Separa cambio textual de interpretación del efecto. Los pasajes con OCR deficiente se excluyen o se marcan para revisión.

### 9.5 Corrección operativa

1. El sistema detecta un cambio de esquema, una entidad ambigua o datos incompatibles.
2. Abre un caso en la cola de revisión con evidencia y ejecuciones relacionadas.
3. El administrador selecciona una resolución justificada o mantiene el conflicto.
4. Se registra quién decidió, cuándo y por qué, preservando el dato original.
5. Se reprocesan solo los derivados afectados y se invalidan respuestas en caché pertinentes.

## 10. Experiencia de respuesta

La respuesta debe ser legible en Telegram y comenzar por la información solicitada. Las secciones se incluyen cuando aportan contenido; no se imprime una plantilla vacía.

Formato orientativo:

> **Proyecto identificado:** número, período, corporación y título abreviado.  
> **Estado legislativo:** último estado respaldado y fecha del evento.  
> **Qué ocurrió:** hasta cinco hitos pertinentes.  
> **Documentos o votaciones:** resultados relevantes con referencias.  
> **Limitaciones:** falta de cobertura, datos antiguos o discrepancias.  
> **Fuentes:** enlaces y, para documentos, páginas o secciones.  
> **Actualización:** última comprobación exitosa de las fuentes utilizadas.

En fases posteriores, «Discusión pública» se presenta aparte y atribuye cada declaración a su origen. El bot no llama «hecho legislativo» a una noticia ni trata un resumen generado como una nueva fuente.

Comandos propuestos: `/start`, `/help`, `/proyecto`, `/agenda`, `/fuentes` y `/privacidad`. El lenguaje natural es el canal principal. Los mensajes extensos se dividen sin cortar una referencia; el detalle adicional puede recuperarse mediante botones o enlaces.

El piloto funciona en chats privados con usuarios autorizados. El acceso público o la operación en grupos requiere configuración adicional de límites, privacidad y permisos.

## 11. Política de calidad de datos

- Cada observación factual debe tener origen, fecha de captura y evidencia.
- Un mismo documento puede pertenecer a varios proyectos, especialmente una gaceta.
- El estado actual se deriva de observaciones respaldadas; no del archivo con la fecha de descarga más reciente.
- Se conservan la fecha del hecho, la fecha de publicación y la fecha de observación como conceptos distintos.
- La autoridad de una fuente no garantiza que sea completa o esté actualizada.
- No se mezclan legislaturas ni afiliaciones de personas de períodos distintos.
- Un cambio de partido no modifica retrospectivamente la afiliación de una votación histórica.
- Las correcciones y retiros en origen generan nuevas observaciones o marcas de retiro, no borrados indiscriminados.
- Las relaciones inferidas se identifican como propuestas hasta superar la regla de validación.
- La cobertura nominal de votos se informa separadamente de la cobertura de resultados agregados.

## 12. Frescura, servicio y capacidad

Objetivos iniciales, sujetos a viabilidad de fuentes:

| Clase | Frecuencia objetivo de consulta | Disponibilidad interna desde publicación observable |
|---|---|---|
| Agenda durante ventanas de actividad configuradas | Cada 30 minutos | ≤2 horas en ≥95% de casos con fuente sana |
| Proyectos, eventos y votaciones | Cada 6 horas | ≤12 horas en ≥95% de casos con fuente sana |
| Documentos y actas | Cada 6 horas | ≤24 horas, incluida extracción, en ≥95% de casos |
| Personas, partidos y comisiones | Diaria | ≤48 horas |
| Enriquecimiento secundario | Diaria | ≤48 horas |

Los objetivos se miden desde que un ítem es públicamente observable y recuperable, no desde la fecha del evento. Si esa hora no existe, se mide desde la primera detección y se etiqueta la limitación. Respetar restricciones de la fuente prevalece sobre aumentar la frecuencia.

Sobre datos ya indexados y en la carga piloto, la meta p95 es ≤5 segundos para consultas exactas y ≤15 segundos para respuestas híbridas. Las comparaciones pesadas pueden ser asíncronas, con confirmación de recepción y consulta de resultado. La entrega final de Telegram se mide aparte de la latencia interna.

El entorno de aceptación propuesto soporta 10 solicitudes concurrentes, un corpus de 100.000 revisiones documentales y hasta 1.000.000 de chunks. Son envolventes de prueba para presupuestar; no estimaciones del tamaño real de las fuentes.

## 13. Operación, costos y sostenibilidad

La plataforma debe mostrar, por fuente y período: ítems detectados, descargados, normalizados, publicados, rechazados, pendientes y fallidos; retraso de actualización; costo de OCR, embeddings y generación; y cantidad de revisiones manuales.

Se establecen presupuestos configurables diarios y mensuales. Al 80% se emite alerta operativa y al 100% se detienen nuevos trabajos opcionales o costosos según política. Consultas SQL y evidencia almacenada continúan disponibles cuando sea posible. No se fijan precios de proveedores a partir de ejemplos del insumo.

El costo se estima con la fórmula: infraestructura fija + almacenamiento y transferencia + páginas OCR + tokens de embeddings + tokens de generación + servicios de adquisición. El lanzamiento requiere completar tarifas verificadas, volúmenes y presupuesto aprobado.

## 14. Riesgos y mitigaciones

| Riesgo | Consecuencia | Mitigación y señal de control |
|---|---|---|
| Endpoint inexistente o cobertura menor a la esperada | Funciones sin datos | Descubrimiento con muestras; matriz visible de cobertura |
| Cambios de HTML o esquemas | Pérdida o interpretación incorrecta | Contratos de parser, alarmas de volumen, cuarentena |
| OCR deficiente | Citas o comparaciones equivocadas | Calidad por página; bloqueo de afirmaciones no verificables |
| Identificadores ambiguos | Mezcla de expedientes | Claves por corporación/período/tipo y revisión de vínculos |
| Publicaciones tardías | Estado aparente desactualizado | Fechas separadas y comprobación visible |
| Discrepancia entre fuentes | Respuesta contradictoria | Conservar observaciones y exponer conflicto |
| Contenido sin permiso suficiente | Restricción de uso | Registro de permisos por operación antes de activación |
| Instrucciones maliciosas en documentos | Uso indebido del agente | Contenido tratado como datos; herramientas permitidas y contexto aislado |
| Consumo excesivo | Servicio inviable | Deduplicación, caché, límites y modelos intercambiables |
| Dependencia de un proveedor | Interrupciones o migración costosa | Adaptadores y datos canónicos independientes del índice |
| Datos de comunidades futuras | Exposición entre usuarios | Aislamiento por ámbito y filtros antes de recuperación |

## 15. Hitos y puertas de salida

No se asignan fechas de calendario sin conocer equipo, presupuesto y calidad de las fuentes.

| Hito | Entrega | Puerta de salida |
|---|---|---|
| H0 — Descubrimiento | Fuentes verificadas, muestras y decisiones | Fuentes núcleo viables; limitaciones y costo inicial documentados |
| H1 — Fundaciones | Esquema, almacenamiento, cola y trazabilidad | Cargas repetibles, respaldo y roles comprobados |
| H2 — Núcleo de datos | Senado + Cámara + documentos + agenda | Identidades, versiones y consultas exactas pasan evaluación |
| H3 — Recuperación y bot | SQL, búsqueda, citas y Telegram | Pruebas de respuesta, ambigüedad y aislamiento superadas |
| H4 — Piloto | Usuarios piloto y operación durante 14 días | Metas P0 cumplidas, fallos graves resueltos y P1 evaluados |
| H5 — Evolución | Normativa, medios y comunidades | Cada nueva fuente supera su propia revisión y evaluación |

P0 es obligatorio para lanzamiento. PRD-07 y PRD-15 pueden diferirse mediante decisión del responsable de producto; se comunicarán como no disponibles y no se incluirán en la promesa pública del MVP. PRD-16 y PRD-17 no bloquean el núcleo.

## 16. Criterios de aceptación del MVP

- Todos los requisitos P0 tienen prueba asociada aprobada en el SRS.
- Existe al menos un recorrido de punta a punta por Senado y por Cámara: adquisición → evidencia → normalización → consulta → cita.
- Los conjuntos que no puedan integrarse aparecen como no cubiertos; el responsable de producto aprueba explícitamente cualquier reducción del alcance prometido.
- El corpus de evaluación contiene casos exactos, semánticos, ambiguos, sin datos, contradictorios y adversariales.
- No quedan incidencias críticas de exposición de datos, pérdida de versiones, mezcla de proyectos o fabricación de evidencia.
- Se han probado restauración, reprocesamiento, caída de fuente y agotamiento de presupuesto.
- Se puede reconstruir una respuesta a partir de su ID de trazabilidad y evidencias.
- Las credenciales, responsables operativos, presupuestos y políticas de retención están configurados.
- El piloto cumple las metas OBJ-01 a OBJ-08 o registra una excepción aprobada que no rebaja controles críticos de evidencia y aislamiento.

## 17. Decisiones y preguntas abiertas

| ID | Decisión pendiente | Valor propuesto | Momento límite |
|---|---|---|---|
| DEC-01 | Audiencia y acceso | Piloto privado con lista de usuarios autorizados | Antes de habilitar Telegram |
| DEC-02 | Período y cobertura inicial | Período vigente al lanzamiento + antecedentes de expedientes incluidos | H0 |
| DEC-03 | Fuentes y endpoints reales | Descubrimiento por fuente; no asumir API uniforme | H0 |
| DEC-04 | Infraestructura y región | Supabase/PostgreSQL, almacenamiento privado y workers separados | H1 |
| DEC-05 | Índice vectorial | pgvector si satisface benchmark; motor externo solo con justificación | Antes de H3 |
| DEC-06 | LLM, embeddings y OCR | Adaptadores, selección por evaluación y costo | Antes de H3 |
| DEC-07 | Presupuesto diario/mensual | Por asignar; producción costosa deshabilitada mientras falte | Antes de ingesta pagada |
| DEC-08 | Retención y tratamiento de consultas | Valores iniciales del SRS, sujetos a aprobación del responsable | Antes del piloto |
| DEC-09 | Responsables y escalamiento | Producto, datos, ingeniería y operación designados | H0 |
| DEC-10 | Condiciones de cada fuente | Registro por descarga, almacenamiento, indexación y redistribución | Antes de activar cada conector |
| DEC-11 | Integración real con botRepos | Revisar repositorio y contratos existentes cuando se proporcione acceso | Antes de modificar código existente |
| DEC-12 | Comparación y enriquecimiento P1 | Incluidos en piloto salvo excepción documentada | H4 |

Estos pendientes no impiden entregar los requisitos, pero sí condicionan las partes correspondientes de la implementación y el lanzamiento.

## 18. Referencias del insumo

Enlaces aportados como puntos de partida; no fueron verificados en vivo para redactar estos documentos:

- [Senado — Datos Públicos](https://app.senado.gov.co/open_data/)
- [Senado — Sección de Leyes](https://leyes.senado.gov.co/)
- [Cámara — Buscador Legislativo](https://www.camara.gov.co/buscador-legislativo/)
- [Cámara — Actas y votaciones](https://www.camara.gov.co/secretaria-general/actas-votaciones-y-otros/)
- [Congreso Visible](https://congresovisible.uniandes.edu.co/)
- [Secretaría del Senado — Base normativa](https://www.secretariasenado.gov.co/senado/basedoc/)
- [SUIN-Juriscol](https://www.suin-juriscol.gov.co/)
- [GDELT DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
- [YouTube Data API](https://developers.google.com/youtube/v3/getting-started)

