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

import math
from dataclasses import dataclass, field

from .models import Helicopter, Scenario, TransportRequest

MAX_EXACT_REQUESTS = 8
_KG_EPS = 1e-6  # tolerancia numérica en comparaciones de peso (kg)


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
    pax_kg_onboard: float  # peso corporal real de los pax a bordo
    baggage_kg_onboard: float  # equipaje de los pax a bordo
    cargo_onboard_kg: float
    refueled_before: bool  # ¿se cargó combustible antes de despegar?
    fuel_l_takeoff: float = 0.0  # litros a bordo al despegar este tramo
    takeoff_kg: float | None = None  # peso total al despegue (None si no hay datos)

    @property
    def payload_kg(self) -> float:
        """Peso total a bordo (tras la acción) contra los límites del helicóptero."""
        return self.pax_kg_onboard + self.baggage_kg_onboard + self.cargo_onboard_kg


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
    # Solicitudes efectivamente planificadas (tras dividir las divisibles)
    requests: list[TransportRequest] = field(default_factory=list)


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
        payload = scn.request_payload_kg(req)
        if heli.max_payload_kg is not None and payload > heli.max_payload_kg + _KG_EPS:
            return (
                f"solicitud '{req.id}': {payload:.0f} kg (pax + equipaje + carga) "
                f"excede el techo de peso ({heli.max_payload_kg:.0f} kg)"
            )
        if heli.has_takeoff_limit and heli.empty_weight_kg + payload > heli.mtow_kg + _KG_EPS:
            return (
                f"solicitud '{req.id}': {heli.empty_weight_kg + payload:.0f} kg "
                f"(vacío + pax + equipaje + carga) excede el peso máximo de "
                f"despegue ({heli.mtow_kg:.0f} kg) incluso sin combustible"
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
    # Límite estructural de cabina (opcional) y cota de MTOW sin combustible;
    # el límite de despegue CON combustible se verifica en cada despegue
    # dentro de _fly, pero esta cota evita embarcar cargas imposibles.
    payload = sum(scn.request_payload_kg(r) for r in onboard_reqs)
    if heli.max_payload_kg is not None and payload > heli.max_payload_kg + _KG_EPS:
        return False
    if heli.has_takeoff_limit and heli.empty_weight_kg + payload > heli.mtow_kg + _KG_EPS:
        return False
    if pax > 0 and cargo > 0 and not heli.can_combine_pax_cargo:
        return False
    return True


def _max_fuel_h(scn: Scenario, heli: Helicopter, payload_kg: float) -> float:
    """Máximo combustible (en horas de vuelo) admisible al despegar con esta
    carga sin exceder el peso máximo de despegue. -1 si ni con tanque vacío cabe."""
    if not heli.has_takeoff_limit:
        return heli.endurance_h
    avail_kg = heli.mtow_kg - heli.empty_weight_kg - payload_kg
    if avail_kg < -1e-9:
        return -1.0
    if heli.fuel_consumption_lph <= 0:
        return heli.endurance_h
    fuel_l = avail_kg / scn.fuel_density_kg_per_l
    return min(heli.endurance_h, fuel_l / heli.fuel_consumption_lph)


def _fly(
    scn: Scenario,
    heli: Helicopter,
    state: _State,
    to_pad: str,
    action: str,
    onboard_after: frozenset,
    pending_after: frozenset,
    requests: list[TransportRequest],
    fuel_hop: dict[str, float] | None = None,
    fuel_policy: str = "cap",
) -> _State | None:
    """Intenta volar un tramo; devuelve el nuevo estado o None si es inviable.

    `fuel_policy` decide cuánto combustible cargar antes de despegar de un
    punto con combustible:
      - "cap":  al máximo que el peso de despegue permite (máximo alcance).
      - "safe": lo mínimo seguro — este tramo más el salto desde el destino
        al punto de recarga más cercano (mínimo peso, máxima carga útil).
    """
    km = scn.distance_km(state.pad, to_pad)
    hours = 0.0
    if km > 0:
        # Tiempo de crucero + ascenso y descenso según la velocidad vertical
        hours = km / heli.cruise_speed_kmh + heli.vertical_time_h(scn.cruise_altitude_m)
    fuel_h = state.fuel_h
    refueled = False
    fuel_l_takeoff = fuel_h * heli.fuel_consumption_lph if heli.fuel_consumption_lph > 0 else 0.0
    takeoff_kg = None

    if km > 0:
        # Solo hay despegue real si el tramo se vuela. La carga que VUELA es
        # la de antes de la acción (la acción ocurre al aterrizar en `to_pad`).
        payload_flying = sum(scn.request_payload_kg(requests[i]) for i in state.onboard)
        fuel_cap_h = _max_fuel_h(scn, heli, payload_flying)
        if fuel_cap_h < 0:
            return None

        pad_here = scn.helipads[state.pad]
        if pad_here.has_fuel:
            target = fuel_cap_h
            if fuel_policy == "safe" and fuel_hop is not None:
                hop = fuel_hop.get(to_pad, math.inf)
                if hop != math.inf:
                    target = min(fuel_cap_h, hours + hop + 1e-6)
            if fuel_h < target - 1e-9:
                fuel_h = target
                refueled = True
        if fuel_h > fuel_cap_h + 1e-9:
            return None  # llegó con más combustible del que este despegue admite
        if hours > fuel_h + 1e-9:
            return None

        fuel_l_takeoff = fuel_h * heli.fuel_consumption_lph if heli.fuel_consumption_lph > 0 else 0.0
        if heli.has_takeoff_limit:
            takeoff_kg = (
                heli.empty_weight_kg
                + fuel_l_takeoff * scn.fuel_density_kg_per_l
                + payload_flying
            )

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
        pax_kg_onboard=sum(scn.request_pax_weight_kg(r) for r in onboard_reqs),
        baggage_kg_onboard=sum(r.baggage_kg for r in onboard_reqs),
        cargo_onboard_kg=sum(r.cargo_kg for r in onboard_reqs),
        refueled_before=refueled,
        fuel_l_takeoff=fuel_l_takeoff,
        takeoff_kg=takeoff_kg,
    )
    return _State(
        pad=to_pad,
        fuel_h=fuel_h - hours,
        cost=state.cost + cost,
        pending=pending_after,
        onboard=onboard_after,
        legs=state.legs + (leg,),
    )


