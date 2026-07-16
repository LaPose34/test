# helilog — Optimización logística de helicópteros

Software que calcula la **ruta de costo mínimo** para servir un conjunto de
solicitudes de transporte (pasajeros y/o carga) entre helipuertos, evaluando
cada helicóptero de la flota y eligiendo el más barato.

Sin dependencias externas: solo Python ≥ 3.10.

## Uso

```bash
python -m helilog examples/escenario_ejemplo.json          # salida en texto
python -m helilog examples/escenario_ejemplo.json --json   # salida en JSON
```

Salida: un ranking de helicópteros por costo total. Para cada uno, el plan de
vuelo tramo a tramo (distancia, horas, costo, carga a bordo y recargas de
combustible), o el motivo por el que no es viable.

## Qué modela

**Helicóptero** (`helicopters[]` en el JSON):

| Campo | Significado |
|---|---|
| `pax_capacity` | capacidad de pasajeros |
| `max_payload_kg` | carga máxima en kg (los pax cuentan con `pax_weight_kg`, 90 kg por defecto) |
| `fuel_consumption_lph` | consumo de combustible en litros por hora |
| `fuel_capacity_l` | capacidad del tanque (define la autonomía) |
| `cruise_speed_kmh` | velocidad crucero |
| `price_per_hour` | tarifa por hora de vuelo |
| `can_combine_pax_cargo` | ¿puede llevar pax y carga en el mismo vuelo? |
| `mtow_kg`, `size_class` | peso y tamaño (1=S, 2=M, 3=L), para la habilitación en helipuertos |
| `base` | helipuerto donde inicia (y termina, si `return_to_base` es `true`) |

**Helipuerto** (`helipads[]`):

| Campo | Significado |
|---|---|
| `lat`, `lon` | coordenadas; la distancia se calcula por haversine si no hay distancia explícita |
| `size_class` | tamaño máximo de helicóptero admitido |
| `max_weight_kg` | MTOW máximo admitido (helipuertos con límite estructural) |
| `has_fuel` | ¿se puede recargar combustible aquí? |
| `banned_helicopters` | ids de helicópteros vetados explícitamente |

**Distancias** (`distances[]`, opcional): pares `{"from", "to", "km"}` que
tienen prioridad sobre el cálculo por coordenadas (útil para rutas reales que
no son línea recta).

**Solicitudes** (`requests[]`): `{"id", "origin", "destination", "pax", "cargo_kg"}`.

**Parámetros globales**: `pax_weight_kg`, `fuel_price_per_l` (0 si la tarifa
horaria ya incluye combustible) y `return_to_base`.

## Cómo optimiza

- Costo de un tramo = horas de vuelo × (tarifa horaria + consumo L/h × precio del litro).
- Para cada helicóptero se resuelve un problema de recogida y entrega con
  **búsqueda exacta (ramificación y poda)** hasta 8 solicitudes; por encima se
  usa una heurística voraz (el plan indica el método usado).
- Restricciones respetadas: capacidad de pax, peso máximo (pax + carga),
  prohibición de mezclar pax/carga, habilitación helicóptero↔helipuerto
  (tamaño, peso, vetos) y autonomía de combustible con recarga en helipuertos
  que tengan `has_fuel`.
- El optimizador consolida solicitudes en un mismo vuelo cuando abarata el
  plan, y divide en varios viajes cuando la capacidad lo exige.

Limitación actual: cada plan asigna **todas** las solicitudes a un solo
helicóptero y compara la flota; el reparto de solicitudes entre varios
helicópteros a la vez es una extensión futura.

## Tests

```bash
python tests/test_optimizer.py    # o: python -m pytest
```

## Uso como librería

```python
from helilog import Scenario, optimize_fleet

scn = Scenario.from_json_file("examples/escenario_ejemplo.json")
for plan in optimize_fleet(scn):
    print(plan.helicopter_id, plan.feasible, plan.total_cost)
```

## Referencias

- [MiroFish](https://github.com/666ghj/MiroFish) — Motor de predicción basado en IA con tecnología multiagente que simula resultados futuros creando mundos digitales poblados por miles de agentes inteligentes.
