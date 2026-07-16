# helilog — Optimización logística de helicópteros

Software que calcula la **ruta de costo mínimo** para servir un conjunto de
solicitudes de transporte (pasajeros y/o carga) entre helipuertos, evaluando
cada helicóptero de la flota y eligiendo el más barato.

Sin dependencias externas: solo Python ≥ 3.10.

## Uso

```bash
# Entrada JSON
python -m helilog examples/escenario_ejemplo.json          # salida en texto
python -m helilog examples/escenario_ejemplo.json --json   # salida en JSON

# Entrada Excel + programación de vuelo en Excel
python -m helilog examples/entrada_malvinas.xlsx --excel programacion.xlsx

# Crear una plantilla de entrada Excel vacía
python -m helilog mi_operacion.xlsx --plantilla
```

Salida: un ranking de helicópteros por costo total. Para cada uno, el plan de
vuelo tramo a tramo (distancia, horas, costo, carga a bordo, peso de despegue
y recargas de combustible), o el motivo por el que no es viable.

## Entrada y salida en Excel

La entrada Excel (`--plantilla` genera el esqueleto) tiene las hojas:

| Hoja | Contenido |
|---|---|
| `Config` | peso estándar pax, precio/densidad de combustible, regreso a base, hora de inicio |
| `Puntos` | helipuntos: id, nombre, **lat/lon**, tamaño, peso máx admitido, combustible, vetos |
| `Distancias` | **matriz de km entre puntos** (ids en primera fila y columna); prevalece sobre el cálculo por coordenadas, útil para rutas reales |
| `Helicopteros` | flota: pax, peso vacío, MTOW, cabina máx, consumo, tanque, velocidad, precio/h, mezcla pax+carga, tamaño |
| `Requerimientos` | demanda por grupos: Req, Origen, Destino, Tipo (PAX/CARGA), Cant, kg/U, Divisible |
| `Pasajeros` | personas individuales: peso, equipaje, origen, destino, escalas (via) |

La salida (`--excel rutas.xlsx`) replica el estilo del reporte operativo
clásico: hoja **Programación** con bloques por rotación (helicóptero, tiempo
horómetro, costo, filas E/D de embarque/desembarque con `DEST:`/`ORIG:`,
unidades, kg y hora estimada de cada despegue), más hojas **Resumen** (ranking
de la flota) y **Requerimientos** (eco de la demanda).

## Catálogo de modelos (capacidades permitidas en Perú)

| Modelo | Pax permitidos | MTOW | Tanque |
|---|---|---|---|
| Mi-171 | **19** | 13.000 kg | 2.615 L |
| Bell 412EP | **13** | 5.398 kg | 1.251 L |
| Airbus H145 | **10** | 3.700 kg | 723 L |
| BK117 | **10** | 3.350 kg | 697 L |
| Airbus H125 | 5 | 2.250 kg | 540 L |

En el Excel basta poner el **nombre del modelo** en la columna Nombre y dejar
las celdas técnicas vacías: se completan desde el catálogo (solo son
obligatorios Id, Base y Precio por hora). En JSON se usa `"model": "Bell 412"`.
Cualquier valor escrito manda sobre el catálogo (la configuración real de
cada matrícula puede diferir). Alias reconocidos: `Mi-171/Mi-8/Mi-17`,
`Bell 412/B412`, `H145/EC145/BK117 D-2`, `BK117/BK117 C-2`, `H125/AS350`.

## Combustible como decisión

Cuánto combustible cargar en cada parada con recarga lo decide el
optimizador entre dos políticas, con vuelta atrás si una no funciona:

- **al tope** que permite el peso de despegue (máximo alcance, menos recargas), o
- **el mínimo seguro**: el tramo actual más el salto desde el destino hasta la
  recarga más cercana (mínimo peso ⇒ máxima carga útil en el destino).

Esto refleja la operación real: para recoger una carga pesada en una
plataforma sin combustible conviene llegar con poco combustible.

### Requerimientos divisibles (rotaciones)