def _fly_variants(scn, heli, state, to_pad, action, onboard_after, pending_after,
                  requests, fuel_hop):
    """El mismo tramo con las dos políticas de combustible (sin duplicados)."""
    safe = _fly(scn, heli, state, to_pad, action, onboard_after, pending_after,
                requests, fuel_hop, "safe")
    cap = _fly(scn, heli, state, to_pad, action, onboard_after, pending_after,
               requests, fuel_hop, "cap")
    if safe is not None:
        yield safe
    if cap is not None and (safe is None or abs(cap.fuel_h - safe.fuel_h) > 1e-9):
        yield cap


def _successors(
    scn: Scenario,
    heli: Helicopter,
    state: _State,
    requests: list[TransportRequest],
    fuel_hop: dict[str, float],
):
    """Genera los estados alcanzables desde `state` (una acción por estado)."""
    idx_by_id = {r.id: j for j, r in enumerate(requests)}
    for i in state.pending:
        req = requests[i]
        if req.after is not None:
            j = idx_by_id[req.after]
            if j in state.pending or j in state.onboard:  # aún no entregada
                continue
        onboard_after = state.onboard | {i}
        if not _load_ok(scn, heli, [requests[j] for j in onboard_after]):
            continue
        yield from _fly_variants(
            scn, heli, state, req.origin, f"recoger {req.id}",
            onboard_after, state.pending - {i}, requests, fuel_hop,
        )
    for i in state.onboard:
        req = requests[i]
        yield from _fly_variants(
            scn, heli, state, req.destination, f"entregar {req.id}",
            state.onboard - {i}, state.pending, requests, fuel_hop,
        )


def _finish(scn, heli, state: _State, requests, fuel_hop) -> _State | None:
    """Aplica el regreso a base si el escenario lo exige."""
    if not scn.return_to_base or state.pad == heli.base:
        return state
    return _fly(
        scn, heli, state, heli.base, "regreso a base", frozenset(), frozenset(),
        requests, fuel_hop, "cap",
    )


MAX_SEARCH_NODES = 200_000


def _first_solution(
    scn: Scenario, heli: Helicopter, requests, start: _State, fuel_hop
) -> _State | None:
    """Heurística: búsqueda en profundidad guiada por costo con vuelta atrás.

    Explora primero la acción más barata (como la voraz) pero, si un camino
    se atasca (combustible/peso), retrocede y prueba alternativas, hasta un
    tope de nodos. Devuelve la primera solución completa encontrada.
    """
    stack = [start]
    nodes = 0
    while stack and nodes < MAX_SEARCH_NODES:
        state = stack.pop()
        nodes += 1
        if not state.pending and not state.onboard:
            done = _finish(scn, heli, state, requests, fuel_hop)
            if done is not None:
                return done
            continue
        succ = sorted(
            _successors(scn, heli, state, requests, fuel_hop),
            key=lambda s: s.cost,
            reverse=True,  # la pila saca primero el más barato
        )
        stack.extend(succ)
    return None


