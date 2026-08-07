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

# Inicializar estados de memoria de Streamlit
if "fechas_disponibles" not in st.session_state:
    st.session_state.fechas_disponibles = []
if "localidad_actual" not in st.session_state:
    st.session_state.localidad_actual = ""

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR SECUENCIAL BLINDADO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Selección de Imagen Real del Catálogo")
st.sidebar.write("---")

pais_usuario = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_usuario = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_usuario].keys()), index=0)
partido_usuario = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_usuario][provincia_usuario], index=0)

id_localidad = f"{pais_usuario}_{provincia_usuario}_{partido_usuario}"
if id_localidad != st.session_state.localidad_actual:
    st.session_state.fechas_disponibles = []
    st.session_state.localidad_actual = id_localidad

st.sidebar.write("---")

btn_conectar_catalogo = st.sidebar.button("🔍 1. Buscar Fechas en Catálogo", use_container_width=True)

if btn_conectar_catalogo:
    with st.sidebar.spinner("Buscando órbitas en los servidores de Google..."):
        paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        roi_pais = paises_db.filter(ee.Filter.eq('country_na', 'Argentina'))

        partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
        roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_usuario))

        if roi_final.size().getInfo() == 0:
            roi_final = roi_pais

        coleccion_fechas = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(roi_final) \
            .filterDate('2023-01-01', '2025-12-31') \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 25))

        # CORRECCIÓN DE FORMATO: Extrae la fecha real usando la propiedad nativa del calendario del satélite
        st.session_state.fechas_disponibles = coleccion_fechas.map(lambda img: ee.Feature(None, {'f': img.date().format('YYYY-MM-DD')})) \
                                                               .aggregate_array('f').distinct().sort().getInfo()

if st.session_state.fechas_disponibles:
    fecha_seleccionada_str = st.sidebar.selectbox(
        "4. Selecciona la Fecha Exacta de la Imagen:",
        options=st.session_state.fechas_disponibles,
        index=len(st.session_state.fechas_disponibles) - 1
    )
    st.sidebar.write("---")
    ejecutar_analisis = st.sidebar.button("🚀 2. Calcular y Mostrar Mapas", type="primary", use_container_width=True)
else:
    st.sidebar.info("💡 Haz clic arriba en 'Buscar Fechas en Catálogo' para desplegar los días disponibles del satélite.")
    ejecutar_analisis = False

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO DEL MAPA
# =========================================================================

mapa_placeholder = st.empty()
M = geemap.Map(center=[-34.9214, -57.9545], zoom=10)
M.add_basemap("HYBRID")

if ejecutar_analisis and st.session_state.fechas_disponibles:
    with st.spinner("Procesando evento hídrico e índices estadísticos anuales..."):
        
        fecha_obj = datetime.datetime.strptime(fecha_seleccionada_str, '%Y-%m-%d')
        anio_automatico = fecha_obj.year
        
        fecha_inicio_anio = f"{anio_automatico}-01-01"
        fecha_fin_anio = f"{anio_automatico}-12-31"
        
        fecha_inicio_evt = (fecha_obj - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_evt = (fecha_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
        paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        roi_pais = paises_db.filter(ee.Filter.eq('country_na', 'Argentina'))
        partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
        roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_usuario))
        
        if roi_final.size().getInfo() == 0:
            roi_final = roi_pais

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

        # C) CLASIFICACIÓN DE LAS 3 CAPAS SOLICITADAS
        capa_permanente_fecha = agua_en_fecha.And(agua_permanente_anual)
        
        frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        capa_temporaria_fecha = agua_en_fecha.And(frecuencia_temporal)

        # Centrar el mapa y recortar capas
        M.center_object(roi_final, zoom=10)
        
        recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
        recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
        recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

        # Inyectar capas hidrológicas al mapa
        M.addLayer(recorte_perm_anual, {'palette': ['#00008B']}, '1. Cuerpos de Agua Permanentes (Promedio Anual de Fondo >80%)')
        M.addLayer(recorte_perm_fecha, {'palette': ['#0000FF']}, '2. Cuerpos de Agua Permanentes (En la Fecha Seleccionada)')
        M.addLayer(recorte_temp_fecha, {'palette': ['#00BFFF']}, '3. Cuerpos de Agua Temporarios (En la Fecha Seleccionada)')
        
        st.success(f"📊 ¡Mapas hídricos generados con éxito para el {fecha_seleccionada_str}! Año analizado automáticamente: {anio_automatico}")

# Dibujar el mapa interactivo final en la pantalla derecha
with mapa_placeholder:
    M.to_streamlit(height=750)
