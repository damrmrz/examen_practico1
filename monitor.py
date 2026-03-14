import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_URL       = "http://ecomarket.local/api/v1"
TOKEN          = "eyJ0eXAiO..."
INTERVALO_BASE = 5
INTERVALO_MAX  = 60
TIMEOUT        = 10

# ── Interfaz Observer ──────────────────────────────────────────────────────────
class Observador(ABC):
    @abstractmethod
    async def actualizar(self, inventario: dict) -> None:
        ...

# ── Observable (MonitorInventario) ────────────────────────────────────────────
class MonitorInventario:
    def __init__(self):
        self._observadores: list[Observador] = []
        self._ultimo_etag:   str | None = None
        self._ultimo_estado: dict | None = None
        self._ejecutando:    bool = False
        self._intervalo:     float = INTERVALO_BASE

    def suscribir(self, obs: Observador) -> None:
        self._observadores.append(obs)

    def desuscribir(self, obs: Observador) -> None:
        self._observadores.remove(obs)

    async def _notificar(self, inventario: dict) -> None:
        for obs in self._observadores:
            try:
                await obs.actualizar(inventario)
            except Exception as e:
                log.error(f"Error en observador {obs.__class__.__name__}: {e}")

    async def _consultar_inventario(self) -> dict | None:
        # TODO — implementar en Fase 2
        pass

    async def iniciar(self) -> None:
        self._ejecutando = True
        while self._ejecutando:
            datos = await self._consultar_inventario()
            if datos and datos != self._ultimo_estado:
                self._ultimo_estado = datos
                self._intervalo = INTERVALO_BASE          # reset backoff
                await self._notificar(datos)
            await asyncio.sleep(self._intervalo)

    def detener(self) -> None:
        self._ejecutando = False

# ── Observadores concretos ─────────────────────────────────────────────────────
class ModuloCompras(Observador):
    async def actualizar(self, inventario: dict) -> None:
        bajos = [p for p in inventario.get("productos", [])
                 if p.get("status") == "BAJO_MINIMO"]
        for p in bajos:
            log.info(f"[COMPRAS] {p['nombre']} — stock {p['stock']} / mín {p['stock_minimo']}")

class ModuloAlertas(Observador):
    async def actualizar(self, inventario: dict) -> None:
        # TODO — implementar POST en Fase 3
        pass

# ── Punto de entrada ───────────────────────────────────────────────────────────
async def main():
    monitor = MonitorInventario()
    monitor.suscribir(ModuloCompras())
    monitor.suscribir(ModuloAlertas())
    await monitor.iniciar()

if __name__ == "__main__":
    asyncio.run(main())