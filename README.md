# Dashboard Antamina — Alerta Temprana

Dashboard estático de alertas tempranas de conflicto social para las 4 UGTs de ANTAMINA en Áncash.

## Arquitectura

```
Google Sheet → export_sheet_antamina.py → data/data.json → index.html
```

## Flujo semanal (cada viernes)

1. Correr `score_ancash.py` en el repo ML (`c:\ML alertas`)
2. Pegar los 4 valores en la pestaña `scoring_modelo` del Sheet
3. Agregar 4 filas en la pestaña `scoring_separadas`
4. Desde esta carpeta: `python scripts/export_sheet_antamina.py`
5. `git add data/data.json && git commit -m "scoring YYYY-MM-DD" && git push`

## Configuración

- **Google Sheet ID:** `1MynxHolQszQYAoGn9V6XhpelN7ru2AxybzqUtp6soPg`
- **Modelo:** Logistic Regression · horizonte 30 días · `y_30`
- **Umbrales:** ALTO ≥ 80% · MEDIO 44–79% · BAJO < 44%

## UGTs

| ID | Nombre | Provincia |
|---|---|---|
| `mina_san_marcos` | Mina San Marcos | Huari, Áncash |
| `huallanca` | Huallanca | Bolognesi, Áncash |
| `huarmey` | Huarmey | Huarmey, Áncash |
| `valle_fortaleza` | Valle Fortaleza | Recuay / Bolognesi, Áncash |
