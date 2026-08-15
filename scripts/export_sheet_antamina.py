#!/usr/bin/env python3
"""
Exporta el Google Sheet del modelo predictivo Antamina a data/data.json
para el dashboard estático de alertas tempranas.

El Sheet debe estar compartido como "Cualquier persona con el enlace: Lector".
Sin credenciales — acceso público de solo lectura.

Pestaňas requeridas (por nombre):
    scoring_modelo          — Predicción semanal por UGT (4 filas)
    scoring_separadas       — Serie histórica OOF + live scoring (N filas)
    features_historicas     — Features de contexto por UGT por semana
    calibracion_modelo      — Métricas del modelo (estático)
    calendario_criticidad   — Ventanas de conflicto recurrentes de Áncash
    alertas_electorales_2026 — Fases ERM 2026 con riesgo operativo
    eventos_historicos      — Protestas/bloqueos documentados
    actores_criticos        — Catálogo de actores clave

Uso:
    python scripts/export_sheet_antamina.py
    python scripts/export_sheet_antamina.py --sheet-id=<ID>
"""
import argparse
import csv
import io
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# ── Configuración ──────────────────────────────────────────────────────────────
# Reemplazar por el ID del Google Sheet de Antamina (parte de la URL entre /d/ y /edit)
SHEET_ID = "1MynxHolQszQYAoGn9V6XhpelN7ru2AxybzqUtp6soPg"

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "data.json"

# UGTs del modelo (en orden de despliegue en el dashboard)
ALL_UGTS = ["mina_san_marcos", "huallanca", "huarmey", "valle_fortaleza"]

# Nombres y colores para cada UGT
UGTS_META = {
    "mina_san_marcos": {
        "label": "Mina San Marcos",
        "provincia": "Huari, Áncash",
        "color": "#5b9cf6",
        "mapa_x": 32.0,   # % posición X en el mapa
        "mapa_y": 14.0,   # % posición Y en el mapa
    },
    "huallanca": {
        "label": "Huallanca",
        "provincia": "Bolognesi, Áncash",
        "color": "#a78bfa",
        "mapa_x": 67.0,
        "mapa_y": 28.0,
    },
    "huarmey": {
        "label": "Huarmey",
        "provincia": "Huarmey, Áncash",
        "color": "#f87171",
        "mapa_x": 24.0,
        "mapa_y": 41.0,
    },
    "valle_fortaleza": {
        "label": "Valle Fortaleza",
        "provincia": "Recuay / Bolognesi, Áncash",
        "color": "#34d399",
        "mapa_x": 53.0,
        "mapa_y": 56.0,
    },
}

# Umbrales de alerta (calibrados 2026-06-30)
UMBRAL_ALTO  = 0.80
UMBRAL_MEDIO = 0.44

# 3 niveles para Antamina (NO tiene MEDIO ALTO)
NIVEL_ORDER = ["BAJO", "MEDIO", "ALTO"]


# ── Fetch de Sheet ─────────────────────────────────────────────────────────────
def fetch_tab(sheet_id, tab_name):
    """Lee una pestaña del Sheet por nombre como lista de dicts."""
    import urllib.request
    url = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={tab_name}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
        return list(csv.DictReader(io.StringIO(raw)))
    except Exception as exc:
        print(f"[export] no se pudo leer '{tab_name}': {exc}", file=sys.stderr)
        return []


# ── Utilidades ─────────────────────────────────────────────────────────────────
def to_float(v, default=None):
    try:
        return float(v) if v not in (None, "") else default
    except (ValueError, TypeError):
        return default


def to_int(v, default=None):
    f = to_float(v, default)
    return int(f) if f is not None else default


def nivel_from_prob(prob):
    if prob is None:
        return None
    if prob >= UMBRAL_ALTO:
        return "ALTO"
    if prob >= UMBRAL_MEDIO:
        return "MEDIO"
    return "BAJO"


