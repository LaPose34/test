"""Optimizador de rutas de costo mínimo.

Para cada helicóptero se resuelve un problema de recogida y entrega
(pickup & delivery) con un solo vehículo:

- La ruta empieza en la base del helicóptero (y opcionalmente vuelve a ella).
- Cada solicitud aporta dos paradas: recoger en el origen y entregar en el
  destino (siempre en ese orden).
- Restricciones: capacidad de pax, carga máxima (los pax cuentan como peso),
  prohibición de mezclar pax y carga si el helicóptero no lo permite,
  compatibilidad helicóptero/helipuerto (tamaño, peso, vetos) y autonomía de
  combustible con recarga en helipuertos que dispongan de combustible.
- Costo de un tramo = horas de vuelo × (tarifa horaria + consumo L/h × precio
  del litro).

La búsqueda es exacta (ramificación y poda) hasta ``MAX_EXACT_REQUESTS``
solicitudes; por encima se usa solo la heurística voraz.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Helicopter, Scenario, TransportRequest

MAX_EXACT_REQUESTS = 8


@dataclass(frozen=True)
class Leg:
    """Un tramo de vuelo con la acción realizada al aterrizar."""

    from_pad: str
    to_pad: str
    km: float
    hours: float
    cost: float
    action: str  # p. ej. "recoger R1", "entregar R1", "regreso a base"
    pax_onboard: int  # a bordo tras la acción
    cargo_onboard_kg: float
    refueled_before: bool  # ¿se recargó combustible antes de despegar?


@dataclass
class RoutePlan:
    helicopter_id: str
    feasible: bool
    legs: list[Leg] = field(default_factory=list)
    total_km: float = 0.0
    total_hours: float = 0.0
    total_cost: float = 0.0
    method: str = "exacto"  # "exacto" o "heurístico"
    infeasible_reason: str = ""


class _State:
    __slots__ = ("pad", "fuel_h", "cost", "pending", "onboard", "legs")

    def __init__(self, pad, fuel_h, cost, pending, onboard, legs):
        self.pad = pad
        self.fuel_h = fuel_h
        self.cost = cost
        self.pending = pending  # frozenset de índices por recoger
        self.onboard = onboard  # frozenset de índices a bordo
        self.legs = legs  # tupla de Leg


def _static_feasibility(
    scn: Scenario, heli: Helicopter, requests: list[TransportRequest]
) -> str:
    """Devuelve el motivo de infactibilidad evidente, o cadena vacía."""
    base = scn.helipads[heli.base]
    if not base.allows(heli):
        return f"la base '{heli.base}' no admite este helicóptero"
    for req in requests:
        if req.pax > heli.pax_capacity:
            return f"solicitud '{req.id}': {req.pax} pax excede capacidad ({heli.pax_capacity})"
        payload = req.cargo_kg + req.pax * scn.pax_weight_kg
        if payload > heli.max_payload_kg:
            return (
                f"solicitud '{req.id}': {payload:.0f} kg excede carga máxima "
                f"({heli.max_payload_kg:.0f} kg)"
            )
        if req.pax > 0 and req.cargo_kg > 0 and not heli.can_combine_pax_cargo:
            return f"solicitud '{req.id}': el helicóptero no puede llevar pax y carga a la vez"
        for pad_id in (req.origin, req.destination):
            if not scn.helipads[pad_id].allows(heli):
                return f"el helipuerto '{pad_id}' no admite este helicóptero"
    return ""


def _load_ok(scn: Scenario, heli: Helicopter, onboard_reqs: list[TransportRequest]) -> bool:
    pax = sum(r.pax for r in onboard_reqs)
    cargo = sum(r.cargo_kg for r in onboard_reqs)
    if pax > heli.pax_capacity:
        return False
    if cargo + pax * scn.pax_weight_kg > heli.max_payload_kg:
        return False
    if pax > 0 and cargo > 0 and not heli.can_combine_pax_cargo:
        return False
    return True


def _fly(
    scn: Scenario,
    heli: Helicopter,
    state: _State,
    to_pad: str,
    action: str,
    onboard_after: frozenset,
    pending_after: frozenset,
    requests: list[TransportRequest],
) -> _State | None:
    """Intenta volar un tramo; devuelve el nuevo estado o None si es inviable."""
    fuel_h = state.fuel_h
    refueled = False
    pad_here = scn.helipads[state.pad]
    if pad_here.has_fuel and fuel_h < heli.endurance_h:
        fuel_h = heli.endurance_h
        refueled = True

    km = scn.distance_km(state.pad, to_pad)
    hours = km / heli.cruise_speed_kmh if km > 0 else 0.0
    if hours > fuel_h + 1e-9:
        return None

    cost = hours * scn.cost_per_flight_hour(heli)
    onboard_reqs = [requests[i] for i in onboard_after]
    leg = Leg(
        from_pad=state.pad,
        to_pad=to_pad,
        km=km,
        hours=hours,
        cost=cost,
        action=action,
        pax_onboard=sum(r.pax for r in onboard_reqs),
        cargo_onboard_kg=sum(r.cargo_kg for r in onboard_reqs),
        refueled_before=refueled,
    )
    return _State(
        pad=to_pad,
        fuel_h=fuel_h - hours,
        cost=state.cost + cost,
        pending=pending_after,
        onboard=onboard_after,
        legs=state.legs + (leg,),
    )


def _successors(
    scn: Scenario, heli: Helicopter, state: _State, requests: list[TransportRequest]
):
    """Genera los estados alcanzables desde `state` (una acción por estado)."""
    for i in state.pending:
        req = requests[i]
        onboard_after = state.onboard | {i}
        if not _load_ok(scn, heli, [requests[j] for j in onboard_after]):
            continue
        nxt = _fly(
            scn,
            heli,
            state,
            req.origin,
            f"recoger {req.id}",
            onboard_after,
            state.pending - {i},
            requests,
        )
        if nxt is not None:
            yield nxt
    for i in state.onboard:
        req = requests[i]
        nxt = _fly(
            scn,
            heli,
            state,
            req.destination,
            f"entregar {req.id}",
            state.onboard - {i},
            state.pending,
            requests,
        )
        if nxt is not None:
            yield nxt


def _finish(scn: Scenario, heli: Helicopter, state: _State, requests) -> _State | None:
    """Aplica el regreso a base si el escenario lo exige."""
    if not scn.return_to_base or state.pad == heli.base:
        return state
    return _fly(
        scn, heli, state, heli.base, "regreso a base", frozenset(), frozenset(), requests
    )


def _greedy(scn: Scenario, heli: Helicopter, requests, start: _State) -> _State | None:
    """Heurística voraz: siempre la siguiente acción viable más barata."""
    state = start
    while state.pending or state.onboard:
        best = None
        for nxt in _successors(scn, heli, state, requests):
            if best is None or nxt.cost < best.cost:
                best = nxt
        if best is None:
            return None
        state = best
    return _finish(scn, heli, state, requests)


def optimize_route(scn: Scenario, heli: Helicopter) -> RoutePlan:
    """Ruta de costo mínimo para que UN helicóptero sirva todas las solicitudes."""
    requests = scn.requests
    reason = _static_feasibility(scn, heli, requests)
    if reason:
        return RoutePlan(helicopter_id=heli.id, feasible=False, infeasible_reason=reason)

    start = _State(
        pad=heli.base,
        fuel_h=heli.endurance_h,
        cost=0.0,
        pending=frozenset(range(len(requests))),
        onboard=frozenset(),
        legs=(),
    )

    best = _greedy(scn, heli, requests, start)
    method = "heurístico"

    if len(requests) <= MAX_EXACT_REQUESTS:
        method = "exacto"
        stack = [start]
        while stack:
            state = stack.pop()
            if best is not None and state.cost >= best.cost:
                continue
            if not state.pending and not state.onboard:
                done = _finish(scn, heli, state, requests)
                if done is not None and (best is None or done.cost < best.cost):
                    best = done
                continue
            stack.extend(_successors(scn, heli, state, requests))

    if best is None:
        return RoutePlan(
            helicopter_id=heli.id,
            feasible=False,
            infeasible_reason=(
                "sin ruta viable (autonomía de combustible o restricciones de carga)"
            ),
        )

    legs = list(best.legs)
    return RoutePlan(
        helicopter_id=heli.id,
        feasible=True,
        legs=legs,
        total_km=sum(l.km for l in legs),
        total_hours=sum(l.hours for l in legs),
        total_cost=best.cost,
        method=method,
    )


def optimize_fleet(scn: Scenario) -> list[RoutePlan]:
    """Evalúa cada helicóptero de la flota; devuelve los planes ordenados por costo.

    El primer plan factible de la lista es el óptimo global (un solo
    helicóptero sirve todas las solicitudes).
    """
    plans = [optimize_route(scn, heli) for heli in scn.helicopters]
    plans.sort(key=lambda p: (not p.feasible, p.total_cost))
    return plans
