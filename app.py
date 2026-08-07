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
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR REACTIVO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Selección de Imagen Real del Catálogo")
st.sidebar.write("---")

# Inputs geográficos iniciales
pais_usuario = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_usuario = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_usuario].keys()), index=0)
partido_usuario = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_usuario][provincia_usuario], index=0)

# --- EXTRACTOR DE GEOMETRÍA SEGURO ---
paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
roi_pais = paises_db.filter(ee.Filter.eq('country_na', pais_usuario))

partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_usuario))

if roi_final.size().getInfo() == 0:
    roi_final = roi_pais

# --- BUSCADOR AUTOMÁTICO DE FECHAS EN EL CATÁLOGO (SIN INPUT DE AÑO) ---
# Buscamos un rango amplio general para traer las pasadas reales del satélite Sentinel-2
coleccion_busqueda = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(roi_final) \
    .filterDate('2021-01-01', '2026-12-31') \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) # Imágenes bien limpias

# Extraemos la lista de fechas reales que sí existen en los servidores de Google
lista_fechas_reales = coleccion_busqueda.map(lambda img: ee.Feature(None, {'f': img.date().format('YYYY-MM-DD')})) \
                                       .aggregate_array('f').distinct().sort().getInfo()

# EL INPUT MÁS IMPORTANTE: Desplegable dinámico alimentado por el catálogo real
if lista_fechas_reales:
    fecha_seleccionada_str = st.sidebar.selectbox(
        "4. Selecciona la Fecha Exacta de la Imagen para Visualizar:",
        options=lista_fechas_reales,
        index=len(lista_fechas_reales) - 1 # Apunta a la más reciente disponible por defecto
    )
    # El botón de aceptar ahora aparece de forma segura porque la lista ya se resolvió
    ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")
else:
    st.sidebar.warning("⚠️ Cargando catálogo de órbitas de Google...")
    ejecutar_analisis = False

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL (TUS REGLAS ESTRICTAS)
# =========================================================================

M = geemap.Map(center=[-34.9214, -57.9545], zoom=10)
M.add_basemap("HYBRID")

if ejecutar_analisis and lista_fechas_reales:
    with st.spinner("Procesando evento hídrico e índices estadísticos anuales..."):
        
        # EL PASO AUTOMÁTICO QUE ME DIJISTE: Leemos el año directamente de la fecha elegida
        fecha_obj = datetime.datetime.strptime(fecha_seleccionada_str, '%Y-%m-%d')
        anio_automatico = fecha_obj.year
        
        # Definimos las ventanas temporales basadas en ese año leído automáticamente
        fecha_inicio_anio = f"{anio_automatico}-01-01"
        fecha_fin_anio = f"{anio_automatico}-12-31"
        
        fecha_inicio_evt = (fecha_obj - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_evt = (fecha_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Función para calcular el NDWI
        def calcular_ndwi(img):
            qa = img.select('QA60')
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)

        # A) PROMEDIO ANUAL AUTOMÁTICO (DE FONDO)
        coleccion_anual = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                            .filterBounds(roi_final) \
                            .filterDate(fecha_inicio_anio, fecha_fin_anio) \
                            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)) \
                            .map(calcular_ndwi).select('NDWI')
        
        frecuencia_anual = coleccion_anual.map(lambda img: img.gt(0)).mean()
        
        # CAPA 1: Promedio de cuerpos de agua permanentes a lo largo de todo ese año (Frecuencia >= 80%)
        agua_permanente_anual = frecuencia_anual.gte(0.80)

        # B) PROCESAMIENTO DE LA FECHA ESPECÍFICA QUE ELIGIÓ EL USUARIO
        imagen_fecha = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                         .filterBounds(roi_final) \
                         .filterDate(fecha_inicio_evt, fecha_fin_evt) \
                         .map(calcular_ndwi).select('NDWI').max()
                         
        agua_en_fecha = imagen_fecha.gt(0)

        # C) CLASIFICACIÓN DE LAS DOS CAPAS DE LA FECHA SELECCIONADA EN BASE AL PROMEDIO
        # CAPA 2: Cuerpos de Agua Permanentes EN ESA FECHA (Tiene agua hoy Y históricamente es permanente)
        capa_permanente_fecha = agua_en_fecha.And(agua_permanente_anual)
        
        # CAPA 3: Cuerpos de Agua Temporarios EN ESA FECHA (Tiene agua hoy PERO su promedio anual está entre 20% y 55%)
        frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        capa_temporaria_fecha = agua_en_fecha.And(frecuencia_temporal)

        # Centrar y recortar
        M.center_object(roi_final, zoom=10)
        
        recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
        recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
        recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

        # Dibujar las 3 capas definitivas en el mapa
        M.addLayer(recorte_perm_anual, {'palette': ['#00008B']}, '1. Cuerpos de Agua Permanentes (Promedio Anual de Fondo >80%)')
        M.addLayer(recorte_perm_fecha, {'palette': ['#0000FF']}, '2. Cuerpos de Agua Permanentes (En la Fecha Seleccionada)')
        M.addLayer(recorte_temp_fecha, {'palette': ['#00BFFF']}, '3. Cuerpos de Agua Temporarios (En la Fecha Seleccionada)')
        
        st.success(f"📊 ¡Mapas hídricos generados con éxito para la escena real del {fecha_seleccionada_str}! Año analizado automáticamente: {anio_automatico}")

# Imprimir el mapa actualizado en pantalla
M.to_streamlit(height=750)
