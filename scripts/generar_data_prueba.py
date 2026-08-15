"""
Genera data/data.json de PRUEBA a partir de los archivos locales del modelo
(predicciones_log.csv, actores_antamina.json y los xlsx de calendario),
para poder ver el dashboard sin haber configurado el Sheet todavía.

Uso (desde la raíz del repo c:\ML alertas):
    python "C:/proyectos/dashboard/dashboard-antamina/scripts/generar_data_prueba.py"
"""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from datetime import date, datetime, timezone
import pandas as pd

# ── Rutas ──────────────────────────────────────────────────────────────────────
ML_ROOT = Path('.')  # cwd = c:\ML alertas
OUT = Path('C:/proyectos/dashboard/dashboard-antamina/data/data.json')
PRED_LOG    = ML_ROOT / 'data/processed/predicciones_log.csv'
ACTORES     = ML_ROOT / 'data/interim/actores_antamina.json'
CAL_XLSX    = ML_ROOT / 'src/data/Calendario_Conflictos_UGT_Antamina.xlsx'
FECHAS_XLSX = ML_ROOT / 'src/data/Fechas_Criticas_Antamina.xlsx'

UGT_META = {
    'Mina San Marcos':  {'id':'mina_san_marcos',  'color':'#5b9cf6', 'mx':32.0,'my':14.0,'provincia':'Huari, Áncash'},
    'Huallanca':        {'id':'huallanca',         'color':'#a78bfa', 'mx':67.0,'my':27.0,'provincia':'Bolognesi, Áncash'},
    'Huarmey':          {'id':'huarmey',           'color':'#f87171', 'mx':25.0,'my':41.0,'provincia':'Huarmey, Áncash'},
    'Valle Fortaleza':  {'id':'valle_fortaleza',   'color':'#34d399', 'mx':53.0,'my':56.0,'provincia':'Recuay/Bolognesi, Áncash'},
}
ALL_UGTS = ['mina_san_marcos','huallanca','huarmey','valle_fortaleza']
NAME_TO_ID = {v['id']: v for v in UGT_META.values()}

def nivel(prob):
    if prob is None: return None
    return 'ALTO' if prob >= 0.80 else ('MEDIO' if prob >= 0.44 else 'BAJO')

def con_ewm(serie, span=3):
    alpha = 2.0 / (span + 1)
    by_ugt = {}
    for p in serie:
        by_ugt.setdefault(p['ugt'], []).append(p)
    for pts in by_ugt.values():
        pts.sort(key=lambda p: p['semana'])
        prev = None
        for p in pts:
            if p['prob'] is None:
                p['prob_suavizado'] = prev
                continue
            prev = p['prob'] if prev is None else alpha * p['prob'] + (1 - alpha) * prev
            p['prob_suavizado'] = round(prev, 6)
    return serie

# ── 1. Predicciones (scoring log + OOF si existe) ─────────────────────────────
serie = []
zonas_latest = {}
zonas_prev = {}

if PRED_LOG.exists():
    df = pd.read_csv(PRED_LOG)
    # Normalizar nombre UGT
    ugt_map = {
        'Mina San Marcos': 'mina_san_marcos',
        'Huallanca': 'huallanca',
        'Huarmey': 'huarmey',
        'Valle Fortaleza': 'valle_fortaleza',
    }
    for _, row in df.sort_values('semana_scoring').iterrows():
        ugt_id = ugt_map.get(row['ugt'], row['ugt'].lower().replace(' ', '_'))
        if ugt_id not in ALL_UGTS:
            continue
        semana = str(row['semana_scoring'])
        prob = float(row['probabilidad'])
        pt = {
            'ugt': ugt_id,
            'semana': semana,
            'prob': prob,
            'prob_suavizado': None,
            'nivel': nivel(prob),
            'origen': 'scoring_modelo',
        }
        serie.append(pt)
        if ugt_id not in zonas_latest or semana > zonas_latest[ugt_id]['semana']:
            if ugt_id in zonas_latest:
                zonas_prev[ugt_id] = zonas_latest[ugt_id]
            zonas_latest[ugt_id] = {'semana': semana, 'prob': prob, 'nivel': nivel(prob)}
    print(f'predicciones_log: {len(serie)} filas')
else:
    print('AVISO: predicciones_log.csv no encontrado — usando datos de ejemplo')
    # Datos de ejemplo basados en el modelo actual
    ejemplos = [
        ('mina_san_marcos', '2026-08-11', 0.72),
        ('huallanca',       '2026-08-11', 0.56),
        ('huarmey',         '2026-08-11', 0.88),
        ('valle_fortaleza', '2026-08-11', 0.48),
    ]
    for ugt_id, sem, prob in ejemplos:
        pt = {'ugt': ugt_id, 'semana': sem, 'prob': prob, 'prob_suavizado': None, 'nivel': nivel(prob), 'origen': 'scoring_modelo'}
        serie.append(pt)
        zonas_latest[ugt_id] = {'semana': sem, 'prob': prob, 'nivel': nivel(prob)}

serie = con_ewm(serie)

# ── 2. Zonas ──────────────────────────────────────────────────────────────────
zonas = []
for ugt_label, meta in UGT_META.items():
    uid = meta['id']
    cur = zonas_latest.get(uid, {})
    prev = zonas_prev.get(uid, {})
    prob_actual = cur.get('prob')
    prob_prev   = prev.get('prob')
    zonas.append({
        'ugt': uid,
        'label': ugt_label,
        'provincia': meta['provincia'],
        'color': meta['color'],
        'mapa_x': meta['mx'],
        'mapa_y': meta['my'],
        'prob_actual': prob_actual,
        'nivel_riesgo': cur.get('nivel'),
        'semana': cur.get('semana'),
        'tendencia_delta': round(prob_actual - prob_prev, 4) if prob_actual is not None and prob_prev is not None else None,
        'n_eventos_1w': None, 'n_eventos_4w': None,
        'dias_desde_ultima_protesta': None, 'racha_semanas_protesta': None,
        'rep_antamina_neg_4w': None, 'rep_compromiso_4w': None,
        'proxima_fecha_critica': None, 'alerta_electoral': None,
    })

