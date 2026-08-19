"""
rsales_client.py — Cliente Python para la API de RSales (ventasremotas.com).

Extrae datos comerciales/financieros de clientes:
  - /customers    — lista de clientes (nombre, NIT, ciudad, estado)
  - /receivables  — cartera / cuentas por cobrar (saldos, fechas de vencimiento)
  - /orders       — pedidos y facturas (montos, fechas, estados)
  - /sellers      — vendedores

Uso:
    from rsales_client import RsalesClient
    client = RsalesClient()
    customers = client.get_all_customers()
    cartera   = client.get_all_receivables()
"""
from __future__ import annotations

import os
import time
import logging
import threading
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import requests

log = logging.getLogger("verifydata.rsales")

RSALES_BASE = os.environ.get("RSALES_BASE_URL", "https://api.ventasremotas.com/v1")
RSALES_API_KEY = os.environ.get("RSALES_API_KEY", "")
RSALES_CLIENT_ID = os.environ.get("RSALES_CLIENT_ID", "")
RSALES_CLIENT_SECRET = os.environ.get("RSALES_CLIENT_SECRET", "")

REQUEST_TIMEOUT = 45
MAX_RETRIES = 3
RETRY_BACKOFF = 1.5  # seconds base


class RsalesAuthError(Exception):
    """Error de autenticación con RSales."""


class RsalesAPIError(Exception):
    """Error de la API de RSales."""


