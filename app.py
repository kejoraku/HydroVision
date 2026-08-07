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

# Base de datos simplificada estructurada para evitar fallas de la FAO
DATA_GEOGRAFICA = {
    "Argentina": {
        "Buenos Aires": ["La Plata", "Bahia Blanca", "Mar del Plata", "Tandil", "Pilar", "Tigre", "Chascomus", "Trenque Lauquen", "Guamini", "San Pedro"],
        "Cordoba": ["Capital", "Rio Cuarto", "Villa Maria", "San Francisco", "Carlos Paz", "Punilla", "Calamuchita"],
        "Santa Fe": ["Capital", "Rosario", "Venado Tuerto", "Rafaela", "Reconquista", "San Lorenzo"]
    },
    "Spain": {
        "Madrid": ["Madrid Alcalá", "Móstoles", "Alcorcón", "Leganés"],
        "Cataluna": ["Barcelona", "Girona", "Lleida", "Tarragona"]
    }
}

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR REACTIVO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Análisis Estadístico Anual vs Evento Específico")
st.sidebar.write("---")

# 1. Selector de País
pais_sel = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)

# 2. Selector de Provincia
provincias_disponibles = list(DATA_GEOGRAFICA[pais_sel].keys())
provincia_sel = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=provincias_disponibles, index=0)

# 3. Selector de Partido / Departamento / Municipio (NUEVO)
partidos_disponibles = DATA_GEOGRAFICA[pais_sel][provincia_sel]
partido_sel = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad (Obligatorio):", options=partidos_disponibles, index=0)

# 4. Selector de Fecha Específica para el Mapa (NUEVO)
fecha_seleccionada = st.sidebar.date_input(
    "4. Selecciona la Fecha del Evento:",
    value=datetime.date(2023, 3, 15),
    min_value=datetime.date(2015, 1, 1),
    max_value=datetime.date.today()
)

ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL (ESTADÍSTICA + DINÁMICA)
# =========================================================================

# El mapa inicia centrado en coordenadas generales de la zona pampeana
M = geemap.Map(center=[-36.0, -60.0], zoom=6)
M.add_basemap("HYBRID") 

if ejecutar_analisis:
    with st.spinner("Conectando con Google Earth Engine y procesando estadísticas hídricas..."):
        
        # Extraer el año completo de la fecha seleccionada para el análisis de fondo
        anio_analisis = fecha_seleccionada.year
        fecha_inicio_anio = f"{anio_analisis}-01-01"
        fecha_fin_anio = f"{anio_analisis}-12-31"
        
        # Definir rango corto (mosaico de 15 días) en torno a la fecha exacta elegida
        fecha_inicio_evento = (fecha_seleccionada - datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        fecha_fin_evento = (fecha_seleccionada + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        
        # Cargar geometrías mundiales desde el dataset oficial LSIB de Google
        paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        roi = paises_db.filter(ee.Filter.eq('country_na', pais_sel))
        
        # Cargar colección completa de Sentinel-2
        coleccion_base = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(roi)
        
        # Función estándar para máscara de nubes y cálculo de NDWI
        def calcular_ndwi(img):
            qa = img.select('QA60')
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)
            
        # --- BLOQUE A: ANÁLISIS ESTADÍSTICO ANUAL (PROMEDIO DE FONDO) ---
        coleccion_anual = coleccion_base.filterDate(fecha_inicio_anio, fecha_fin_anio).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
        
        # --- BLOQUE B: IMAGEN DE FECHA ESPECÍFICA (EVENTO) ---
        coleccion_evento = coleccion_base.filterDate(fecha_inicio_evento, fecha_fin_evento).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 50))
        
        if coleccion_anual.size().getInfo() == 0 or coleccion_evento.size().getInfo() == 0:
            st.error("❌ No hay suficientes imágenes satelitales limpias en esa fecha o año para procesar la región.")
        else:
            # Procesar el NDWI de la serie anual y del evento corto
            ndwi_anual = coleccion_anual.map(calcular_ndwi).select('NDWI')
            ndwi_evento = coleccion_evento.map(calcular_ndwi).select('NDWI').max() # Mosaico del pico hídrico de esos 15 días
            
            # Calcular frecuencia anual (Línea base promedio)
            frecuencia_anual = ndwi_anual.map(lambda img: img.gt(0)).mean()
            
            # Clasificación de capas fijas anuales basadas en tus porcentajes (20%-55%)
            agua_permanente = frecuencia_anual.gte(0.80)
            agua_temporaria = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
            
            # Estado del agua en la FECHA ESPECÍFICA seleccionada
            agua_en_fecha = ndwi_evento.gt(0)
            
            # Comparar el evento específico contra el promedio anual permanente
            # Si hay agua en la fecha elegida, pero históricamente el píxel está seco = Inundación por crecida
            zona_inundada = agua_en_fecha.where(frecuencia_anual.gte(0.55), 0)
            
            # Recortar capas a la visualización de la zona
            capa_permanente = agua_permanente.updateMask(agua_permanente).clip(roi)
            capa_temporaria = agua_temporaria.updateMask(agua_temporaria).clip(roi)
            capa_inundacion = zona_inundada.updateMask(zona_inundada).clip(roi)
            
            # Cargar capas al mapa interactivo
            M.addLayer(capa_permanente, {'palette': ['#0000FF']}, 'Cuerpos de Agua Permanentes Anuales (>80%)')
            M.addLayer(capa_temporaria, {'palette': ['#00BFFF']}, 'Cuerpos de Agua Temporarios Anuales (20%-55%)')
            M.addLayer(capa_inundacion, {'palette': ['#FF0000']}, 'Crecidas Excesivas / Inundación en la Fecha Elegida')
            
            st.success(f"📊 Análisis completado con éxito para el año {anio_analisis}!")

# Imprimir el contenedor final
M.to_streamlit(height=750)
