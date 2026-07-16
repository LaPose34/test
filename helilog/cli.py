"""CLI: `python -m helilog escenario.json` imprime el plan óptimo por costo."""

from __future__ import annotations

import argparse
import json
import sys

from .models import Scenario
from .optimizer import RoutePlan, optimize_fleet


def _format_plan(plan: RoutePlan, scn: Scenario) -> str:
    heli = next(h for h in scn.helicopters if h.id == plan.helicopter_id)
    name = heli.name or heli.id
    if not plan.feasible:
        return f"✗ {name}: NO VIABLE — {plan.infeasible_reason}"

    lines = [
        f"✓ {name} — costo total: {plan.total_cost:,.2f}"
        f" | {plan.total_km:,.1f} km | {plan.total_hours:.2f} h"
        f" | método: {plan.method}"
    ]
    for i, leg in enumerate(plan.legs, 1):
        fuel = " [recarga]" if leg.refueled_before else ""
        load = (
            f" a bordo: {leg.pax_onboard} pax {leg.pax_kg_onboard:,.0f} kg"
            f" + equipaje {leg.baggage_kg_onboard:,.0f} kg"
            f" + carga {leg.cargo_onboard_kg:,.0f} kg"
            f" = {leg.payload_kg:,.0f} kg"
        )
        if heli.max_payload_kg is not None:
            load += f" (cabina máx {heli.max_payload_kg:,.0f})"
        takeoff = ""
        if leg.takeoff_kg is not None:
            takeoff = (
                f"; despegue {leg.takeoff_kg:,.0f}/{heli.mtow_kg:,.0f} kg"
                f" con {leg.fuel_l_takeoff:,.0f} L"
            )
        lines.append(
            f"    {i}. {leg.from_pad} → {leg.to_pad}{fuel}: {leg.action}"
            f" ({leg.km:,.1f} km, {leg.hours:.2f} h, costo {leg.cost:,.2f};"
            f"{load}{takeoff})"
        )
    return "\n".join(lines)


def _plan_to_dict(plan: RoutePlan) -> dict:
    return {
        "helicopter_id": plan.helicopter_id,
        "feasible": plan.feasible,
        "infeasible_reason": plan.infeasible_reason,
        "method": plan.method,
        "total_km": round(plan.total_km, 3),
        "total_hours": round(plan.total_hours, 4),
        "total_cost": round(plan.total_cost, 2),
        "legs": [
            {
                "from": leg.from_pad,
                "to": leg.to_pad,
                "km": round(leg.km, 3),
                "hours": round(leg.hours, 4),
                "cost": round(leg.cost, 2),
                "action": leg.action,
                "pax_onboard": leg.pax_onboard,
                "pax_kg_onboard": round(leg.pax_kg_onboard, 1),
                "baggage_kg_onboard": round(leg.baggage_kg_onboard, 1),
                "cargo_onboard_kg": round(leg.cargo_onboard_kg, 1),
                "payload_kg": round(leg.payload_kg, 1),
                "fuel_l_takeoff": round(leg.fuel_l_takeoff, 1),
                "takeoff_kg": round(leg.takeoff_kg, 1) if leg.takeoff_kg is not None else None,
                "refueled_before": leg.refueled_before,
            }
            for leg in plan.legs
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="helilog",
        description="Optimización logística de helicópteros: ruta de costo mínimo.",
    )
    parser.add_argument("scenario", help="ruta al archivo JSON del escenario")
    parser.add_argument(
        "--json", action="store_true", help="salida en JSON en lugar de texto"
    )
    args = parser.parse_args(argv)

    try:
        scn = Scenario.from_json_file(args.scenario)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error al leer el escenario: {exc}", file=sys.stderr)
        return 2

    plans = optimize_fleet(scn)

    if args.json:
        print(json.dumps([_plan_to_dict(p) for p in plans], ensure_ascii=False, indent=2))
    else:
        print(f"Solicitudes: {len(scn.requests)} | Helicópteros: {len(scn.helicopters)}")
        print("Ranking por costo (el primero viable es el óptimo):\n")
        for plan in plans:
            print(_format_plan(plan, scn))
            print()

    return 0 if any(p.feasible for p in plans) else 1
