---
name: Botcentro — Panel de operación
description: Tablero de salidas estilo split-flap para observar el pipeline del bot legislativo
colors:
  tablero-negro: "#0f0e0c"
  tablero-elevado: "#161512"
  linea-tablero: "#2a2823"
  paleta-base: "#23211d"
  paleta-superior: "#2a2824"
  costura-paleta: "#0b0a09"
  marfil: "#f1ead8"
  marfil-atenuado: "#c9c1ad"
  marfil-tenue: "#9d9684"
  verde-ok: "#5fbf73"
  verde-ok-fondo: "#17301d"
  ambar-curso: "#f2b04a"
  ambar-curso-fondo: "#3a2a11"
  rojo-fallido: "#ff6b63"
  rojo-fallido-fondo: "#3e1715"
  pizarra: "#9aa1ab"
  pizarra-fondo: "#22252a"
  cian-accion: "#5cc8f0"
  cian-accion-tinta: "#06161d"
typography:
  display:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "2rem"
    fontWeight: 600
    lineHeight: 1.05
    letterSpacing: "0.02em"
  headline:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "1.9rem"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "normal"
  title:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.02em"
  body:
    fontFamily: "Barlow, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  label:
    fontFamily: "Barlow Condensed, Arial Narrow, sans-serif"
    fontSize: "0.8rem"
    fontWeight: 500
    lineHeight: 1.2
    letterSpacing: "0.1em"
rounded:
  sm: "2px"
  md: "3px"
  lg: "4px"
  pill: "999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "24px"
  6: "32px"
  7: "48px"
components:
  button-primary:
    backgroundColor: "{colors.cian-accion}"
    textColor: "{colors.cian-accion-tinta}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "2.4rem"
  button-primary-hover:
    backgroundColor: "{colors.cian-accion}"
    textColor: "{colors.cian-accion-tinta}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.cian-accion}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "2.4rem"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.marfil-atenuado}"
    rounded: "{rounded.pill}"
    padding: "0 12px"
    height: "1.9rem"
  input-field:
    backgroundColor: "{colors.tablero-negro}"
    textColor: "{colors.marfil}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "2.9rem"
  status-ok:
    backgroundColor: "{colors.verde-ok-fondo}"
    textColor: "{colors.verde-ok}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
  status-fail:
    backgroundColor: "{colors.rojo-fallido-fondo}"
    textColor: "{colors.rojo-fallido}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
---

# Design System: Botcentro — Panel de operación

## Overview

**Creative North Star: "El tablero de salidas de una sala de operación nocturna"**

El panel no es un dashboard de tarjetas con sparklines: es un tablero de salidas (split-flap) que un operador lee de un vistazo, a lo largo de una jornada, para saber qué se mueve, qué espera, qué falló y qué no está medido. El negro del tablero (`--board`) es el color de esa sala, no una categoría de severidad: todo lo demás —paletas de estado, dígitos tabulares, condensada en mayúsculas— existe porque así se construye un tablero físico de este tipo, no como decoración retro. El único color con temperatura distinta al carbón y al hueso es el cian (`--action`), reservado en exclusiva a lo accionable; su rareza es el mecanismo, no una preferencia estética.

La densidad es alta pero legible: tablas con cabeceras fijas, tipografía condensada para lo compacto (etiquetas, celdas, cifras) y Barlow sin condensar para prosa. El sistema rechaza explícitamente kickers/eyebrows decorativos, iconografía sin texto de respaldo y cualquier tipografía "de sistema" (San Francisco/Segoe genérica) para títulos: la voz tipográfica es siempre Barlow Condensed autoalojada.

**Key Characteristics:**
- Fondo negro cálido de tablero físico, no un tema oscuro genérico.
- Paletas de estado (color + fondo emparejado) en vez de badges planos; cada paleta se parte por una costura horizontal como una ficha física.
- Dígitos y letras en celdas de ancho fijo (tabular-nums, condensada) que solo voltean ante un cambio real de valor.
- Un único cian reservado a la acción; todo lo demás es carbón, hueso o color de estado.
- Estado siempre expresado en texto + icono + color, nunca solo color.

## Colors

Paleta de tablero: negros cálidos y carbones para la estructura, hueso para el texto, y cuatro paletas de estado saturadas que solo aparecen dentro de las paletas (`.status`) y las celdas de voltea (`.flap`), nunca como fondo de página.

### Primary
- **Cian de acción** (`#5cc8f0`, tinta `#06161d`): único color reservado a lo accionable —botones primarios, enlaces, foco de teclado, chips activos—. No se usa para decorar ni para indicar estado informativo.

### Secondary — paletas de estado
- **Verde OK** (`#5fbf73` sobre fondo `#17301d`): completado / al día / en reposo sin pendientes.
- **Ámbar en curso** (`#f2b04a` sobre fondo `#3a2a11`): en curso, en espera, incierto, y también "vencido" (observación fuera de su ventana de frescura).
- **Rojo fallido** (`#ff6b63` sobre fondo `#3e1715`): fallido.
- **Pizarra rayada** (`#9aa1ab` sobre fondo rayado `#22252a`): sin datos, no instrumentado o sin acceso. El rayado diagonal (`repeating-linear-gradient` a 45°) distingue "no hay medición" de un estado real; nunca se usa para un valor cero medido.