def con_ewm(serie, span=3):
    """EWM visual-only (span=3). Agrega prob_suavizado; no toca prob."""
    alpha = 2.0 / (span + 1)
    por_ugt = {}
    for p in serie:
        por_ugt.setdefault(p["ugt"], []).append(p)
    for pts in por_ugt.values():
        pts.sort(key=lambda p: p["semana"])
        prev = None
        for p in pts:
            if p["prob"] is None:
                p["prob_suavizado"] = prev
                continue
            prev = p["prob"] if prev is None else alpha * p["prob"] + (1 - alpha) * prev
            p["prob_suavizado"] = round(prev, 6)
    return serie


# ── Parsers de fechas ──────────────────────────────────────────────────────────
MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def parse_periodo_electoral(texto):
    """'15 julio–5 agosto 2026' / '6-20 agosto 2026' / '4 octubre 2026'
    → (date_inicio, date_fin) o None."""
    s = (texto or "").strip()
    pat_rango_2meses = re.match(
        r"^(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s*[–-]\s*(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s+(\d{4})$", s)
    if pat_rango_2meses:
        d1, m1, d2, m2, y = pat_rango_2meses.groups()
        ma, mb = MESES_ES.get(m1.lower()), MESES_ES.get(m2.lower())
        if ma and mb:
            return date(int(y), ma, int(d1)), date(int(y), mb, int(d2))
    pat_rango_1mes = re.match(
        r"^(\d{1,2})\s*[–-]\s*(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s+(\d{4})$", s)
    if pat_rango_1mes:
        d1, d2, m, y = pat_rango_1mes.groups()
        mo = MESES_ES.get(m.lower())
        if mo:
            return date(int(y), mo, int(d1)), date(int(y), mo, int(d2))
    pat_dia_mes = re.match(
        r"^(\d{1,2})\s+([A-Za-záéíóúñÁÉÍÓÚÑ]+)\s+(\d{4})$", s)
    if pat_dia_mes:
        d, m, y = pat_dia_mes.groups()
        mo = MESES_ES.get(m.lower())
        if mo:
            dt = date(int(y), mo, int(d))
            return dt, dt
    return None


def next_occurrence(mm_dd, today):
    """Próxima fecha (≥ hoy) de un evento recurrente anual 'MM-DD'."""
    month, day = (int(x) for x in mm_dd.split("-"))
    candidate = date(today.year, month, day)
    if candidate < today:
        candidate = date(today.year + 1, month, day)
    return candidate


# ── UGT keywords para alertas electorales ─────────────────────────────────────
ZONA_KEYWORDS = {
    "mina_san_marcos": ["san marcos", "huari", "chavín", "chavin", "huachis", "chana"],
    "huallanca":       ["huallanca", "aquia", "bolognesi", "chiquián", "chiquian"],
    "huarmey":         ["huarmey", "paramonga", "pativilca", "punta lobitos"],
    "valle_fortaleza": ["recuay", "independencia", "llacllin", "cajacay", "colcabamba"],
}
ZONA_KEYWORDS_AMPLIAS = ["regionales", "todos los distritos", "capitales provinciales", "los 18 distritos"]


def ugts_para_zonas_prioritarias(texto):
    t = (texto or "").lower()
    ugts = set()
    for ugt_id, palabras in ZONA_KEYWORDS.items():
        if any(p in t for p in palabras):
            ugts.add(ugt_id)
    if any(p in t for p in ZONA_KEYWORDS_AMPLIAS):
        ugts.update(ALL_UGTS)
    if not ugts:
        ugts.update(ALL_UGTS)  # no reconocido → mostrar en todas
    return ugts


# ── Builds principales ─────────────────────────────────────────────────────────
def build_serie_tiempo(scoring_separadas_rows):
    """Lee la pestaña scoring_separadas (OOF + live scoring) y aplica EWM."""
    serie = []
    for row in scoring_separadas_rows:
        ugt = (row.get("ugt") or "").strip()
        semana = (row.get("semana") or "").strip()
        if not ugt or not semana or ugt not in ALL_UGTS:
            continue
        prob = to_float(row.get("prob"))
        nivel = (row.get("nivel") or "").strip() or nivel_from_prob(prob)
        origen = (row.get("origen") or "scoring_modelo").strip()
        serie.append({
            "ugt": ugt,
            "semana": semana,
            "prob": prob,
            "prob_suavizado": None,  # se rellena por con_ewm
            "nivel": nivel,
            "origen": origen,
        })
    serie.sort(key=lambda p: (p["ugt"], p["semana"]))
    return con_ewm(serie)


