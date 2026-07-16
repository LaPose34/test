"""Modelos de datos: helicópteros, helipuertos, solicitudes y escenario."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Helipad:
    """Un helipuerto/punto de aterrizaje.

    La posición puede darse por coordenadas (lat/lon, en grados) y/o mediante
    la matriz de distancias explícita del escenario. Las restricciones de
    tamaño y peso determinan qué helicópteros pueden operar aquí.
    """

    id: str
    name: str = ""
    lat: float | None = None
    lon: float | None = None
    size_class: int = 3  # tamaño máximo de helicóptero admitido (1=S, 2=M, 3=L)
    max_weight_kg: float | None = None  # MTOW máximo admitido; None = sin límite
    has_fuel: bool = False  # ¿se puede recargar combustible aquí?
    banned_helicopters: frozenset[str] = frozenset()  # ids explícitamente prohibidos

    def allows(self, heli: "Helicopter") -> bool:
        """¿Puede este helicóptero operar en este helipuerto?"""
        if heli.id in self.banned_helicopters:
            return False
        if heli.size_class > self.size_class:
            return False
        if self.max_weight_kg is not None and heli.mtow_kg > self.max_weight_kg:
            return False
        return True


@dataclass(frozen=True)
class Helicopter:
    """El límite de peso lo pone el helicóptero, no la persona.

    Si se dan `empty_weight_kg` y `mtow_kg`, en CADA despegue se exige:

        peso vacío + combustible a bordo + pax + equipaje + carga ≤ MTOW

    `max_payload_kg` es un límite estructural de cabina adicional y opcional.
    """

    id: str
    pax_capacity: int
    fuel_consumption_lph: float  # litros por hora
    fuel_capacity_l: float
    cruise_speed_kmh: float
    price_per_hour: float  # tarifa horaria (sin combustible si fuel_price_per_l > 0)
    base: str  # id del helipuerto donde inicia
    name: str = ""
    max_payload_kg: float | None = None  # límite de cabina opcional
    empty_weight_kg: float = 0.0  # peso vacío operativo
    can_combine_pax_cargo: bool = True  # ¿puede llevar pax y carga en el mismo vuelo?
    mtow_kg: float = 0.0  # peso máximo de despegue (y restricción de helipads)
    size_class: int = 2  # 1=S, 2=M, 3=L

    @property
    def has_takeoff_limit(self) -> bool:
        """¿Hay datos para aplicar el límite de peso de despegue?"""
        return self.empty_weight_kg > 0 and self.mtow_kg > 0

    @property
    def endurance_h(self) -> float:
        """Horas de vuelo con tanque lleno."""
        if self.fuel_consumption_lph <= 0:
            return math.inf
        return self.fuel_capacity_l / self.fuel_consumption_lph

    @property
    def range_km(self) -> float:
        """Alcance con tanque lleno, sin reservas."""
        return self.endurance_h * self.cruise_speed_kmh


@dataclass(frozen=True)
class TransportRequest:
    """Una solicitud: mover pax y/o carga de un helipuerto a otro.

    `pax_weight_kg` es el peso REAL total de los pasajeros de esta solicitud;
    si es None se estima con `pax × Scenario.pax_weight_kg`.

    `baggage_kg` es el equipaje que viaja CON los pasajeros: cuenta contra la
    carga máxima del helicóptero (seguridad: techo de peso), pero no es
    "carga" para la regla de no mezclar pax y carga.

    `after` encadena solicitudes: esta recogida solo puede hacerse cuando la
    solicitud referida ya fue entregada. Se usa para itinerarios multi-punto
    (un pax que visita varios helipuertos antes de su destino final).
    """

    id: str
    origin: str
    destination: str
    pax: int = 0
    cargo_kg: float = 0.0
    pax_weight_kg: float | None = None
    baggage_kg: float = 0.0
    after: str | None = None


@dataclass(frozen=True)
class Passenger:
    """Una persona con su peso real, su equipaje y su itinerario.

    Por seguridad, contra el techo de peso del helicóptero cuenta el peso
    total de la persona: `weight_kg` (corporal) + `baggage_kg` (maleta). El
    equipaje viaja siempre con el pasajero.

    `via` son escalas obligatorias, en orden, antes del destino final: el
    pasajero aterriza (y puede esperar) en cada una. Internamente se expande a
    una cadena de TransportRequest enlazadas con `after`.
    """

    id: str
    weight_kg: float
    origin: str
    destination: str
    name: str = ""
    baggage_kg: float = 0.0
    via: tuple[str, ...] = ()

    @property
    def total_kg(self) -> float:
        return self.weight_kg + self.baggage_kg

    def to_requests(self) -> list[TransportRequest]:
        stops = [self.origin, *self.via, self.destination]
        chain: list[TransportRequest] = []
        for k in range(len(stops) - 1):
            rid = self.id if len(stops) == 2 else f"{self.id}#{k + 1}"
            chain.append(
                TransportRequest(
                    id=rid,
                    origin=stops[k],
                    destination=stops[k + 1],
                    pax=1,
                    pax_weight_kg=self.weight_kg,
                    baggage_kg=self.baggage_kg,
                    after=chain[-1].id if chain else None,
                )
            )
        return chain


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia ortodrómica en km entre dos puntos (lat/lon en grados)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Scenario:
    """Escenario completo: flota, red de helipuertos y demanda."""

    helipads: dict[str, Helipad]
    helicopters: list[Helicopter]
    requests: list[TransportRequest]
    # Distancias explícitas en km, clave (origen, destino). Si falta un par se
    # usa el simétrico y, en último término, haversine sobre coordenadas.
    distances: dict[tuple[str, str], float] = field(default_factory=dict)
    pax_weight_kg: float = 90.0  # peso estándar por pasajero (cuenta como payload)
    fuel_price_per_l: float = 0.0  # 0 si la tarifa horaria ya incluye combustible
    fuel_density_kg_per_l: float = 0.8  # Jet A-1 ≈ 0.8 kg/L (peso del combustible)
    return_to_base: bool = False  # ¿el helicóptero debe volver a su base al final?

    def distance_km(self, a: str, b: str) -> float:
        if a == b:
            return 0.0
        if (a, b) in self.distances:
            return self.distances[(a, b)]
        if (b, a) in self.distances:
            return self.distances[(b, a)]
        pa, pb = self.helipads[a], self.helipads[b]
        if pa.lat is None or pa.lon is None or pb.lat is None or pb.lon is None:
            raise ValueError(
                f"No hay distancia definida entre '{a}' y '{b}' y faltan coordenadas"
            )
        return haversine_km(pa.lat, pa.lon, pb.lat, pb.lon)

    def cost_per_flight_hour(self, heli: Helicopter) -> float:
        """Costo total por hora de vuelo: tarifa + combustible."""
        return heli.price_per_hour + heli.fuel_consumption_lph * self.fuel_price_per_l

    def request_pax_weight_kg(self, req: TransportRequest) -> float:
        """Peso de los pasajeros de la solicitud: real si se dio, estimado si no."""
        if req.pax_weight_kg is not None:
            return req.pax_weight_kg
        return req.pax * self.pax_weight_kg

    def request_payload_kg(self, req: TransportRequest) -> float:
        """Peso total de la solicitud contra el techo del helicóptero:
        pax + equipaje + carga."""
        return self.request_pax_weight_kg(req) + req.baggage_kg + req.cargo_kg

    def validate(self) -> None:
        ids = set()
        for heli in self.helicopters:
            if heli.id in ids:
                raise ValueError(f"Helicóptero duplicado: '{heli.id}'")
            ids.add(heli.id)
            if heli.base not in self.helipads:
                raise ValueError(
                    f"Helicóptero '{heli.id}': base '{heli.base}' no existe"
                )
        req_ids = set()
        for req in self.requests:
            if req.id in req_ids:
                raise ValueError(f"Solicitud duplicada: '{req.id}'")
            req_ids.add(req.id)
            for pad in (req.origin, req.destination):
                if pad not in self.helipads:
                    raise ValueError(
                        f"Solicitud '{req.id}': helipuerto '{pad}' no existe"
                    )
            if req.pax <= 0 and req.cargo_kg <= 0:
                raise ValueError(f"Solicitud '{req.id}': sin pax ni carga")
        by_id = {r.id: r for r in self.requests}
        for req in self.requests:
            if req.after is None:
                continue
            if req.after not in by_id:
                raise ValueError(
                    f"Solicitud '{req.id}': 'after' apunta a '{req.after}', que no existe"
                )
            # detectar ciclos en la cadena de precedencias
            seen, cur = {req.id}, req.after
            while cur is not None:
                if cur in seen:
                    raise ValueError(f"Ciclo de precedencias 'after' en '{req.id}'")
                seen.add(cur)
                cur = by_id[cur].after

    # ------------------------------------------------------------------ JSON

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        helipads = {}
        for raw in data.get("helipads", []):
            pad = Helipad(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                lat=raw.get("lat"),
                lon=raw.get("lon"),
                size_class=raw.get("size_class", 3),
                max_weight_kg=raw.get("max_weight_kg"),
                has_fuel=raw.get("has_fuel", False),
                banned_helicopters=frozenset(raw.get("banned_helicopters", [])),
            )
            helipads[pad.id] = pad

        helicopters = [
            Helicopter(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                pax_capacity=raw["pax_capacity"],
                max_payload_kg=raw.get("max_payload_kg"),
                empty_weight_kg=raw.get("empty_weight_kg", 0.0),
                fuel_consumption_lph=raw["fuel_consumption_lph"],
                fuel_capacity_l=raw["fuel_capacity_l"],
                cruise_speed_kmh=raw["cruise_speed_kmh"],
                price_per_hour=raw["price_per_hour"],
                base=raw["base"],
                can_combine_pax_cargo=raw.get("can_combine_pax_cargo", True),
                mtow_kg=raw.get("mtow_kg", 0.0),
                size_class=raw.get("size_class", 2),
            )
            for raw in data.get("helicopters", [])
        ]

        requests = [
            TransportRequest(
                id=raw["id"],
                origin=raw["origin"],
                destination=raw["destination"],
                pax=raw.get("pax", 0),
                cargo_kg=raw.get("cargo_kg", 0.0),
                pax_weight_kg=raw.get("pax_weight_kg"),
                baggage_kg=raw.get("baggage_kg", 0.0),
                after=raw.get("after"),
            )
            for raw in data.get("requests", [])
        ]

        # Los pasajeros individuales se expanden a cadenas de solicitudes
        for raw in data.get("passengers", []):
            passenger = Passenger(
                id=raw["id"],
                name=raw.get("name", raw["id"]),
                weight_kg=raw["weight_kg"],
                baggage_kg=raw.get("baggage_kg", 0.0),
                origin=raw["origin"],
                destination=raw["destination"],
                via=tuple(raw.get("via", [])),
            )
            requests.extend(passenger.to_requests())

        distances = {
            (raw["from"], raw["to"]): float(raw["km"])
            for raw in data.get("distances", [])
        }

        scenario = cls(
            helipads=helipads,
            helicopters=helicopters,
            requests=requests,
            distances=distances,
            pax_weight_kg=data.get("pax_weight_kg", 90.0),
            fuel_price_per_l=data.get("fuel_price_per_l", 0.0),
            fuel_density_kg_per_l=data.get("fuel_density_kg_per_l", 0.8),
            return_to_base=data.get("return_to_base", False),
        )
        scenario.validate()
        return scenario

    @classmethod
    def from_json_file(cls, path: str) -> "Scenario":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))
