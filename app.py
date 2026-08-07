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

# Estructura de navegación para la interfaz de usuario
DATA_GEOGRAFICA = {
    "Argentina": {
        "Buenos Aires": ["La Plata", "Bahia Blanca", "Tandil", "Pilar", "Tigre", "Chascomus", "Trenque Lauquen", "Guamini", "San Pedro"],
        "Cordoba": ["Capital", "Rio Cuarto", "Villa Maria", "San Francisco", "Punilla", "Calamuchita"],
        "Santa Fe": ["Capital", "Rosario", "Venado Tuerto", "Rafaela", "Reconquista", "San Lorenzo"]
    },
    "Spain": {
        "Madrid": ["Madrid", "Alcala de Henares", "Mostoles", "Alcorcon", "Leganes"],
        "Cataluna": ["Barcelona", "Girona", "Lleida", "Tarragona"]
    }
}

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR REACTIVO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Análisis Estadístico Anual vs Evento Específico")
st.sidebar.write("---")

pais_sel = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)

provincias_disponibles = list(DATA_GEOGRAFICA[pais_sel].keys())
provincia_sel = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=provincias_disponibles, index=0)

partidos_disponibles = DATA_GEOGRAFICA[pais_sel][provincia_sel]
partido_sel = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad (Obligatorio):", options=partidos_disponibles, index=0)

fecha_seleccionada = st.sidebar.date_input(
    "4. Selecciona la Fecha del Evento:",
    value=datetime.date(2023, 3, 15),
    min_value=datetime.date(2015, 1, 1),
    max_value=datetime.date.today()
)

ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO DEL MAPA
# =========================================================================

# Inicializar objeto del mapa base híbrido
M = geemap.Map(center=[-34.9214, -57.9545], zoom=11) # Coordenadas iniciales por defecto (La Plata)
M.add_basemap("HYBRID") 

if ejecutar_analisis:
    with st.spinner("Conectando con Google Earth Engine y procesando estadísticas hídricas..."):
        
        anio_analisis = fecha_seleccionada.year
        fecha_inicio_anio = f"{anio_analisis}-01-01"
        fecha_fin_anio = f"{anio_analisis}-12-31"
        
        fecha_inicio_evento = (fecha_seleccionada - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        fecha_fin_evento = (fecha_seleccionada + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        
        partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
        
        roi = partidos_db.filter(ee.Filter.eq('adm0_name', pais_sel)) \
                          .filter(ee.Filter.eq('adm1_name', provincia_sel)) \
                          .filter(ee.Filter.eq('adm2_name', partido_sel))
                          
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
            
        coleccion_anual = coleccion_base.filterDate(fecha_inicio_anio, fecha_fin_anio).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
        coleccion_evento = coleccion_base.filterDate(fecha_inicio_evento, fecha_fin_evento).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 50))
        
        if coleccion_anual.size().getInfo() == 0 or coleccion_evento.size().getInfo() == 0:
            st.error("❌ No hay suficientes imágenes satelitales limpias en esa fecha o año para procesar la región.")
        else:
            ndwi_anual = coleccion_anual.map(calcular_ndwi).select('NDWI')
            ndwi_evento = coleccion_evento.map(calcular_ndwi).select('NDWI').max()
            
            frecuencia_anual = ndwi_anual.map(lambda img: img.gt(0)).mean()
            
            agua_permanente = frecuencia_anual.gte(0.80)
            agua_temporaria = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
            agua_en_fecha = ndwi_evento.gt(0)
            
            zona_inundada = agua_en_fecha.where(frecuencia_anual.gte(0.55), 0)
            
            # Ajustar dinámicamente el centro del mapa antes de imprimirlo en pantalla
            M.center_object(roi, zoom=11)
            
            capa_permanente = agua_permanente.updateMask(agua_permanente).clip(roi)
            capa_temporaria = agua_temporaria.updateMask(agua_temporaria).clip(roi)
            capa_inundacion = zona_inundada.updateMask(zona_inundada).clip(roi)
            
            M.addLayer(capa_permanente, {'palette': ['#0000FF']}, 'Cuerpos de Agua Permanentes Anuales (>80%)')
            M.addLayer(capa_temporaria, {'palette': ['#00BFFF']}, 'Cuerpos de Agua Temporarios Anuales (20%-55%)')
            M.addLayer(capa_inundacion, {'palette': ['#FF0000']}, 'Crecidas Excesivas / Inundación en la Fecha Elegida')
            
            st.success(f"📊 ¡Análisis estadístico completado con éxito para {partido_sel} en el año {anio_analisis}!")

# CORRECCIÓN CLAVE: El mapa se imprime AL FINAL de todo el script, reflejando siempre el estado actualizado con las capas añadidas
M.to_streamlit(height=750)
