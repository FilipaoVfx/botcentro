-- Catálogo inicial de referencia (PRD §7, §18). Idempotente.
--
-- Todas las fuentes quedan como 'candidate': sus endpoints, esquemas, cobertura y condiciones de
-- uso NO están verificados (PRD §1, §7). Pasar a 'validated'/'active' exige, en la fase de
-- descubrimiento (H0), un perfil de uso revisado (admin_add_source_policy), cobertura declarada,
-- dominios permitidos, adaptador versionado y responsable; el trigger enforce_source_activation
-- lo impide en otro caso (T-01).
--
-- Aplicar con aprobación explícita del responsable de datos:
--   npx -y @insforge/cli db query "$(grep -v '^--' seeds/catalogo_inicial.sql)"

insert into public.corporations (code, name) values
  ('senado', 'Senado de la República'),
  ('camara', 'Cámara de Representantes')
on conflict (code) do nothing;

insert into public.sources (code, name, authority, phase, base_url, allowed_domains, supported_objects, notes) values
  ('SRC-01', 'Senado — Datos Públicos', 'primary', 'mvp', 'https://app.senado.gov.co/open_data/',
   '{senado.gov.co}', '{person,commission,agenda,voting,attendance,intervention}',
   'Verificar API/CSV y semántica de cada campo. URL del insumo, no verificada en vivo.'),
  ('SRC-02', 'Senado — Sección de Leyes', 'primary', 'mvp', 'https://leyes.senado.gov.co/',
   '{senado.gov.co}', '{project,document}',
   'Mantener enlaces, IDs de origen y revisiones. URL del insumo, no verificada en vivo.'),
  ('SRC-03', 'Gacetas enlazadas por fuentes oficiales', 'primary', 'mvp', null,
   '{}', '{document,gaceta}', 'Descomponer gacetas multiexpediente sin perder páginas. Entrada por definir en H0.'),
  ('SRC-04', 'Senado — Órdenes del día', 'primary', 'mvp', null,
   '{}', '{agenda}', 'La publicación de agenda no confirma la celebración. Entrada por definir en H0.'),
  ('SRC-05', 'Senado — Actas y Relatoría', 'primary', 'mvp', null,
   '{}', '{session,document,intervention}', 'Distinguir texto disponible de transcripción completa. Entrada por definir en H0.'),
  ('SRC-06', 'Cámara — Buscador Legislativo', 'primary', 'mvp', 'https://www.camara.gov.co/buscador-legislativo/',
   '{camara.gov.co}', '{project,document}',
   'Conciliar expediente entre corporaciones con evidencia. URL del insumo, no verificada en vivo.'),
  ('SRC-07', 'Cámara — Actas y votaciones', 'primary', 'mvp',
   'https://www.camara.gov.co/secretaria-general/actas-votaciones-y-otros/',
   '{camara.gov.co}', '{session,voting,document}',
   'No deducir votos individuales de un total. URL del insumo, no verificada en vivo.'),
  ('SRC-08', 'Congreso Visible', 'secondary', 'mvp_conditional', 'https://congresovisible.uniandes.edu.co/',
   '{congresovisible.uniandes.edu.co}', '{topic,synopsis}',
   'Enriquecimiento etiquetado; nunca reemplazo silencioso. Condicionado a validar acceso y uso.'),
  ('SRC-09', 'Secretaría del Senado — Base normativa', 'normative', 'phase_2',
   'https://www.secretariasenado.gov.co/senado/basedoc/', '{secretariasenado.gov.co}', '{law}',
   'Separar norma pública de contenido editorial agregado.'),
  ('SRC-10', 'SUIN-Juriscol', 'normative', 'phase_2', 'https://www.suin-juriscol.gov.co/',
   '{suin-juriscol.gov.co}', '{law,law_relation}', 'Validar acceso, cobertura y términos.'),
  ('SRC-11', 'YouTube (canales seleccionados)', 'primary', 'phase_2', null,
   '{}', '{video}', 'Metadata y enlaces; cuotas y subtítulos se validan aparte.'),
  ('SRC-12', 'GDELT / proveedores de noticias', 'public_discourse', 'phase_2', null,
   '{}', '{news}', 'URL y metadata por defecto; texto según derechos.'),
  ('SRC-13', 'X / proveedor autorizado', 'public_discourse', 'phase_3', null,
   '{}', '{social_post}', 'Presupuesto, permisos y política de borrado propios.'),
  ('SRC-14', 'Telegram (comunidades autorizadas)', 'public_discourse', 'phase_3', null,
   '{}', '{community_message}', 'Autorización registrada, aislamiento y retención definida. Servicio distinto del bot.')
on conflict (code) do nothing;
