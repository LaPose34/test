"""Catálogo de modelos de helicóptero con capacidades permitidas en Perú.

Capacidad de pasajeros según lo permitido en operación en Perú:
Mi-171 → 19 pax, Bell 412 → 13 pax, H145 → 10 pax, H125 → 5 pax.

Los demás valores (peso vacío, MTOW, tanque, consumo, velocidad) son
típicos del modelo y sirven como punto de partida: cualquier campo puede
sobreescribirse por aeronave (la configuración real de cada matrícula manda).
"""

from __future__ import annotations

from .models import Helicopter

SPECS: dict[str, dict] = {
    "MI171": dict(
        name="Mi-171",
        pax_capacity=19,  # permitido en Perú
        empty_weight_kg=7100,
        mtow_kg=13000,
        fuel_capacity_l=2615,
        fuel_consumption_lph=650,
        cruise_speed_kmh=230,
        size_class=3,
    ),
    "BELL412": dict(
        name="Bell 412EP",
        pax_capacity=13,  # permitido en Perú
        empty_weight_kg=3079,
        mtow_kg=5398,
        fuel_capacity_l=1251,
        fuel_consumption_lph=420,
        cruise_speed_kmh=226,
        size_class=2,
    ),
    "H145": dict(
        name="Airbus H145",
        pax_capacity=10,  # permitido en Perú
        empty_weight_kg=1970,
        mtow_kg=3700,
        fuel_capacity_l=723,
        fuel_consumption_lph=260,
        cruise_speed_kmh=240,
        size_class=2,
    ),
    "BK117": dict(
        name="BK117",
        pax_capacity=10,  # permitido en Perú
        empty_weight_kg=1745,
        mtow_kg=3350,
        fuel_capacity_l=697,
        fuel_consumption_lph=250,
        cruise_speed_kmh=230,
        size_class=2,
    ),
    "H125": dict(
        name="Airbus H125",
        pax_capacity=5,
        empty_weight_kg=1265,
        mtow_kg=2250,
        fuel_capacity_l=540,
        fuel_consumption_lph=195,
        cruise_speed_kmh=220,
        size_class=1,
    ),
}

_ALIASES = {
    "MI171": "MI171",
    "MI8": "MI171",
    "MI17": "MI171",
    "BELL412": "BELL412",
    "B412": "BELL412",
    "BELL412EP": "BELL412",
    "412": "BELL412",
    "H145": "H145",
    "EC145": "H145",
    "BK117": "BK117",
    "BK117C2": "BK117",
    "BK117D2": "H145",  # el D-2 es la variante comercializada como H145
    "H125": "H125",
    "AS350": "H125",
    "ECUREUIL": "H125",
    "ARDILLA": "H125",
}


def _norm(text: str) -> str:
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def find(model_or_name: str) -> dict | None:
    """Busca un modelo por nombre flexible ('Mi-171', 'bell 412', 'EC-145'...)."""
    key = _norm(model_or_name)
    if not key:
        return None
    if key in _ALIASES:
        return dict(SPECS[_ALIASES[key]])
    # coincidencia por contenido: "AIRBUSH145", "BELL412EPHP", etc.
    for alias, canonical in _ALIASES.items():
        if alias in key:
            return dict(SPECS[canonical])
    return None


def make(model: str, id: str, base: str, price_per_hour: float, **overrides) -> Helicopter:
    """Crea un helicóptero desde el catálogo; `overrides` ajusta cualquier campo."""
    specs = find(model)
    if specs is None:
        raise ValueError(
            f"Modelo '{model}' no está en el catálogo ({', '.join(sorted(SPECS))})"
        )
    specs.update(overrides)
    return Helicopter(id=id, base=base, price_per_hour=price_per_hour, **specs)
