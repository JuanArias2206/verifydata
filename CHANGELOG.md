# Changelog — VerifyData Demo

## [v1.0-demo] — 2026-07-17

### Cambios respecto a la versión original MinTrace

- **Rebrand completo**: Cuantico Corporation SAS / MinTrace → VerifyData
  (nombre neutral, wordmark de texto, sin logos de marca).
- **Fuentes mineras eliminadas**: ANM (Agencia Nacional de Minería) y
  RUCOM (Registro Único de Comercializadores de Minerales) removidos
  por completo.
- **Secretos limpiados**: todas las API keys reales eliminadas del
  paquete; reemplazadas por placeholders en `.env.example` y
  `config.example.yaml`.
- **Estructura reorganizada**: el contenido de `demo/` subió a la raíz;
  archivos sueltos e informes pesados (docx, PDFs) eliminados.
- **Datos de runtime limpiados**: `verifydata.db`, screenshots, caches
  y `__pycache__` removidos (~290 MB → ~2 MB).
- **Docs reescritas**: README, AGENTS.md, API.md, DEPLOYMENT.md
  actualizadas a VerifyData.
- **NIT de entidad consultante**: ahora configurable por entorno
  (`VERIFYDATA_ENTITY_NIT` / `VERIFYDATA_ENTITY_NAME`) con valores
  placeholder por defecto.

---

_Maintained by VerifyData · 2026_