def build_zonas(scoring_modelo_rows, features_historicas_rows, proximas, alertas_electorales):
    """Construye el bloque 'zonas' con el dato más reciente por UGT."""
    # Último score por UGT
    latest_score = {}
    prev_score = {}
    scores_by_ugt = {}
    for row in scoring_modelo_rows:
        ugt = (row.get("ugt") or "").strip()
        semana = (row.get("semana") or "").strip()
        if not ugt or not semana or ugt not in ALL_UGTS:
            continue
        scores_by_ugt.setdefault(ugt, []).append(row)
    for ugt, rows in scores_by_ugt.items():
        rows_sorted = sorted(rows, key=lambda r: r.get("semana", ""))
        latest_score[ugt] = rows_sorted[-1]
        if len(rows_sorted) >= 2:
            prev_score[ugt] = rows_sorted[-2]

    # Últimas features por UGT
    latest_feat = {}
    for row in features_historicas_rows:
        ugt = (row.get("ugt") or "").strip()
        semana = (row.get("semana") or "").strip()
        if not ugt or ugt not in ALL_UGTS:
            continue
        if ugt not in latest_feat or semana > latest_feat[ugt].get("semana", ""):
            latest_feat[ugt] = row

    zonas = []
    for ugt in ALL_UGTS:
        meta = UGTS_META[ugt]
        cur = latest_score.get(ugt)
        prev = prev_score.get(ugt)
        feat = latest_feat.get(ugt, {})

        prob_actual = to_float(cur.get("prob")) if cur else None
        prob_prev   = to_float(prev.get("prob")) if prev else None
        nivel = (cur.get("nivel") or "").strip() if cur else nivel_from_prob(prob_actual)

        zonas.append({
            "ugt": ugt,
            "label": meta["label"],
            "provincia": meta["provincia"],
            "color": meta["color"],
            "mapa_x": meta["mapa_x"],
            "mapa_y": meta["mapa_y"],
            "prob_actual": prob_actual,
            "nivel_riesgo": nivel,
            "semana": cur.get("semana") if cur else None,
            "tendencia_delta": (
                round(prob_actual - prob_prev, 4)
                if prob_actual is not None and prob_prev is not None else None
            ),
            "n_eventos_1w": to_int(feat.get("n_eventos_1w")),
            "n_eventos_4w": to_int(feat.get("n_eventos_4w")),
            "dias_desde_ultima_protesta": to_int(feat.get("dias_desde_ultima_protesta")),
            "racha_semanas_protesta": to_int(feat.get("racha_semanas_protesta")),
            "rep_antamina_neg_4w": to_float(feat.get("rep_antamina_neg_4w")),
            "rep_compromiso_4w": to_float(feat.get("rep_compromiso_4w")),
            "proxima_fecha_critica": proximas.get(ugt),
            "alerta_electoral": alertas_electorales.get(ugt),
        })
    return zonas