# ── 3. Calendario de criticidad (del xlsx) ────────────────────────────────────
calendario_fechas_criticas = []
UGT_ID_MAP = {
    'Huarmey': 'huarmey', 'Mina-San Marcos': 'mina_san_marcos',
    'Mina San Marcos': 'mina_san_marcos', 'Huallanca': 'huallanca',
    'Valle Fortaleza': 'valle_fortaleza', 'TODAS': None,
}
try:
    df_cal = pd.read_excel(CAL_XLSX, sheet_name='Base Consolidada', header=2)
    df_cal.columns = [
        'id','categoria','fecha_periodo','frecuencia','ugt_provincia','distritos',
        'tema','evidencia_recurrencia','situacion_2526','proyeccion','probabilidad',
        'nivel_operativo','detonantes','escenario','recomendacion','validacion','fuente1','fuente2'
    ]
    today = date.today()
    for _, r in df_cal.dropna(subset=['id']).iterrows():
        if not str(r['id']).strip().isdigit():
            continue
        ugts_raw = str(r.get('ugt_provincia', '')).split('/')[0].strip()
        ugts_raw2 = str(r.get('ugt_provincia', '')).strip()
        ugts = []
        for key, uid in UGT_ID_MAP.items():
            if key.lower() in ugts_raw2.lower():
                if uid and uid not in ugts:
                    ugts.append(uid)
        if not ugts:
            ugts = list(ALL_UGTS)
        calendario_fechas_criticas.append({
            'evento': str(r.get('tema',''))[:120],
            'tipo': str(r.get('categoria','')),
            'criticidad': str(r.get('nivel_operativo','')),
            'fecha': None,
            'dias_restantes': None,
            'fecha_periodo': str(r.get('fecha_periodo','')),
            'evidencia_historica': str(r.get('evidencia_recurrencia',''))[:300],
            'ugts': ugts,
        })
    print(f'Calendario: {len(calendario_fechas_criticas)} ventanas')
except Exception as e:
    print(f'AVISO calendario: {e}')

# ── 4. Actores críticos ────────────────────────────────────────────────────────
actores = []
if ACTORES.exists():
    actores_raw = json.loads(ACTORES.read_text(encoding='utf-8'))
    for a in actores_raw:
        actores.append({
            'nombre': a.get('nombre',''),
            'cargo_o_rol': a.get('cargo_o_rol'),
            'distrito_o_zona': a.get('distrito_o_zona'),
            'n_apariciones': a.get('n_apariciones', 0),
            'ultima_mencion': a.get('ultima_mencion'),
            'contexto_resumen': a.get('contexto_resumen'),
        })
    print(f'Actores: {len(actores)}')

# ── 5. Calibración (estática del modelo) ──────────────────────────────────────
calibracion_modelo = [
    {'nivel': 'ALTO',  'umbral': 0.80, 'n': 76,  'tasa_acierto': 0.75, 'recall': 0.69, 'descripcion': 'Umbral calibrado OOF · precisión 75%, recall 69%'},
    {'nivel': 'MEDIO', 'umbral': 0.44, 'n': 72,  'tasa_acierto': 0.58, 'recall': 0.82, 'descripcion': 'Riesgo mayor que la tasa histórica (43.9%)'},
    {'nivel': 'BAJO',  'umbral': 0.00, 'n': 47,  'tasa_acierto': 0.15, 'recall': 0.95, 'descripcion': 'Prob < 44% — baja actividad esperada'},
]

# ── 6. Alertas electorales (del xlsx) ─────────────────────────────────────────
alertas_electorales = []
try:
    df_el = pd.read_excel(CAL_XLSX, sheet_name='Ventanas Electorales', header=1)
    df_el.columns = ['periodo','fase','riesgo','detonantes','modalidad','zonas_prioritarias','indicadores','accion','evidencia','fuente']
    for _, r in df_el.dropna(subset=['fase']).iterrows():
        alertas_electorales.append({
            'periodo': str(r.get('periodo','')),
            'fase': str(r.get('fase','')),
            'riesgo': str(r.get('riesgo','')),
            'accion_recomendada': str(r.get('accion',''))[:250],
        })
    print(f'Alertas electorales: {len(alertas_electorales)}')
except Exception as e:
    print(f'AVISO alertas electorales: {e}')

# ── Guardar ───────────────────────────────────────────────────────────────────
OUT.parent.mkdir(parents=True, exist_ok=True)
data = {
    'meta': {
        'generado_en': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'fuente': 'datos locales (prueba — sin Sheet configurado)',
        'modelo': 'logistic_regression',
        'horizonte_dias': 30,
        'umbral_alto': 0.80,
        'umbral_medio': 0.44,
        'n_niveles': 3,
        'semana_mas_reciente': max((p['semana'] for p in serie), default=None),
    },
    'zonas': zonas,
    'serie_tiempo': serie,
    'calibracion_modelo': calibracion_modelo,
    'calendario_fechas_criticas': calendario_fechas_criticas,
    'eventos_historicos': [],
    'actores_criticos': actores,
    'alertas_electorales_2026': alertas_electorales,
}
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'\n✓ data.json generado: {OUT}')
print(f'  {len(zonas)} UGTs · {len(serie)} puntos · {len(actores)} actores · {len(calendario_fechas_criticas)} ventanas')