### Neutral
- **Marfil** (`#f1ead8`): texto principal sobre el tablero negro.
- **Marfil atenuado** (`#c9c1ad`): texto secundario, subtítulos de sección.
- **Marfil tenue** (`#9d9684`): metadatos, etiquetas de campo, texto atenuado (`.muted`).
- **Negro de tablero** (`#0f0e0c`): fondo base de la app.
- **Negro elevado** (`#161512`): barra superior, tarjetas de contador, cabeceras de tabla — la superficie "un paso arriba" del fondo.
- **Línea de tablero** (`#2a2823`): todos los bordes y divisores.
- **Paleta base / superior** (`#23211d` / `#2a2824`): las dos mitades del degradado de cada celda de flap.

### Named Rules
**The One Cyan Rule.** El cian (`--action`) es el único color fuera de la gama carbón/hueso/estado permitido en la interfaz. Si un elemento no es accionable, no lleva cian.
**The Palette-Not-Badge Rule.** Los estados nunca son un punto de color aislado: son una paleta completa (fondo de estado + texto de estado + costura) igual que en `.status` y `.flap`.

## Typography

**Display Font:** Barlow Condensed (con Arial Narrow, sans-serif de reserva), autoalojada.
**Body Font:** Barlow (con system-ui, sans-serif de reserva).

**Character:** condensada grotesca en mayúsculas para todo lo estructural del tablero (títulos, celdas, etiquetas, botones, navegación); Barlow sin condensar y en minúsculas para prosa y contenido de lectura. Los números son siempre tabulares (`font-variant-numeric: tabular-nums`) para que las cifras no salten de posición al actualizarse.

### Hierarchy
- **Display** (600, 2rem, 1.05 de interlineado): `h1`, en mayúsculas, usado además como celdas de flap en los títulos de página (`.flap-title`).
- **Headline** (600, 1.9rem/`flap--lg`, interlineado 1): cifras grandes en contadores (`FlapCount` tamaño `lg`).
- **Title** (600, 1.15–1.25rem): `h2`, títulos de fila (`.row-title`).
- **Body** (400, 15px, 1.45 de interlineado): párrafos y prosa de contexto; ancho máximo 68–72ch.
- **Label** (500–600, 0.8–0.85rem, 0.1–0.12em de tracking, mayúsculas): etiquetas de campo, cabeceras de tabla, chips de navegación.

### Named Rules
**The Tabular Digits Rule.** Toda cifra que puede cambiar en vivo (contadores, columnas numéricas) usa `font-variant-numeric: tabular-nums` y celdas de ancho fijo (`FlapCount`), para que el ancho no salte al actualizarse.
**The Dash-Not-Zero Rule.** Cuando no hay medición, la celda muestra guiones (`–`) repetidos, nunca `0`. Un cero es un valor medido; un guión es la ausencia de medición.

## Layout

Grid de dos columnas fijas: riel de navegación (`--rail`, 232px) + contenido fluido, con barra superior fija a todo el ancho (`.topbar`, pegajosa). El contenido principal usa un grid de `1fr` + panel lateral de 300px (`.grid-main`) para tabla principal + "Requiere atención". La escala de espaciado es de 7 pasos (4/8/12/16/24/32/48px, `--space-1`…`--space-7`).

Responsivo en dos quiebres: a 1180px el panel lateral cae debajo del contenido principal; a 860px el layout pasa a una sola columna, el riel se vuelve una barra horizontal de scroll, y las tablas (`.board--stack`) se apilan en tarjetas por fila con etiquetas `data-label` en vez de cabeceras de columna. El ancho de referencia es escritorio (1.440px) con acceso esencial desde 390px (PRODUCT.md).

## Elevation & Depth

El sistema no usa sombras de elevación ambiental: la profundidad se transmite por capas de negro (tablero → elevado) y por sombras muy sutiles y estructurales, no decorativas. Las celdas de flap llevan una sombra de contacto mínima (`0 1px 1px rgb(0 0 0 / 0.55), 0 2px 6px rgb(0 0 0 / 0.25)`) que simula el borde físico de una ficha, y cada paleta/celda lleva una línea de costura horizontal (`::after`, 1px, `--flap-seam`) al 50% de su alto — no es una sombra, es el detalle de fabricación del tablero físico.

### Named Rules
**The Seam-Not-Shadow Rule.** La costura horizontal de las paletas (`.status::after`, `.cell::after`) es un detalle de construcción del mundo split-flap, no un efecto decorativo genérico; se reproduce en toda paleta o celda nueva, nunca como sombra proyectada arbitraria.

## Shapes

Radios pequeños y consistentes: 2px en celdas de flap, 3px (`--radius`) en botones/paletas/inputs, 4px en contenedores de tarjeta (tarjetas de contador, tablero, bloques de atención), y píldora completa (999px) solo en chips de filtro. No hay esquinas afiladas a 0 ni esquinas muy redondeadas fuera de los chips; los bordes son siempre 1px sólido en `--board-line`.