Un requerimiento marcado como **Divisible** se reparte automáticamente en
varios viajes cuando no cabe completo: por ejemplo, 29 pax con un helicóptero
de 10 asientos/1.000 kg de cabina se planifican como 10 + 10 + 9 en tres
rotaciones (`P08/1`, `P08/2`, `P08/3`), igual que la operación real. El tamaño
de cada parte se calcula por helicóptero según sus asientos, cabina y MTOW.

## Qué modela

**Helicóptero** (`helicopters[]` en el JSON):

| Campo | Significado |
|---|---|
| `pax_capacity` | capacidad de pasajeros |
| `empty_weight_kg` | peso vacío operativo; junto con `mtow_kg` activa el límite de despegue |
| `max_payload_kg` | (opcional) límite estructural de cabina adicional |
| `fuel_consumption_lph` | consumo de combustible en litros por hora |
| `fuel_capacity_l` | capacidad del tanque (define la autonomía) |
| `cruise_speed_kmh` | velocidad crucero |
| `vertical_speed_ms` | velocidad vertical (m/s): cada despegue+aterrizaje suma tiempo de ascenso y descenso a la altitud de crucero (`cruise_altitude_m`, 300 m por defecto) |
| `price_per_hour` | tarifa por hora de vuelo |
| `can_combine_pax_cargo` | ¿puede llevar pax y carga en el mismo vuelo? |
| `mtow_kg` | **peso máximo de despegue**; también se usa para la habilitación en helipuertos |
| `size_class` | tamaño (1=S, 2=M, 3=L), para la habilitación en helipuertos |
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

**Pasajeros individuales** (`passengers[]`): personas con su **peso real**:

```json
{ "id": "P2", "name": "Luis Torres", "weight_kg": 102, "baggage_kg": 18,
  "origin": "BASE", "destination": "PLAT-B", "via": ["PLAT-A"] }
```

- **Seguridad — límite de despegue**: no hay límite de peso por persona; el
  límite lo pone el helicóptero. En **cada despegue** se exige:

  ```
  peso vacío + combustible a bordo + pax + equipaje + carga  ≤  MTOW
  ```

  con el peso real de cada persona (`weight_kg` + `baggage_kg`) y el peso del
  combustible (`fuel_density_kg_per_l`, 0.8 kg/L por defecto). En helipuertos
  con combustible se carga **solo hasta lo que el MTOW permite** con la carga
  a bordo (más carga útil ⇒ menos combustible). Cada tramo del plan reporta el
  peso de despegue contra el MTOW y los litros a bordo; el optimizador nunca
  genera un despegue que lo exceda.
- El equipaje viaja siempre con su pasajero y **no** cuenta como "carga" para
  la regla de no mezclar pax y carga.
- `via` (opcional) son **escalas obligatorias en orden**: el pasajero aterriza
  en cada una antes de seguir a su destino final. Internamente cada tramo se
  vuelve una solicitud encadenada (`P2#1`, `P2#2`, …).
- Además, cualquier pasajero puede **seguir a bordo** mientras el helicóptero
  hace otras paradas intermedias camino a su destino — el optimizador lo
  aprovecha cuando abarata la ruta.

**Solicitudes** (`requests[]`): grupos de pax y/o carga:
`{"id", "origin", "destination", "pax", "cargo_kg", "pax_weight_kg", "baggage_kg", "after"}`.
`pax_weight_kg` (opcional) es el peso corporal real total del grupo y
`baggage_kg` su equipaje; `after` (opcional) indica que esta recogida solo
puede hacerse tras entregar otra solicitud.

**Parámetros globales**: `pax_weight_kg`, `fuel_price_per_l` (0 si la tarifa
horaria ya incluye combustible) y `return_to_base`.

## Cómo optimiza

- Horas de un tramo = `km / velocidad crucero + 2 × altitud / velocidad vertical`:
  el tiempo de maniobra vertical se paga en **cada** despegue y aterrizaje, así
  que el perfil de los pedidos decide el modelo — tramos largos favorecen al
  crucero rápido; muchas paradas cortas favorecen al de ascenso rápido y hora
  barata, aunque su tarifa sea mayor.
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
