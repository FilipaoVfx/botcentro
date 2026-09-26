---
version: 1
slug: "panel-src-app-tsx"
primary_target: "panel/src/App.tsx"
related_targets: []
---

# Panel de operación — brief de superficie

Modo: Operate. Público: responsable del proyecto (admin) antes de la primera ingesta; después, operadores, revisores e ingenieros. Tarea: saber de un vistazo qué se mueve, qué espera, qué falló, qué no está medido, y bajar a la fila que lo explica. Restricciones: solo lectura en esta iteración (P-H2); acciones visibles pero deshabilitadas con su motivo; nada inventado.

## Direction contract

THESIS: El panel es un tablero de salidas del trabajo del bot: cada etapa, fuente, ejecución y cola es una fila cuya paleta de estado solo voltea cuando cambia el estado durable. Rechaza la cuadrícula de tarjetas KPI con sparklines.

OWN-WORLD: Fondo negro de tablero cálido, celdas de paleta carbón partidas por una línea horizontal, letras marfil en condensada grotesca de ancho fijo, dígitos tabulares de posiciones fijas. Estados como paletas de color: ámbar EN CURSO/DEMORADO, rojo señal FALLIDO, verde solo COMPLETADO/AL DÍA, pizarra rayada SIN DATOS y NO INSTRUMENTADO. Un único cian reservado a lo accionable. Leyenda de etapas siempre nivelada.

STORY: El operador ve el tablero, detecta la fila que no está al día, la abre y encuentra la ejecución, el trabajo o el registro que la explica, con la hora en que se observó.

FIRST VIEWPORT: Barra superior con placa de entorno, reloj de Bogotá en paletas, estado de conexión y «observado a». Centro: tablero «Salidas» con una fila por etapa del pipeline (paletas: pendientes, en curso, fallidos, último movimiento, estado). Derecha: «Requiere atención». Riel izquierdo de navegación.

FORM: Tablero de salidas split-flap, posición 5 de la lista propia, semilla 10a5cac5. Aportes: dígitos de posición fija (contador nixie); leyenda nivelada (jardín de gravedad); color de acción único (app de consumo). Interacción firma: volteo de paleta solo ante cambios reales llegados por SSE; «Congelar tablero» detiene la animación, no el bot.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