def _chunk_units_for_heli(scn: Scenario, heli: Helicopter, req: TransportRequest) -> int:
    """Cuántas unidades de esta solicitud caben por viaje en este helicóptero."""
    unit_payload = scn.request_payload_kg(req) / req.units
    budget = math.inf
    if heli.max_payload_kg is not None:
        budget = heli.max_payload_kg
    if heli.has_takeoff_limit:
        tank_kg = heli.fuel_capacity_l * scn.fuel_density_kg_per_l
        # Presupuesto conservador: con tanque lleno; si ni una unidad cabe,
        # relajar a medio tanque (el chequeo real por despegue sigue vigente).
        mtow_budget = heli.mtow_kg - heli.empty_weight_kg - tank_kg
        if unit_payload > 0 and mtow_budget < unit_payload:
            mtow_budget = heli.mtow_kg - heli.empty_weight_kg - tank_kg / 2
        budget = min(budget, mtow_budget)
    by_weight = (
        int((budget + _KG_EPS) // unit_payload)
        if unit_payload > 0 and budget != math.inf
        else req.units
    )
    by_seats = heli.pax_capacity if req.pax > 0 else req.units
    return max(1, min(req.units, by_weight, by_seats))


def _split_requests(scn: Scenario, heli: Helicopter) -> list[TransportRequest]:
    """Divide las solicitudes divisibles en partes que caben por viaje.

    Replica el comportamiento operativo real: un requerimiento de 29 pax se
    reparte en rotaciones de, p. ej., 10 + 10 + 9. No se dividen solicitudes
    encadenadas con `after` ni aquellas cuyos pax no coinciden con `units`.
    """
    chained = {r.after for r in scn.requests if r.after is not None}
    out: list[TransportRequest] = []
    for req in scn.requests:
        divisible = (
            req.splittable
            and req.units > 1
            and req.after is None
            and req.id not in chained
            and (req.pax == 0 or req.pax == req.units)
        )
        if not divisible:
            out.append(req)
            continue
        chunk = _chunk_units_for_heli(scn, heli, req)
        if chunk >= req.units:
            out.append(req)
            continue
        n = req.units
        part = 1
        done = 0
        while done < n:
            m = min(chunk, n - done)
            frac = m / n
            out.append(
                TransportRequest(
                    id=f"{req.id}/{part}",
                    origin=req.origin,
                    destination=req.destination,
                    pax=m if req.pax else 0,
                    cargo_kg=req.cargo_kg * frac,
                    pax_weight_kg=(
                        req.pax_weight_kg * frac if req.pax_weight_kg is not None else None
                    ),
                    baggage_kg=req.baggage_kg * frac,
                    units=m,
                    description=req.description,
                    company=req.company,
                    project=req.project,
                )
            )
            done += m
            part += 1
    return out


def optimize_route(scn: Scenario, heli: Helicopter) -> RoutePlan:
    """Ruta de costo mínimo para que UN helicóptero sirva todas las solicitudes."""
    requests = _split_requests(scn, heli)
    reason = _static_feasibility(scn, heli, requests)
    if reason:
        return RoutePlan(
            helicopter_id=heli.id,
            feasible=False,
            infeasible_reason=reason,
            requests=requests,
        )

    # Horas de vuelo desde cada punto hasta su recarga más cercana (para la
    # política de combustible "mínimo seguro").
    fuel_hop: dict[str, float] = {}
    for pid, pad in scn.helipads.items():
        if pad.has_fuel:
            fuel_hop[pid] = 0.0
            continue
        vert_h = heli.vertical_time_h(scn.cruise_altitude_m)
        best_h = math.inf
        for fid, fpad in scn.helipads.items():
            if fpad.has_fuel:
                try:
                    best_h = min(
                        best_h,
                        scn.distance_km(pid, fid) / heli.cruise_speed_kmh + vert_h,
                    )
                except ValueError:
                    continue
        fuel_hop[pid] = best_h

    # Si la base tiene combustible, se carga justo antes del primer despegue
    # (limitado por el MTOW); si no, se asume que llega con tanque lleno.
    base_has_fuel = scn.helipads[heli.base].has_fuel
    start = _State(
        pad=heli.base,
        fuel_h=0.0 if base_has_fuel else heli.endurance_h,
        cost=0.0,
        pending=frozenset(range(len(requests))),
        onboard=frozenset(),
        legs=(),
    )

    best = _first_solution(scn, heli, requests, start, fuel_hop)
    method = "heurístico"

    if len(requests) <= MAX_EXACT_REQUESTS:
        method = "exacto"
        stack = [start]
        while stack:
            state = stack.pop()
            if best is not None and state.cost >= best.cost:
                continue
            if not state.pending and not state.onboard:
                done = _finish(scn, heli, state, requests, fuel_hop)
                if done is not None and (best is None or done.cost < best.cost):
                    best = done
                continue
            stack.extend(_successors(scn, heli, state, requests, fuel_hop))

    if best is None:
        if len(requests) > MAX_EXACT_REQUESTS:
            reason = (
                f"la heurística no encontró ruta viable (más de {MAX_EXACT_REQUESTS} "
                "solicitudes: podría existir; pruebe con menos solicitudes a la vez)"
            )
        else:
            reason = "sin ruta viable (autonomía de combustible o restricciones de carga)"
        return RoutePlan(
            helicopter_id=heli.id,
            feasible=False,
            infeasible_reason=reason,
            requests=requests,
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
        requests=requests,
    )


def plan_visits(scn: Scenario, heli: Helicopter, plan: RoutePlan) -> list[dict]:
    """Agrupa la ruta en paradas, con tiempo en tierra estimado y horario.

    Cada parada incluye: pad, tramo de llegada (from/km/hours/cost), acciones,
    pax y kg movidos, empresas involucradas, minutos en tierra estimados,
    exceso sobre el tiempo admisible del helicóptero y el cargo extra que
    asume el cliente. `arrive_h`/`depart_h` son horas desde el inicio,
    incluyendo el tiempo en tierra de las paradas anteriores.
    """
    by_id = {r.id: r for r in plan.requests}
    visits: list[dict] = []
    flight_t = 0.0
    for leg in plan.legs:
        flight_t += leg.hours
        if leg.km > 0 or not visits:
            visits.append(
                {
                    "pad": leg.to_pad,
                    "from": leg.from_pad,
                    "km": leg.km,
                    "hours": leg.hours,
                    "cost": leg.cost,
                    "legs": [leg],
                    "arrive_h": flight_t,
                }
            )
        else:
            visits[-1]["legs"].append(leg)

    cum_ground_h = 0.0
    for v in visits:
        pax_moved = 0
        kg_moved = 0.0
        companies: set[str] = set()
        for leg in v["legs"]:
            action, _, rid = leg.action.partition(" ")
            req = by_id.get(rid)
            if action not in ("recoger", "entregar") or req is None:
                continue
            pax_moved += req.pax
            kg_moved += scn.request_payload_kg(req)
            if req.company:
                companies.add(req.company)
        if pax_moved or kg_moved:
            ground_min = (
                scn.ground_base_min
                + pax_moved * scn.ground_min_per_pax
                + kg_moved / 100.0 * scn.ground_min_per_100kg
            )
        else:
            ground_min = 0.0
        excess_min = (
            max(0.0, ground_min - heli.free_ground_min) if heli.free_ground_min > 0 else 0.0
        )
        v["pax_moved"] = pax_moved
        v["kg_moved"] = kg_moved
        v["companies"] = sorted(companies)
        v["ground_min"] = ground_min
        v["excess_min"] = excess_min
        v["extra_cost"] = excess_min / 60.0 * heli.price_per_hour
        v["arrive_h"] += cum_ground_h
        cum_ground_h += ground_min / 60.0
        v["depart_h"] = v["arrive_h"] + ground_min / 60.0
    return visits


def ground_stats_by_company(visits: list[dict]) -> list[dict]:
    """Estadística de tiempo en tierra por empresa: paradas, min/prom/max,
    exceso total y cargo extra total. Las paradas con varias empresas se
    cuentan para cada una; las sin empresa van como '(sin empresa)'."""
    acc: dict[str, list[dict]] = {}
    for v in visits:
        if v.get("ground_min", 0.0) <= 0:
            continue
        for company in v["companies"] or ["(sin empresa)"]:
            acc.setdefault(company, []).append(v)
    out = []
    for company in sorted(acc):
        vs = acc[company]
        mins = [v["ground_min"] for v in vs]
        out.append(
            {
                "company": company,
                "stops": len(vs),
                "min_ground_min": min(mins),
                "avg_ground_min": sum(mins) / len(mins),
                "max_ground_min": max(mins),
                "total_excess_min": sum(v["excess_min"] for v in vs),
                "total_extra_cost": sum(v["extra_cost"] for v in vs),
            }
        )
    return out


def optimize_fleet(scn: Scenario) -> list[RoutePlan]:
    """Evalúa cada helicóptero de la flota; devuelve los planes ordenados por costo.

    El primer plan factible de la lista es el óptimo global (un solo
    helicóptero sirve todas las solicitudes).
    """
    plans = [optimize_route(scn, heli) for heli in scn.helicopters]
    plans.sort(key=lambda p: (not p.feasible, p.total_cost))
    return plans
