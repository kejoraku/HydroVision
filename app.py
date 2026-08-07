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
st.sidebar.markdown("### Selección Basada en Catálogo de Imágenes Reales")
st.sidebar.write("---")

pais_sel = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)

provincias_disponibles = list(DATA_GEOGRAFICA[pais_sel].keys())
provincia_sel = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=provincias_disponibles, index=0)

partidos_disponibles = DATA_GEOGRAFICA[pais_sel][provincia_sel]
partido_sel = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=partidos_disponibles, index=0)

anio_actual = datetime.datetime.now().year
anio_seleccionado = st.sidebar.number_input("4. Año para Estadística de Fondo:", min_value=2015, max_value=anio_actual, value=2023, step=1)

# --- BLOQUE EXTRACTOR DE FECHAS REALES DESDE EL CATÁLOGO ---
fechas_disponibles = []

fecha_inicio_anio = f"{anio_seleccionado}-01-01"
fecha_fin_anio = f"{anio_seleccionado}-12-31"

partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")

# CORRECCIÓN CLAVE: Filtro inteligente por aproximación de texto (stringContains) para evitar fallas por nombres compuestos de la FAO
roi_fechas = partidos_db.filter(ee.Filter.eq('adm0_name', pais_sel)) \
                        .filter(ee.Filter.eq('adm1_name', provincia_sel)) \
                        .filter(ee.Filter.stringContains('adm2_name', partido_sel))

# Si el filtro por aproximación no arroja resultados, usamos el límite provincial para no trabar la interfaz
if roi_fechas.size().getInfo() == 0:
    roi_fechas = ee.FeatureCollection("FAO/GAUL/2015/level1") \
                    .filter(ee.Filter.eq('adm0_name', pais_sel)) \
                    .filter(ee.Filter.eq('adm1_name', provincia_sel))

# Buscar escenas de Sentinel-2 que toquen la geometría resuelta
coleccion_fechas = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(roi_fechas) \
    .filterDate(fecha_inicio_anio, fecha_fin_anio) \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 50)) 
    
def extraer_fecha(img):
    return ee.Feature(None, {'fecha': img.date().format('YYYY-MM-DD')})
    
lista_fechas_ee = coleccion_fechas.map(extraer_fecha).aggregate_array('fecha').distinct().sort()
fechas_disponibles = lista_fechas_ee.getInfo()

# Mostrar el selector de fechas dinámico alimentado directamente por GEE
if fechas_disponibles:
    fecha_seleccionada_str = st.sidebar.selectbox(
        "5. Selecciona una Fecha Real del Catálogo (100% Disponible):",
        options=fechas_disponibles,
        index=0
    )
    ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")
else:
    st.sidebar.warning("⚠️ No se detectaron órbitas de Sentinel-2 despejadas para este año en este partido.")
    ejecutar_analisis = False

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO
# =========================================================================

M = geemap.Map(center=[-34.9214, -57.9545], zoom=11)
M.add_basemap("HYBRID")

if ejecutar_analisis and fechas_disponibles:
    with st.spinner("Procesando evento hídrico garantizado desde el servidor de Google..."):
        
        fecha_obj = datetime.datetime.strptime(fecha_seleccionada_str, '%Y-%m-%d')
        fecha_inicio_evento = (fecha_obj - datetime.timedelta(days=2)).strftime('%Y-%m-%d')
        fecha_fin_evento = (fecha_obj + datetime.timedelta(days=2)).strftime('%Y-%m-%d')
        
        # Aplicamos la misma lógica de tolerancia de texto para la ROI de procesamiento
        roi = partidos_db.filter(ee.Filter.eq('adm0_name', pais_sel)) \
                         .filter(ee.Filter.eq('adm1_name', provincia_sel)) \
                         .filter(ee.Filter.stringContains('adm2_name', partido_sel))
        
        if roi.size().getInfo() == 0:
            roi = ee.FeatureCollection("FAO/GAUL/2015/level1") \
                    .filter(ee.Filter.eq('adm0_name', pais_sel)) \
                    .filter(ee.Filter.eq('adm1_name', provincia_sel))
        
        coleccion_base = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(roi)
        
        def calcular_ndwi(img):
            qa = img.select('QA60')
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)
            
        coleccion_anual = coleccion_base.filterDate(fecha_inicio_anio, fecha_fin_anio).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 50))
        ndwi_anual = coleccion_anual.map(calcular_ndwi).select('NDWI')
        frecuencia_anual = ndwi_anual.map(lambda img: img.gt(0)).mean()
        
        imagen_evento = coleccion_base.filterDate(fecha_inicio_evento, fecha_fin_evento).map(calcular_ndwi).select('NDWI').max()
        
        agua_permanente = frecuencia_anual.gte(0.80)
        agua_temporaria = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        agua_en_fecha = imagen_evento.gt(0)
        
        zona_inundada = agua_en_fecha.where(frecuencia_anual.gte(0.55), 0)
        
        M.center_object(roi, zoom=11)
        capa_permanente = agua_permanente.updateMask(agua_permanente).clip(roi)
        capa_temporaria = agua_temporaria.updateMask(agua_temporaria).clip(roi)
        capa_inundacion = zona_inundada.updateMask(zona_inundada).clip(roi)
        
        M.addLayer(capa_permanente, {'palette': ['#0000FF']}, 'Cuerpos de Agua Permanentes Anuales (>80%)')
        M.addLayer(capa_temporaria, {'palette': ['#00BFFF']}, 'Cuerpos de Agua Temporarios Anuales (20%-55%)')
        M.addLayer(capa_inundacion, {'palette': ['#FF0000']}, 'Crecidas Excesivas / Inundación en la Fecha Elegida')
        
        st.success(f"📊 ¡Análisis hidrológico completado para {partido_sel} usando la escena real del {fecha_seleccionada_str}!")

# Dibujar el mapa actualizado al final
M.to_streamlit(height=750)
