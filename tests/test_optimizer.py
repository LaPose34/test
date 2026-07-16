"""Tests del optimizador. Ejecutar con `python -m pytest` o `python tests/test_optimizer.py`."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from helilog import Helicopter, Helipad, Passenger, Scenario, TransportRequest
from helilog.optimizer import optimize_fleet, optimize_route


def _pad(pid, **kw):
    return Helipad(id=pid, name=pid, **kw)


def _heli(hid, **kw):
    defaults = dict(
        pax_capacity=6,
        max_payload_kg=1200,
        fuel_consumption_lph=200,
        fuel_capacity_l=600,
        cruise_speed_kmh=200,
        price_per_hour=2000,
        base="A",
        size_class=1,
        mtow_kg=2500,
    )
    defaults.update(kw)
    return Helicopter(id=hid, **defaults)


def _scn(**kw):
    """Triángulo A-B-C con distancias explícitas."""
    defaults = dict(
        helipads={
            "A": _pad("A", has_fuel=True),
            "B": _pad("B"),
            "C": _pad("C"),
        },
        helicopters=[_heli("H1")],
        requests=[],
        distances={("A", "B"): 100.0, ("B", "C"): 100.0, ("A", "C"): 150.0},
    )
    defaults.update(kw)
    return Scenario(**defaults)


def test_ruta_simple_costo():
    scn = _scn(requests=[TransportRequest("R1", "A", "B", pax=2)])
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    # 100 km a 200 km/h = 0.5 h; costo/h = 2000 (fuel_price = 0)
    assert abs(plan.total_cost - 1000.0) < 1e-6
    assert plan.total_km == 100.0
    assert [l.action for l in plan.legs] == ["recoger R1", "entregar R1"]


def test_orden_optimo_multiples_solicitudes():
    # Servir B→C y A→B: lo óptimo es recoger en A, entregar en B, recoger, entregar en C
    scn = _scn(
        requests=[
            TransportRequest("R1", "B", "C", pax=1),
            TransportRequest("R2", "A", "B", pax=1),
        ]
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    assert plan.total_km == 200.0  # A→B→C sin retrocesos
    assert plan.method == "exacto"


def test_consolidacion_en_un_vuelo():
    # Dos solicitudes con el mismo par origen-destino viajan juntas
    scn = _scn(
        requests=[
            TransportRequest("R1", "A", "B", pax=2),
            TransportRequest("R2", "A", "B", pax=3),
        ]
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    assert plan.total_km == 100.0


def test_capacidad_pax_obliga_dos_viajes():
    scn = _scn(
        requests=[
            TransportRequest("R1", "A", "B", pax=4),
            TransportRequest("R2", "A", "B", pax=4),
        ]
    )
    plan = optimize_route(scn, scn.helicopters[0])  # capacidad 6 < 8
    assert plan.feasible
    assert plan.total_km == 300.0  # A→B, B→A, A→B


def test_pax_cuentan_como_peso():
    scn = _scn(requests=[TransportRequest("R1", "A", "B", pax=6, cargo_kg=800)])
    # 6*90 + 800 = 1340 > 1200
    plan = optimize_route(scn, scn.helicopters[0])
    assert not plan.feasible
    assert "techo de peso" in plan.infeasible_reason


def test_sin_mezcla_pax_carga():
    heli = _heli("H1", can_combine_pax_cargo=False)
    scn = _scn(
        helicopters=[heli],
        requests=[
            TransportRequest("R1", "A", "B", pax=2),
            TransportRequest("R2", "A", "B", cargo_kg=200),
        ],
    )
    plan = optimize_route(scn, heli)
    assert plan.feasible
    assert plan.total_km == 300.0  # no pueden compartir vuelo → dos viajes
    for leg in plan.legs:
        assert not (leg.pax_onboard > 0 and leg.cargo_onboard_kg > 0)


def test_helipad_no_habilitado_por_tamano():
    scn = _scn(
        helipads={
            "A": _pad("A", has_fuel=True),
            "B": _pad("B", size_class=1),
            "C": _pad("C"),
        },
        helicopters=[_heli("H1", size_class=2)],
        requests=[TransportRequest("R1", "A", "B", pax=1)],
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert not plan.feasible
    assert "'B' no admite" in plan.infeasible_reason


def test_helipad_no_habilitado_por_peso():
    scn = _scn(
        helipads={
            "A": _pad("A", has_fuel=True),
            "B": _pad("B", max_weight_kg=2000),
            "C": _pad("C"),
        },
        requests=[TransportRequest("R1", "A", "B", pax=1)],
    )
    plan = optimize_route(scn, scn.helicopters[0])  # mtow 2500 > 2000
    assert not plan.feasible


def test_autonomia_combustible():
    # Autonomía: 600/200 = 3 h → 600 km por tanque. Tramo de 700 km inviable.
    scn = _scn(
        distances={("A", "B"): 700.0, ("B", "C"): 100.0, ("A", "C"): 650.0},
        requests=[TransportRequest("R1", "A", "B", pax=1)],
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert not plan.feasible


def test_recarga_en_helipad_con_combustible():
    # 500 km ida + 500 vuelta: imposible sin recargar (600 km de autonomía),
    # posible recargando en B.
    scn = _scn(
        helipads={
            "A": _pad("A", has_fuel=True),
            "B": _pad("B", has_fuel=True),
            "C": _pad("C"),
        },
        distances={("A", "B"): 500.0, ("B", "C"): 100.0, ("A", "C"): 550.0},
        requests=[
            TransportRequest("R1", "A", "B", pax=1),
            TransportRequest("R2", "B", "A", pax=1),
        ],
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    assert any(l.refueled_before for l in plan.legs)


def test_costo_incluye_combustible():
    scn = _scn(
        fuel_price_per_l=2.0,
        requests=[TransportRequest("R1", "A", "B", pax=1)],
    )
    plan = optimize_route(scn, scn.helicopters[0])
    # costo/h = 2000 + 200 L/h * 2.0 = 2400; 0.5 h → 1200
    assert abs(plan.total_cost - 1200.0) < 1e-6


def test_regreso_a_base():
    scn = _scn(
        return_to_base=True,
        requests=[TransportRequest("R1", "A", "B", pax=1)],
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    assert plan.legs[-1].to_pad == "A"
    assert plan.total_km == 200.0


def test_flota_elige_mas_barato():
    caro = _heli("CARO", price_per_hour=5000)
    barato = _heli("BARATO", price_per_hour=1500)
    scn = _scn(
        helicopters=[caro, barato],
        requests=[TransportRequest("R1", "A", "B", pax=1)],
    )
    plans = optimize_fleet(scn)
    assert plans[0].helicopter_id == "BARATO"
    assert plans[0].total_cost < plans[1].total_cost


def test_peso_individual_de_pasajeros():
    # Dos personas de 150 kg: caben por pax (6) pero no por peso juntos
    heli = _heli("H1", max_payload_kg=200)
    scn = _scn(
        helicopters=[heli],
        requests=[
            TransportRequest("P1", "A", "B", pax=1, pax_weight_kg=150.0),
            TransportRequest("P2", "A", "B", pax=1, pax_weight_kg=150.0),
        ],
    )
    plan = optimize_route(scn, heli)
    assert plan.feasible
    assert plan.total_km == 300.0  # dos viajes por peso real, no por conteo


def test_peso_individual_permite_compartir():
    # Las mismas dos personas pero de 90 kg sí comparten vuelo
    heli = _heli("H1", max_payload_kg=200)
    scn = _scn(
        helicopters=[heli],
        requests=[
            TransportRequest("P1", "A", "B", pax=1, pax_weight_kg=90.0),
            TransportRequest("P2", "A", "B", pax=1, pax_weight_kg=90.0),
        ],
    )
    plan = optimize_route(scn, heli)
    assert plan.feasible
    assert plan.total_km == 100.0


def test_equipaje_cuenta_contra_techo_de_peso():
    # 90 kg persona + 30 kg maleta cada uno: 2 × 120 = 240 > 200 → dos viajes
    heli = _heli("H1", max_payload_kg=200)
    scn = _scn(
        helicopters=[heli],
        requests=[
            TransportRequest("P1", "A", "B", pax=1, pax_weight_kg=90.0, baggage_kg=30.0),
            TransportRequest("P2", "A", "B", pax=1, pax_weight_kg=90.0, baggage_kg=30.0),
        ],
    )
    plan = optimize_route(scn, heli)
    assert plan.feasible
    assert plan.total_km == 300.0
    for leg in plan.legs:
        assert leg.payload_kg <= heli.max_payload_kg


def test_equipaje_excede_techo_es_inviable():
    heli = _heli("H1", max_payload_kg=100)
    scn = _scn(
        helicopters=[heli],
        requests=[TransportRequest("P1", "A", "B", pax=1, pax_weight_kg=85.0, baggage_kg=25.0)],
    )
    plan = optimize_route(scn, heli)
    assert not plan.feasible
    assert "techo de peso" in plan.infeasible_reason


def test_equipaje_no_es_carga_para_regla_de_mezcla():
    # Helicóptero que NO mezcla pax y carga: la maleta del pax SÍ puede volar con él
    heli = _heli("H1", can_combine_pax_cargo=False)
    scn = _scn(
        helicopters=[heli],
        requests=[TransportRequest("P1", "A", "B", pax=1, pax_weight_kg=90.0, baggage_kg=20.0)],
    )
    plan = optimize_route(scn, heli)
    assert plan.feasible
    assert plan.total_km == 100.0


def test_pasajero_json_con_equipaje():
    p = Passenger(id="P", weight_kg=80, baggage_kg=15, origin="A", destination="B")
    assert p.total_kg == 95
    reqs = p.to_requests()
    assert len(reqs) == 1
    assert reqs[0].pax_weight_kg == 80 and reqs[0].baggage_kg == 15


def test_pasajero_multi_punto_via():
    # P debe aterrizar en B antes de llegar a C: la ruta directa A→C (150 km)
    # queda prohibida; la ruta obligada es A→B→C (200 km).
    passenger = Passenger(id="P", weight_kg=80, origin="A", destination="C", via=("B",))
    scn = _scn(requests=passenger.to_requests())
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    assert plan.total_km == 200.0
    actions = [l.action for l in plan.legs]
    assert actions.index("entregar P#1") < actions.index("recoger P#2")


def test_precedencia_after_se_respeta():
    # R2 solo puede recogerse tras entregar R1 (mismo pad B)
    scn = _scn(
        requests=[
            TransportRequest("R1", "A", "B", pax=1),
            TransportRequest("R2", "B", "C", pax=1, after="R1"),
        ]
    )
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    actions = [l.action for l in plan.legs]
    assert actions.index("entregar R1") < actions.index("recoger R2")


def test_after_invalido_se_rechaza():
    try:
        _scn(
            requests=[TransportRequest("R1", "A", "B", pax=1, after="NO-EXISTE")]
        ).validate()
    except ValueError as exc:
        assert "after" in str(exc)
    else:
        raise AssertionError("se esperaba ValueError por 'after' inexistente")


def test_passengers_en_json():
    scn = Scenario.from_dict(
        {
            "helipads": [
                {"id": "A", "has_fuel": True},
                {"id": "B"},
                {"id": "C"},
            ],
            "distances": [
                {"from": "A", "to": "B", "km": 100},
                {"from": "B", "to": "C", "km": 100},
                {"from": "A", "to": "C", "km": 150},
            ],
            "helicopters": [
                {
                    "id": "H1",
                    "pax_capacity": 6,
                    "max_payload_kg": 1200,
                    "fuel_consumption_lph": 200,
                    "fuel_capacity_l": 600,
                    "cruise_speed_kmh": 200,
                    "price_per_hour": 2000,
                    "base": "A",
                    "size_class": 1,
                }
            ],
            "passengers": [
                {"id": "P1", "weight_kg": 95, "origin": "A", "destination": "C", "via": ["B"]},
                {"id": "P2", "weight_kg": 70, "origin": "A", "destination": "B"},
            ],
        }
    )
    assert len(scn.requests) == 3  # P1 se expande en 2 tramos encadenados
    plan = optimize_route(scn, scn.helicopters[0])
    assert plan.feasible
    assert plan.total_km == 200.0  # ambos comparten A→B; P1 sigue a C


def test_escenario_ejemplo_json():
    path = os.path.join(os.path.dirname(__file__), "..", "examples", "escenario_ejemplo.json")
    scn = Scenario.from_json_file(path)
    plans = optimize_fleet(scn)
    assert plans[0].feasible
    assert plans[0].helicopter_id == "XA-H125"  # el B412 no cabe en PLAT-B
    assert not plans[1].feasible


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"OK  {fn.__name__}")
    print(f"\n{len(fns)} tests pasaron")
