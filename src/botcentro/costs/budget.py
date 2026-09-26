"""Control de gasto con reserva previa (SRS-N17, PRD §13; T-29).

Toda operación cobrable (OCR, embeddings, generación, adquisición de pago) reserva su costo
estimado antes de ejecutarse; al terminar se concilia con el costo real o se libera. Sin
presupuesto configurado no se autoriza gasto (DEC-07).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from botcentro.errors import BotcentroError, FailureKind
from botcentro.insforge.client import RpcClient, as_rows

log = logging.getLogger(__name__)


class BudgetExceeded(BotcentroError):
    kind = FailureKind.POLICY

    def __init__(self, reason: str) -> None:
        super().__init__("BUDGET_EXCEEDED", reason)


@dataclass
class Reservation:
    id: UUID
    provider: str
    operation: str
    estimated_cost: Decimal
    alert: bool
    actual_cost: Decimal | None = None

    def settle(self, actual_cost: Decimal | float | str) -> None:
        self.actual_cost = Decimal(str(actual_cost))


class BudgetGuard:
    def __init__(self, rpc: RpcClient) -> None:
        self._rpc = rpc

    def reserve(self, provider: str, operation: str, estimated_cost: Decimal | float | str, *, units: float = 0,
                job_id: UUID | None = None, query_id: UUID | None = None,
                source_id: UUID | None = None) -> Reservation:
        cost = Decimal(str(estimated_cost))
        row = as_rows(self._rpc.call("budget_reserve", {
            "p_provider": provider, "p_operation": operation, "p_estimated_cost": cost, "p_units": units,
            "p_job_id": job_id, "p_query_id": query_id, "p_source_id": source_id,
        }))[0]
        if not row["granted"]:
            raise BudgetExceeded(row.get("reason") or "presupuesto no disponible")
        if row["alert"]:
            log.warning("gasto de %s/%s alcanza el umbral de alerta del presupuesto", provider, operation)
        return Reservation(UUID(str(row["reservation_id"])), provider, operation, cost, bool(row["alert"]))

    def settle(self, reservation: Reservation, actual_cost: Decimal) -> None:
        self._rpc.call("budget_settle", {"p_reservation_id": reservation.id, "p_actual_cost": actual_cost})

    def release(self, reservation: Reservation) -> None:
        self._rpc.call("budget_release", {"p_reservation_id": reservation.id})

    @contextmanager
    def spend(self, provider: str, operation: str, estimated_cost: Decimal | float | str,
              **kwargs: object) -> Iterator[Reservation]:
        """Reserva y, al salir, concilia o libera.

        * Éxito sin costo real fijado: se concilia con el estimado (no subestimar consumo).
        * Error con costo real fijado (se consumió algo antes de fallar): se concilia.
        * Error sin costo real: se libera la reserva.
        """
        reservation = self.reserve(provider, operation, estimated_cost, **kwargs)  # type: ignore[arg-type]
        try:
            yield reservation
        except BaseException:
            if reservation.actual_cost is not None:
                self.settle(reservation, reservation.actual_cost)
            else:
                self.release(reservation)
            raise
        self.settle(reservation, reservation.actual_cost if reservation.actual_cost is not None
                    else reservation.estimated_cost)
