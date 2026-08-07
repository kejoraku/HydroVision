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
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR PREDICTIVO LIBERADO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Clasificación de Cuerpos de Agua en Fecha Específica vs Fondo Anual")
st.sidebar.write("---")

# Inputs geográficos iniciales
pais_usuario = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_usuario = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_usuario].keys()), index=0)
partido_usuario = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_usuario][provincia_usuario], index=0)

# --- EL INPUT DE SELECCIÓN DE FECHA (APARECE AL INSTANTE) ---
# Calendario nativo de Streamlit que aparece en un milisegundo sin depender del servidor
fecha_seleccionada = st.sidebar.date_input(
    "4. Selecciona la Fecha de la Imagen a Visualizar:",
    value=datetime.date(2023, 3, 15),
    min_value=datetime.date(2016, 1, 1),
    max_value=datetime.date.today()
)

# Botón de ejecución siempre visible y disponible para el usuario
ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL (TUS REGLAS ESTRICTAS)
# =========================================================================

# Inicializar mapa base centrado genéricamente
M = geemap.Map(center=[-34.9214, -57.9545], zoom=10)
M.add_basemap("HYBRID")

if ejecutar_analisis:
    with st.spinner("Procesando evento hídrico e índices estadísticos anuales..."):
        
        # EL PASO AUTOMÁTICO: Extraemos el año directamente del objeto de fecha seleccionado
        anio_automatico = fecha_seleccionada.year
        fecha_inicio_anio = f"{anio_automatico}-01-01"
        fecha_fin_anio = f"{anio_automatico}-12-31"
        
        # Mosaico corto de paso orbital en torno al día elegido
        fecha_inicio_evt = (fecha_seleccionada - datetime.timedelta(days=3)).strftime('%Y-%m-%d')
        fecha_fin_evt = (fecha_seleccionada + datetime.timedelta(days=3)).strftime('%Y-%m-%d')
        
        # Resolver límites espaciales del partido de forma segura
        paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        roi_pais = paises_db.filter(ee.Filter.eq('country_na', pais_usuario))
        
        partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
        roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_usuario))
        
        if roi_final.size().getInfo() == 0:
            roi_final = roi_pais

        # Función estándar para calcular el NDWI
        def calcular_ndwi(img):
            qa = img.select('QA60')
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)

        # A) PROMEDIO ANUAL AUTOMÁTICO DE FONDO (PROCESADO EN SEGUNDO PLANO)
        coleccion_anual = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                            .filterBounds(roi_final) \
                            .filterDate(fecha_inicio_anio, fecha_fin_anio) \
                            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)) \
                            .map(calcular_ndwi).select('NDWI')
        
        frecuencia_anual = coleccion_anual.map(lambda img: img.gt(0)).mean()
        
        # CAPA 1: Promedio de cuerpos de agua permanentes a lo largo de todo ese año (Frecuencia >= 80%)
        agua_permanente_anual = frecuencia_anual.gte(0.80)

        # B) IMAGEN DE LA FECHA ESPECÍFICA QUE ELIGIÓ EL USUARIO
        coleccion_evento = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                             .filterBounds(roi_final) \
                             .filterDate(fecha_inicio_evt, fecha_fin_evt) \
                             .map(calcular_ndwi).select('NDWI')
                             
        if coleccion_evento.size().getInfo() == 0:
            st.error(f"❌ El satélite no registra pasadas libres de nubes sobre {partido_usuario} en la fecha {fecha_seleccionada}. Intenta seleccionando otra fecha cercana.")
        else:
            imagen_fecha = coleccion_evento.max()
            agua_en_fecha = imagen_fecha.gt(0)

            # C) CLASIFICACIÓN DE LAS CAPAS EN LA FECHA EN BASE AL PROMEDIO ANUAL
            # CAPA 2: Cuerpos de Agua Permanentes EN ESA FECHA (Tiene agua hoy Y históricamente es permanente)
            capa_permanente_fecha = agua_en_fecha.And(agua_permanente_anual)
            
            # CAPA 3: Cuerpos de Agua Temporarios EN ESA FECHA (Tiene agua hoy PERO su promedio anual está entre 20% y 55%)
            frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
            capa_temporaria_fecha = agua_en_fecha.And(frecuencia_temporal)

            # Ajustar cámara del mapa al partido procesado y recortar capas
            M.center_object(roi_final, zoom=10)
            
            recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
            recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
            recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

            # Dibujar las 3 capas hidrológicas exactas que solicitaste
            M.addLayer(recorte_perm_anual, {'palette': ['#00008B']}, '1. Cuerpos de Agua Permanentes (Promedio Anual de Fondo >80%)')
            M.addLayer(recorte_perm_fecha, {'palette': ['#0000FF']}, '2. Cuerpos de Agua Permanentes (En la Fecha Seleccionada)')
            M.addLayer(recorte_temp_fecha, {'palette': ['#00BFFF']}, '3. Cuerpos de Agua Temporarios (En la Fecha Seleccionada)')
            
            st.success(f"📊 ¡Mapas hídricos generados con éxito para el {fecha_seleccionada}! Año analizado automáticamente: {anio_automatico}")

# Imprimir el mapa dinámico al final
M.to_streamlit(height=750)