def build_proxima_fecha_critica(calendario_rows, today):
    """Para cada UGT, el evento de calendario más próximo marcado como activo."""
    per_ugt = {u: [] for u in ALL_UGTS}
    for row in calendario_rows:
        if (row.get("usar_como_proxima_fecha_critica") or "").strip().lower() != "true":
            continue
        ugts_field = (row.get("ugts") or "").strip()
        if ugts_field.upper() == "TODAS":
            ugts = list(ALL_UGTS)
        else:
            ugts = [u.strip() for u in ugts_field.split(",") if u.strip() in ALL_UGTS]

        if (row.get("tipo") or "").strip().lower() == "electoral":
            fecha_str = (row.get("fecha") or "").strip()
            if not fecha_str:
                continue
            try:
                from datetime import datetime as _dt
                fecha = _dt.strptime(fecha_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if fecha < today:
                continue
        else:
            inicio = (row.get("inicio_mes_dia") or "").strip()
            if not inicio:
                continue
            fecha = next_occurrence(inicio, today)

        entry = {
            "evento": row.get("evento") or None,
            "tipo": row.get("tipo") or "fecha_critica",
            "criticidad": row.get("criticidad") or None,
            "fecha": fecha.isoformat(),
            "evidencia_historica": row.get("evidencia_historica") or None,
            "fuente_url": row.get("fuente_url") or None,
        }
        for u in ugts:
            if u in per_ugt:
                per_ugt[u].append(entry)

    proximas = {}
    for u, entries in per_ugt.items():
        entries.sort(key=lambda e: e["fecha"])
        proximas[u] = entries[0] if entries else None
    return proximas


def build_calendario_completo(calendario_rows, today, horizon_days=365):
    """Lista completa de eventos de calendario dentro de los próximos N días."""
    by_key = {}
    for row in calendario_rows:
        if (row.get("usar_como_proxima_fecha_critica") or "").strip().lower() != "true":
            continue
        ugts_field = (row.get("ugts") or "").strip()
        if ugts_field.upper() == "TODAS":
            ugts = list(ALL_UGTS)
        else:
            ugts = [u.strip() for u in ugts_field.split(",") if u.strip() in ALL_UGTS]

        tipo = (row.get("tipo") or "fecha_critica").strip().lower()
        if tipo == "electoral":
            fecha_str = (row.get("fecha") or "").strip()
            if not fecha_str:
                continue
            try:
                from datetime import datetime as _dt
                fecha = _dt.strptime(fecha_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            if fecha < today:
                continue
        else:
            inicio = (row.get("inicio_mes_dia") or "").strip()
            if not inicio:
                continue
            fecha = next_occurrence(inicio, today)

        if (fecha - today).days > horizon_days:
            continue

        key = (fecha.isoformat(), row.get("evento"))
        if key not in by_key:
            by_key[key] = {
                "evento": row.get("evento") or None,
                "tipo": tipo,
                "criticidad": row.get("criticidad") or None,
                "fecha": fecha.isoformat(),
                "dias_restantes": (fecha - today).days,
                "evidencia_historica": row.get("evidencia_historica") or None,
                "fuente_url": row.get("fuente_url") or None,
                "ugts": [],
            }
        for u in ugts:
            if u in ALL_UGTS and u not in by_key[key]["ugts"]:
                by_key[key]["ugts"].append(u)
    return sorted(by_key.values(), key=lambda e: e["fecha"])


def build_alertas_electorales(alertas_rows, today):
    """Fase electoral vigente hoy para cada UGT (si aplica)."""
    por_ugt = {u: None for u in ALL_UGTS}
    for row in alertas_rows:
        rango = parse_periodo_electoral(row.get("periodo"))
        if not rango:
            continue
        inicio, fin = rango
        if not (inicio <= today <= fin):
            continue
        ugts = ugts_para_zonas_prioritarias(row.get("zonas_prioritarias"))
        alerta = {
            "fase": row.get("fase") or None,
            "periodo": row.get("periodo") or None,
            "riesgo": row.get("riesgo") or None,
            "accion_recomendada": row.get("accion_recomendada") or None,
        }
        for u in ugts:
            if u in por_ugt and por_ugt[u] is None:
                por_ugt[u] = alerta
    return por_ugt


def build_calibracion(calibracion_rows):
    out = []
    for row in calibracion_rows:
        nivel = (row.get("nivel") or "").strip()
        if not nivel:
            continue
        out.append({
            "nivel": nivel,
            "umbral": to_float(row.get("umbral")),
            "n": to_int(row.get("n"), 0),
            "tasa_acierto": to_float(row.get("tasa_acierto")),
            "recall": to_float(row.get("recall")),
            "descripcion": row.get("descripcion") or None,
        })
    out.sort(key=lambda r: NIVEL_ORDER.index(r["nivel"]) if r["nivel"] in NIVEL_ORDER else 99)
    return out


def build_eventos_historicos(eventos_rows):
    out = []
    for row in eventos_rows:
        ugt = (row.get("ugt") or "").strip()
        fecha = (row.get("fecha") or "").strip()
        if not ugt or not fecha or ugt not in ALL_UGTS:
            continue
        out.append({
            "fecha": fecha,
            "ugt": ugt,
            "evento": row.get("evento") or None,
            "tipo": row.get("tipo") or None,
            "severidad": row.get("severidad") or None,
            "fuente_url": row.get("fuente_url") or None,
        })
    out.sort(key=lambda e: e["fecha"], reverse=True)
    return out


def build_actores_criticos(actores_rows):
    out = []
    for row in actores_rows:
        nombre = (row.get("nombre") or "").strip()
        if not nombre:
            continue
        out.append({
            "nombre": nombre,
            "cargo_o_rol": row.get("cargo_o_rol") or None,
            "distrito_o_zona": row.get("distrito_o_zona") or None,
            "n_apariciones": to_int(row.get("n_apariciones"), 0),
            "ultima_mencion": row.get("ultima_mencion") or None,
            "contexto_resumen": row.get("contexto_resumen") or None,
        })
    out.sort(key=lambda a: (a["ultima_mencion"] or "", a["n_apariciones"]), reverse=True)
    return out


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet-id", default=SHEET_ID, help="ID del Google Sheet de Antamina")
    args = parser.parse_args()
    sheet_id = args.sheet_id

    if sheet_id == "REEMPLAZAR_CON_ID_DEL_SHEET":
        print("ERROR: Debes configurar SHEET_ID en el script o pasar --sheet-id=<ID>", file=sys.stderr)
        sys.exit(1)

    print(f"Leyendo Sheet {sheet_id}...")
    scoring_modelo      = fetch_tab(sheet_id, "scoring_modelo")
    scoring_separadas   = fetch_tab(sheet_id, "scoring_separadas")
    features_hist       = fetch_tab(sheet_id, "features_historicas")
    calibracion_raw     = fetch_tab(sheet_id, "calibracion_modelo")
    calendario_raw      = fetch_tab(sheet_id, "calendario_criticidad")
    alertas_elec_raw    = fetch_tab(sheet_id, "alertas_electorales_2026")
    eventos_raw         = fetch_tab(sheet_id, "eventos_historicos")
    actores_raw         = fetch_tab(sheet_id, "actores_criticos")

    today = date.today()
    proximas          = build_proxima_fecha_critica(calendario_raw, today)
    alertas_elec      = build_alertas_electorales(alertas_elec_raw, today)
    serie_tiempo      = build_serie_tiempo(scoring_separadas)
    zonas             = build_zonas(scoring_modelo, features_hist, proximas, alertas_elec)
    calendario_full   = build_calendario_completo(calendario_raw, today)
    calibracion       = build_calibracion(calibracion_raw)
    eventos_hist      = build_eventos_historicos(eventos_raw)
    actores           = build_actores_criticos(actores_raw)

    semanas_live = [r.get("semana") for r in scoring_modelo if r.get("semana")]

    data = {
        "meta": {
            "generado_en": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sheet_id": sheet_id,
            "modelo": "logistic_regression",
            "horizonte_dias": 30,
            "umbral_alto": UMBRAL_ALTO,
            "umbral_medio": UMBRAL_MEDIO,
            "n_niveles": 3,
            "semana_mas_reciente": max(semanas_live) if semanas_live else None,
        },
        "zonas": zonas,
        "serie_tiempo": serie_tiempo,
        "calibracion_modelo": calibracion,
        "calendario_fechas_criticas": calendario_full,
        "eventos_historicos": eventos_hist,
        "actores_criticos": actores,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK → {OUT_PATH}")
    print(f"   {len(zonas)} UGTs  ·  {len(serie_tiempo)} puntos serie  ·  {len(actores)} actores")


if __name__ == "__main__":
    main()
