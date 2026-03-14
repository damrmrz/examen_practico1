import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Configuración ──────────────────────────────────────────────────────────────
BASE_URL       = "http://ecomarket.local/api/v1"
TOKEN          = "eyJ0eXAiO..."          # reemplazar con el token del examen
INTERVALO_BASE = 5
INTERVALO_MAX  = 60
TIMEOUT        = 10


# ── Interfaz Observer ──────────────────────────────────────────────────────────
class Observador(ABC):
    @abstractmethod
    async def actualizar(self, inventario: dict) -> None: ...


# ── Observable ────────────────────────────────────────────────────────────────
class MonitorInventario:
    def __init__(self):
        self._observadores: list[Observador] = []
        self._ultimo_etag:   str | None = None
        self._ultimo_estado: dict | None = None
        self._ejecutando:    bool = False
        self._intervalo:     float = INTERVALO_BASE

    # ── Gestión de observadores ────────────────────────────────────────────────
    def suscribir(self, obs: Observador) -> None:
        self._observadores.append(obs)

    def desuscribir(self, obs: Observador) -> None:
        self._observadores.remove(obs)

    async def _notificar(self, inventario: dict) -> None:
        """Notifica a TODOS los observadores de forma idéntica.
        Si uno lanza excepción, los demás siguen recibiendo la notificación."""
        for obs in self._observadores:
            try:
                await obs.actualizar(inventario)
            except Exception as e:
                log.error(f"Error en observador {obs.__class__.__name__}: {e}")

    # ── GET /inventario ────────────────────────────────────────────────────────
    async def _consultar_inventario(self) -> dict | None:
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/json",
        }
        if self._ultimo_etag:
            headers["If-None-Match"] = self._ultimo_etag

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{BASE_URL}/inventario",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                ) as resp:

                    if resp.status == 200:
                        body = await resp.json()
                        if body.get("productos") is None:
                            log.warning("200 sin campo 'productos' — ignorado")
                            return None
                        self._ultimo_etag = resp.headers.get("ETag")
                        self._intervalo = INTERVALO_BASE
                        return body

                    elif resp.status == 304:
                        log.debug("304 — sin cambios")
                        self._intervalo = min(self._intervalo * 2, INTERVALO_MAX)
                        return None

                    elif 400 <= resp.status < 500:
                        log.error(f"Error {resp.status} — no se reintentará")
                        return None

                    elif resp.status >= 500:
                        self._intervalo = min(self._intervalo * 2, INTERVALO_MAX)
                        log.warning(f"Error {resp.status} — backoff {self._intervalo}s")
                        return None

                    else:
                        log.warning(f"Código inesperado: {resp.status}")
                        return None

        except aiohttp.ServerTimeoutError:
            log.warning("Timeout — ciclo continúa")
            return None
        except aiohttp.ClientConnectionError:
            log.warning("Sin conexión — ciclo continúa")
            return None
        except Exception as e:
            log.error(f"Error inesperado: {e}")
            return None

    # ── Ciclo de polling ───────────────────────────────────────────────────────
    async def iniciar(self) -> None:
        self._ejecutando = True
        log.info("Monitor iniciado")
        while self._ejecutando:
            datos = await self._consultar_inventario()
            if datos and datos != self._ultimo_estado:
                self._ultimo_estado = datos
                self._intervalo = INTERVALO_BASE
                await self._notificar(datos)
            await asyncio.sleep(self._intervalo)   # ← no bloqueante
        log.info("Monitor detenido")

    def detener(self) -> None:
        """Cierre suave — solo cambia la bandera, no cancela la corrutina."""
        self._ejecutando = False


# ── Observadores concretos ─────────────────────────────────────────────────────
class ModuloCompras(Observador):
    async def actualizar(self, inventario: dict) -> None:
        bajos = [p for p in inventario.get("productos", [])
                 if p.get("status") == "BAJO_MINIMO"]
        if bajos:
            log.info(f"[COMPRAS] {len(bajos)} producto(s) bajo mínimo:")
            for p in bajos:
                log.info(f"  · {p['id']} {p['nombre']} — stock={p['stock']} mín={p['stock_minimo']}")


class ModuloAlertas(Observador):
    async def actualizar(self, inventario: dict) -> None:
        bajos = [p for p in inventario.get("productos", [])
                 if p.get("status") == "BAJO_MINIMO"]
        async with aiohttp.ClientSession() as session:
            for p in bajos:
                payload = {
                    "producto_id": p["id"],
                    "stock_actual": p["stock"],
                    "stock_minimo": p["stock_minimo"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    async with session.post(
                        f"{BASE_URL}/alertas",
                        json=payload,
                        headers={"Authorization": f"Bearer {TOKEN}",
                                 "Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                    ) as resp:
                        if resp.status == 201:
                            log.info(f"[ALERTAS] Alerta enviada: {p['id']}")
                        elif resp.status == 422:
                            log.error(f"[ALERTAS] 422 — payload inválido para {p['id']}, no se reintenta")
                        else:
                            log.warning(f"[ALERTAS] Respuesta inesperada {resp.status}")
                except Exception as e:
                    log.error(f"[ALERTAS] Error al enviar alerta para {p['id']}: {e}")


# ── Punto de entrada ───────────────────────────────────────────────────────────
async def main():
    monitor = MonitorInventario()
    monitor.suscribir(ModuloCompras())
    monitor.suscribir(ModuloAlertas())

    try:
        await monitor.iniciar()
    except KeyboardInterrupt:
        monitor.detener()

if __name__ == "__main__":
    asyncio.run(main())