class RsalesClient:
    """Cliente autenticado para la API de RSales."""

    def __init__(
        self,
        api_key: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
    ):
        self.api_key = api_key or RSALES_API_KEY
        self.client_id = client_id or RSALES_CLIENT_ID
        self.client_secret = client_secret or RSALES_CLIENT_SECRET
        self.base_url = (base_url or RSALES_BASE).rstrip("/")
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ── Auth ─────────────────────────────────────────────────
    def _authenticate(self) -> str:
        """Obtiene un token JWT OAuth2 de RSales. Cachea 55 min."""
        if self._token and time.time() < self._token_exp:
            return self._token

        if not all([self.api_key, self.client_id, self.client_secret]):
            raise RsalesAuthError(
                "Credenciales RSales no configuradas. Define RSALES_API_KEY, "
                "RSALES_CLIENT_ID y RSALES_CLIENT_SECRET en .env"
            )

        url = f"{self.base_url}/token"
        headers = {
            "Subscription-Key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise RsalesAuthError(
                    f"RSales auth falló ({resp.status_code}): {resp.text[:300]}"
                )
            data = resp.json()
            self._token = data.get("access_token")
            if not self._token:
                raise RsalesAuthError("RSales no devolvió access_token")
            # expires_in suele ser 3600s; cache 55 min con margen
            expires_in = data.get("expires_in", 3600)
            self._token_exp = time.time() + min(expires_in - 300, 3300)
            return self._token
        except requests.RequestException as e:
            raise RsalesAuthError(f"No se pudo conectar a RSales: {e}") from e

    # ── HTTP helper ──────────────────────────────────────────
    def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET autenticado a la API de RSales con retry/backoff."""
        token = self._authenticate()
        url = f"{self.base_url}{path}"
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urlencode(filtered)

        headers = {
            "Subscription-Key": self.api_key,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 429:
                    # Rate limit — esperar y reintentar
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    log.warning("RSales 429 en %s, esperando %.1fs", path, wait)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    raise RsalesAPIError(
                        f"RSales {path} error ({resp.status_code}): {resp.text[:400]}"
                    )
                return resp.json()
            except requests.RequestException as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    log.warning("RSales %s fallo (intento %d/%d), retry en %.1fs: %s",
                                path, attempt + 1, MAX_RETRIES, wait, e)
                    time.sleep(wait)
                    continue
                raise RsalesAPIError(
                    f"RSales {path} request falló tras {MAX_RETRIES} intentos: {e}"
                ) from e
        raise RsalesAPIError(f"RSales {path} request falló: {last_err}")

    def _get_all_paged(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        limit: int = 1000,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Obtiene todos los registros paginando automáticamente."""
        all_items: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            p = dict(params or {})
            p["page"] = page
            p["limit"] = limit
            try:
                data = self._get(path, p)
            except RsalesAPIError:
                break
            items = data.get("data") or []
            if not items:
                break
            all_items.extend(items)
            meta = data.get("meta") or {}
            total = meta.get("totalItems", 0)
            if len(all_items) >= total:
                break
            page += 1
        return all_items

    # ── Endpoints públicos ───────────────────────────────────

    def get_customers(
        self, page: int = 1, limit: int = 1000, code: str | None = None,
        name: str | None = None
    ) -> dict[str, Any]:
        """Clientes (paginado)."""
        return self._get("/customers", {"page": page, "limit": limit,
                                        "code": code, "name": name})

    def get_all_customers(self) -> list[dict[str, Any]]:
        """Todos los clientes."""
        return self._get_all_paged("/customers")

    def get_receivables(
        self, page: int = 1, limit: int = 1000,
        client_code: str | None = None,
        seller_code: str | None = None,
        document_type: str | None = None,
        created_from: str | None = None,
        modified_from: str | None = None,
    ) -> dict[str, Any]:
        """Cartera / cuentas por cobrar (paginado)."""
        return self._get("/receivables", {
            "page": page, "limit": limit,
            "client_code": client_code, "seller_code": seller_code,
            "document_type": document_type,
            "created_from": created_from, "modified_from": modified_from,
        })

    def get_all_receivables(
        self, client_code: str | None = None,
        seller_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Toda la cartera (paginación automática)."""
        params: dict[str, Any] = {}
        if client_code:
            params["client_code"] = client_code
        if seller_code:
            params["seller_code"] = seller_code
        return self._get_all_paged("/receivables", params)

    def get_orders(
        self, page: int = 1, limit: int = 500,
        client_code: str | None = None,
        seller_code: str | None = None,
        state: str | None = None,
        created_from: str | None = None,
    ) -> dict[str, Any]:
        """Pedidos / órdenes (paginado)."""
        return self._get("/orders", {
            "page": page, "limit": limit,
            "client_code": client_code, "seller_code": seller_code,
            "state": state, "created_from": created_from,
        })

    def get_all_orders(
        self, client_code: str | None = None,
        created_from: str | None = None,
    ) -> list[dict[str, Any]]:
        """Todos los pedidos."""
        params: dict[str, Any] = {}
        if client_code:
            params["client_code"] = client_code
        if created_from:
            params["created_from"] = created_from
        return self._get_all_paged("/orders", params, limit=500)

    def get_all_sellers(self) -> list[dict[str, Any]]:
        """Todos los vendedores."""
        return self._get_all_paged("/sellers")

    def get_appointments(
        self, day_from: str | None = None, page: int = 1, limit: int = 500
    ) -> dict[str, Any]:
        """Citas / visitas (paginado)."""
        return self._get("/appointments", {
            "day_from": day_from, "page": page, "limit": limit,
        })

    def get_all_appointments(
        self, day_from: str | None = None
    ) -> list[dict[str, Any]]:
        """Todas las citas desde una fecha."""
        return self._get_all_paged(
            "/appointments", {"day_from": day_from}, limit=500
        )

    def get_managements(
        self, client_code: str | None = None,
        seller_code: str | None = None,
        date_from: str | None = None,
    ) -> list[dict[str, Any]]:
        """Historial de gestión / visitas a clientes."""
        params: dict[str, Any] = {}
        if client_code:
            params["client_code"] = client_code
        if seller_code:
            params["seller_code"] = seller_code
        if date_from:
            params["date_from"] = date_from
        return self._get_all_paged("/managements", params)

    # ── Métodos de alto nivel para perfil crediticio ─────────

    def get_customer_financial_profile(
        self, client_code: str
    ) -> dict[str, Any]:
        """Perfil financiero completo de un cliente desde RSales.

        Retorna:
            - datos del cliente (nombre, NIT, ciudad, email, teléfono)
            - cartera: total, vencida, corriente, documentos, aging buckets
            - pedidos: total compras, promedio, frecuencia, último pedido
            - histórico de gestión (visitas)
        """
        # ── Datos básicos ──
        customers = self._get_all_paged(
            "/customers", {"code": client_code}, limit=10
        )
        customer = None
        for c in customers:
            cid = c.get("code") or c.get("client_code") or ""
            if cid == client_code:
                customer = c
                break

        if not customer:
            # Usar el índice cacheado en vez de cargar todos los clientes
            try:
                from rsales_client import _get_customer_index
                idx = _get_customer_index()
                match = idx.get(client_code)
                if match:
                    code = match.get("code", client_code)
                    customers = self._get_all_paged(
                        "/customers", {"code": code}, limit=10
                    )
                    for c in customers:
                        cid = c.get("code") or c.get("client_code") or ""
                        if cid == code:
                            customer = c
                            break
            except Exception:
                pass

        if not customer:
            return {"error": f"Cliente {client_code} no encontrado en RSales",
                    "client_code": client_code}

        # ── Cartera ──
        try:
            receivables = self.get_all_receivables(client_code=client_code)
        except Exception:
            receivables = []

        cartera_total = sum(r.get("balance", 0) or 0 for r in receivables)
        now = datetime.now()
        cartera_vencida = 0.0
        cartera_corriente = 0.0
        aging_buckets: dict[str, float] = {
            "corriente": 0, "1_30": 0, "31_60": 0,
            "61_90": 0, "91_180": 0, "181_360": 0, "mas_360": 0,
        }
        documentos_vencidos = 0

        for r in receivables:
            balance = r.get("balance", 0) or 0
            due_str = r.get("due_date") or r.get("expiration_date") or ""
            if due_str:
                try:
                    due_date = datetime.fromisoformat(
                        due_str.replace("Z", "+00:00")
                    )
                    dias_vencido = (now - due_date.replace(tzinfo=None)).days
                    if dias_vencido > 0:
                        cartera_vencida += balance
                        documentos_vencidos += 1
                    else:
                        cartera_corriente += balance
                    # Aging buckets
                    if dias_vencido <= 0:
                        aging_buckets["corriente"] += balance
                    elif dias_vencido <= 30:
                        aging_buckets["1_30"] += balance
                    elif dias_vencido <= 60:
                        aging_buckets["31_60"] += balance
                    elif dias_vencido <= 90:
                        aging_buckets["61_90"] += balance
                    elif dias_vencido <= 180:
                        aging_buckets["91_180"] += balance
                    elif dias_vencido <= 360:
                        aging_buckets["181_360"] += balance
                    else:
                        aging_buckets["mas_360"] += balance
                except (ValueError, TypeError):
                    cartera_corriente += balance
                    aging_buckets["corriente"] += balance
            else:
                cartera_corriente += balance
                aging_buckets["corriente"] += balance

        # ── Pedidos ──
        try:
            orders = self.get_all_orders(client_code=client_code)
        except Exception:
            orders = []

        total_compras = sum(
            (o.get("total") or o.get("subtotal") or 0) for o in orders
        )
        num_pedidos = len(orders)
        promedio_pedido = total_compras / num_pedidos if num_pedidos > 0 else 0

        # Último pedido
        ultimo_pedido_fecha: str | None = None
        ultimo_pedido_monto: float = 0
        if orders:
            sorted_orders = sorted(
                orders,
                key=lambda o: o.get("created_at") or o.get("date") or "",
                reverse=True,
            )
            ultimo = sorted_orders[0]
            ultimo_pedido_fecha = (
                ultimo.get("created_at") or ultimo.get("date") or ""
            )
            ultimo_pedido_monto = ultimo.get("total") or ultimo.get("subtotal") or 0

        # Frecuencia de compra (meses entre pedidos)
        frecuencia_meses: float | None = None
        if len(orders) >= 2:
            dates = []
            for o in orders:
                d = o.get("created_at") or o.get("date") or ""
                try:
                    dates.append(
                        datetime.fromisoformat(d.replace("Z", "+00:00"))
                    )
                except (ValueError, TypeError):
                    pass
            dates.sort()
            if len(dates) >= 2:
                delta = (dates[-1] - dates[0]).days
                frecuencia_meses = round(delta / (len(dates) - 1) / 30, 1)

        # ── Gestión ──
        try:
            managements = self.get_managements(
                client_code=client_code,
                date_from=(datetime.now().replace(
                    year=datetime.now().year - 1
                ).strftime("%Y-%m-%d")),
            )
        except Exception:
            managements = []

        gestiones_12m = len(managements)

        # ── Armar perfil ──
        return {
            "client_code": client_code,
            "nombre": customer.get("name") or customer.get("business_name", ""),
            "nit": customer.get("nit") or customer.get("identification", ""),
            "ciudad": customer.get("city", ""),
            "direccion": customer.get("address", ""),
            "telefono": customer.get("phone", ""),
            "email": customer.get("email", ""),
            "estado": customer.get("state", ""),
            "es_persona_juridica": bool(
                customer.get("nit") or customer.get("identification")
            ),
            # Cartera
            "cartera": {
                "total": cartera_total,
                "vencida": cartera_vencida,
                "corriente": cartera_corriente,
                "documentos_total": len(receivables),
                "documentos_vencidos": documentos_vencidos,
                "pct_vencida": (
                    (cartera_vencida / cartera_total * 100)
                    if cartera_total > 0 else 0
                ),
                "aging": aging_buckets,
                "dias_mora_max": (
                    max(
                        (
                            (now - datetime.fromisoformat(
                                (r.get("due_date") or "").replace("Z", "+00:00")
                            ).replace(tzinfo=None)).days
                        )
                        for r in receivables
                        if r.get("due_date")
                    )
                    if receivables and any(r.get("due_date") for r in receivables)
                    else 0
                ),
            },
            # Pedidos / Compras
            "compras": {
                "total_historico": total_compras,
                "num_pedidos": num_pedidos,
                "promedio_pedido": round(promedio_pedido, 2),
                "ultimo_pedido_fecha": ultimo_pedido_fecha,
                "ultimo_pedido_monto": round(ultimo_pedido_monto, 2),
                "frecuencia_meses": frecuencia_meses,
            },
            # Gestión comercial
            "gestion": {
                "visitas_12_meses": gestiones_12m,
            },
            "fuente": "rsales",
            "extraido_en": datetime.now().isoformat(),
        }


# ── Cache de clientes RSales (NIT → code, nombre) ──────────
_rsales_customer_cache: dict[str, dict[str, Any]] | None = None
_rsales_cache_time: float = 0.0
CACHE_TTL = 600  # 10 minutos
_cache_lock = threading.Lock()


def _get_customer_index() -> dict[str, dict[str, Any]]:
    """Índice NIT → datos básicos del cliente (cache 10 min, thread-safe)."""
    global _rsales_customer_cache, _rsales_cache_time
    now = time.time()
    with _cache_lock:
        if _rsales_customer_cache and (now - _rsales_cache_time) < CACHE_TTL:
            return _rsales_customer_cache
    client = get_rsales_client()
    customers = client.get_all_customers()
    idx: dict[str, dict[str, Any]] = {}
    for c in customers:
        nit = (c.get("nit") or c.get("identification") or "").strip()
        code = (c.get("code") or c.get("client_code") or "").strip()
        if nit:
            idx[nit] = {"code": code, "name": c.get("name") or c.get("business_name", ""),
                        "city": c.get("city", ""), "state": c.get("state", "")}
        if code and code not in idx:
            idx[code] = {"code": code, "name": c.get("name") or c.get("business_name", ""),
                         "city": c.get("city", ""), "state": c.get("state", "")}
    with _cache_lock:
        _rsales_customer_cache = idx
        _rsales_cache_time = now
    log.info("RSales customer index: %d entries cached for %ds", len(idx), CACHE_TTL)
    return idx


def find_customer_in_rsales(cedula_nit: str) -> dict[str, Any] | None:
    """Busca un cliente por cédula/NIT en RSales (usa cache)."""
    idx = _get_customer_index()
    if cedula_nit in idx:
        return idx[cedula_nit]
    # También buscar por código parcial
    for key, val in idx.items():
        if key.startswith(cedula_nit):
            return val
    return None


# ── Singleton (thread-safe) ────────────────────────────────
_client: RsalesClient | None = None
_client_lock = threading.Lock()


def get_rsales_client() -> RsalesClient:
    """Devuelve una instancia singleton del cliente RSales (thread-safe)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = RsalesClient()
        return _client