## Components

### Buttons
- **Shape:** radio 3px (`--radius`), altura 2.4rem (`--btn`) o 2rem (`.btn--sm`).
- **Primary:** fondo cian (`--action`), texto en tinta oscura (`--action-ink`), tipografía Label en mayúsculas con 0.06em de tracking.
- **Hover / Focus:** hover aclara el brillo (`filter: brightness(1.08)`); `:active` desplaza 1px hacia abajo; el foco de teclado usa el mismo cian como anillo (`outline: 2px solid var(--focus)`).
- **Ghost:** fondo transparente, texto e interior de contorno en cian (`box-shadow: inset 0 0 0 1px`); `aria-pressed="true"` añade un fondo cian traslúcido.
- **Disabled:** fondo `--flap`, texto `--ink-3`, sin filtro ni transformación — usado para acciones visibles pero deshabilitadas con motivo (P0 de solo lectura).

### Chips
- **Style:** borde 1px `--board-line`, fondo transparente, texto `--ink-2`, radio píldora (999px).
- **State:** `aria-pressed="true"` cambia borde y texto a cian con fondo cian al 10% de opacidad.

### Cards / Containers
- **Corner Style:** radio 4px.
- **Background:** `--board-raise` sobre el fondo `--board`, con borde 1px `--board-line`.
- **Shadow Strategy:** sin sombra proyectada; la separación es por color de superficie, no por elevación (ver Elevation & Depth).
- **Border:** 1px sólido `--board-line` en todos los contenedores (tarjetas de contador, tablero, bloques de atención, bloques de estado vacío).
- **Internal Padding:** `--space-3` a `--space-5` según densidad del contenedor.

### Inputs / Fields
- **Style:** fondo `--board` (más oscuro que su contenedor), borde 1px `--board-line`, radio 3px, altura 2.9rem; la variante de código (`--input-code`) usa la condensada a 1.8rem con tracking amplio para códigos de acceso.
- **Focus:** el borde cambia a cian (`--focus`) sin desplazar el layout (`outline-offset: 0`).
- **Error:** mensaje en rojo fallido (`--fail`), no en el borde del input.

### Navigation
- Riel vertical con enlaces en condensada, mayúsculas, 1.02rem; estado activo (`aria-current="page"`) recibe fondo `--flap`, texto `--ink` y un icono coloreado en ámbar (`--run`); hover usa el mismo fondo `--flap`. En móvil (≤860px) el riel se aplana en una barra horizontal con scroll.

### Paleta de estado (Status) — componente de firma
Combina siempre color de fondo + color de texto + icono Lucide + palabra completa (nunca solo un punto de color): las cuatro familias son OK (verde), en curso/vencido (ámbar), fallido (rojo) y la familia rayada (pizarra: sin datos, no instrumentado, sin acceso). Voltea (`.is-flipping`, 420ms, `cubic-bezier(0.16,1,0.3,1)`) solo cuando cambia la palabra de estado, nunca en cada render; `[data-frozen="true"]` (el control "Congelar tablero") detiene la animación de volteo sin detener el dato subyacente. Respeta `prefers-reduced-motion`.

### Celda de flap (Flap / FlapCount) — componente de firma
Texto o cifra dentro de celdas de ancho fijo con costura horizontal, condensada en mayúsculas. `FlapCount` no muestra nunca `0` cuando no hay medición: muestra guiones y una etiqueta accesible (`missing`) que explica por qué. Solo las celdas cuyo carácter cambió realmente voltean.

## Do's and Don'ts

### Do:
- **Do** reservar el cian (`--action`, `#5cc8f0`) exclusivamente para lo accionable (botones, enlaces, foco, chips activos, cursor de input).
- **Do** expresar todo estado como paleta completa: fondo de estado + texto de estado + icono Lucide + palabra, nunca color aislado.
- **Do** usar guiones (`–`) en vez de `0` cuando no hay medición, con una etiqueta accesible que explique la ausencia (sin datos / no instrumentado / sin acceso / vencido).
- **Do** voltear una celda o paleta solo cuando su valor visible cambia realmente; un control como "Congelar tablero" detiene la animación, no el dato.
- **Do** usar Barlow Condensed en mayúsculas para todo lo estructural (títulos, etiquetas, celdas, botones, navegación) y Barlow sin condensar para prosa.

### Don't:
- **Don't** introducir un segundo color de acento fuera del cian; cualquier otro color saturado pertenece a una paleta de estado, no a la decoración.
- **Don't** usar sombras proyectadas de elevación genéricas (drop shadows ambientales); la profundidad se transmite por capas de negro y por la costura horizontal de las paletas.
- **Don't** mostrar una cifra en cero cuando en realidad no hay medición o no hay acceso; eso es un valor inventado disfrazado de dato real.
- **Don't** animar el volteo de una celda que no cambió de valor, ni en el render inicial.
