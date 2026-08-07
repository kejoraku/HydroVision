import streamlit as st
import ee
import geemap
import datetime

# =========================================================================
# 1. CONFIGURACIÓN DE LA PÁGINA E INICIALIZACIÓN DE EARTH ENGINE
# =========================================================================
st.set_page_config(layout="wide", page_title="HydroVision NDWI", page_icon="💧")

@st.cache_resource
def iniciar_earth_engine():
    try:
        ee.Initialize(project='ee-raanidg') 
    except Exception as e:
        st.error(f"Error al inicializar Earth Engine: {e}")

iniciar_earth_engine()

# Base de datos local para la interfaz predictiva rápida
DATA_GEOGRAFICA = {
    "Argentina": {
        "Buenos Aires": ["La Plata", "Bahia Blanca", "Tandil", "Pilar", "Tigre", "Chascomus", "Trenque Lauquen", "Guamini", "San Pedro"],
        "Cordoba": ["Capital", "Rio Cuarto", "Villa Maria", "San Francisco", "Punilla", "Calamuchita"],
        "Santa Fe": ["Capital", "Rosario", "Venado Tuerto", "Rafaela", "Reconquista", "San Lorenzo"]
    }
}

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR AUTOMÁTICO OPTIMIZADO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Selección de Imagen Real del Catálogo")
st.sidebar.write("---")

pais_usuario = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_usuario = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_usuario].keys()), index=0)
partido_usuario = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_usuario][provincia_usuario], index=0)

# --- CONFIGURACIÓN DE GEOMETRÍA FIJA ---
paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
roi_pais = paises_db.filter(ee.Filter.eq('country_na', 'Argentina'))

partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_usuario))

if roi_final.size().getInfo() == 0:
    roi_final = roi_pais

# --- EXTRACTOR AUTOMÁTICO DE FECHAS EN SEGUNDO PLANO ---
# Usamos un decorador fragmentado para que cargue solo y no congele el resto de los componentes visuales
@st.fragment
def renderizar_selector_fechas(roi_geometria):
    coleccion_fechas = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(roi_geometria) \
        .filterDate('2022-01-01', '2026-12-31') \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 25))

    lista_fechas_reales = coleccion_fechas.aggregate_array('system:index') \
        .map(lambda idx: ee.String(idx).slice(0, 8)) \
        .map(lambda s: ee.Date.parse('YYYYMMdd', s).format('YYYY-MM-DD')) \
        .distinct().sort().getInfo()

    if lista_fechas_reales:
        fecha_sel_str = st.selectbox(
            "4. Selecciona la Fecha Exacta de la Imagen del Catálogo:",
            options=lista_fechas_reales,
            index=len(lista_fechas_reales) - 1
        )
        return fecha_sel_str
    else:
        st.warning("⚠️ No se encontraron imágenes en el catálogo para esta región.")
        return None

fecha_seleccionada_str = renderizar_selector_fechas(roi_final)

if fecha_seleccionada_str:
    ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")
else:
    ejecutar_analisis = False

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL (CRITERIOS HIDROLÓGICOS ESTRICTOS)
# =========================================================================

mapa_placeholder = st.empty()
M = geemap.Map(center=[-34.9214, -57.9545], zoom=10)
M.add_basemap("HYBRID")

if ejecutar_analisis and fecha_seleccionada_str:
    with st.spinner("Procesando evento hídrico e índices estadísticos anuales..."):
        
        fecha_obj = datetime.datetime.strptime(fecha_seleccionada_str, '%Y-%m-%d')
        anio_automatico = fecha_obj.year
        
        fecha_inicio_anio = f"{anio_automatico}-01-01"
        fecha_fin_anio = f"{anio_automatico}-12-31"
        
        fecha_inicio_evt = (fecha_obj - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_evt = (fecha_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
        def calcular_ndwi(img):
            qa = img.select('QA60')
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)

        # A) PROMEDIO ANUAL AUTOMÁTICO DE FONDO
        coleccion_anual = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                            .filterBounds(roi_final) \
                            .filterDate(fecha_inicio_anio, fecha_fin_anio) \
                            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)) \
                            .map(calcular_ndwi).select('NDWI')
        
        frecuencia_anual = coleccion_anual.map(lambda img: img.gt(0)).mean()
        agua_permanente_anual = frecuencia_anual.gte(0.80)

        # B) PROCESAMIENTO DE LA IMAGEN DE LA FECHA ESPECÍFICA SELECCIONADA
        imagen_fecha = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                         .filterBounds(roi_final) \
                         .filterDate(fecha_inicio_evt, fecha_fin_evt) \
                         .map(calcular_ndwi).select('NDWI').max()
                         
        agua_en_fecha = imagen_fecha.gt(0)

        # C) CLASIFICACIÓN DE LAS CAPAS SOLICITADAS EN BASE AL PROMEDIO ANUAL
        capa_permanente_fecha = agua_en_fecha.And(agua_permanente_anual)
        
        frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        capa_temporaria_fecha = agua_en_fecha.And(frecuencia_temporal)

        # Centrar el mapa y recortar capas
        M.center_object(roi_final, zoom=10)
        
        recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
        recorte_perm_fecha = recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
        recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

        # Dibujar las 3 capas solicitadas en el mapa
        M.addLayer(recorte_perm_anual, {'palette': ['#00008B']}, '1. Cuerpos de Agua Permanentes (Promedio Anual de Fondo >80%)')
        M.addLayer(recorte_perm_fecha, {'palette': ['#0000FF']}, '2. Cuerpos de Agua Permanentes (En la Fecha Seleccionada)')
        M.addLayer(recorte_temp_fecha, {'palette': ['#00BFFF']}, '3. Cuerpos de Agua Temporarios (En la Fecha Seleccionada)')
        
        st.success(f"📊 ¡Mapas hídricos generados con éxito para el {fecha_seleccionada_str}! Año analizado automáticamente: {anio_automatico}")

# Dibujar el mapa interactivo final en la pantalla derecha
with mapa_placeholder:
    M.to_streamlit(height=750)
