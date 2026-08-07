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

# Diccionario geográfico en español para la interfaz de usuario
DATA_GEOGRAFICA = {
    "Argentina": {
        "Buenos Aires": ["La Plata", "Bahia Blanca", "Tandil", "Pilar", "Tigre", "Chascomus", "Trenque Lauquen", "Guamini", "San Pedro"],
        "Cordoba": ["Capital", "Rio Cuarto", "Villa Maria", "San Francisco", "Punilla", "Calamuchita"],
        "Santa Fe": ["Capital", "Rosario", "Venado Tuerto", "Rafaela", "Reconquista", "San Lorenzo"]
    }
}

# Traducción interna para que Google Earth Engine encuentre las provincias (GAUL Level 1)
TRADUCCION_PROVINCIAS = {
    "Buenos Aires": "Buenos Aires",
    "Cordoba": "Cordoba",
    "Santa Fe": "Santa Fe"
}

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR REACTIVO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Clasificación de Cuerpos de Agua en Fecha Específica vs Fondo Anual")
st.sidebar.write("---")

pais_sel = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_sel = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_sel].keys()), index=0)
partido_sel = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_sel][provincia_sel], index=0)

anio_actual = datetime.datetime.now().year
anio_seleccionado = st.sidebar.number_input("4. Año de la Imagen del Catálogo:", min_value=2016, max_value=anio_actual, value=2023, step=1)

# --- EXTRACTOR AUTOMÁTICO DE FECHAS DISPONIBLES EN EL CATÁLOGO ---
prov_en = TRADUCCION_PROVINCIAS[provincia_sel]
fecha_inicio_filtro = f"{anio_seleccionado}-01-01"
fecha_fin_filtro = f"{anio_seleccionado}-12-31"

# Definir la ROI a nivel provincial para garantizar la captura de órbitas del satélite
roi_fechas = ee.FeatureCollection("FAO/GAUL/2015/level1") \
               .filter(ee.Filter.eq('adm0_name', pais_sel)) \
               .filter(ee.Filter.eq('adm1_name', prov_en))

# Consultar el catálogo de Google para ver qué fechas reales existen sin nubes (<30%)
coleccion_fechas = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(roi_fechas) \
    .filterDate(fecha_inicio_filtro, fecha_fin_filtro) \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30))

# Extraer y ordenar la lista de fechas disponibles
lista_fechas = coleccion_fechas.map(lambda img: ee.Feature(None, {'f': img.date().format('YYYY-MM-DD')})) \
                               .aggregate_array('f').distinct().sort().getInfo()

# Forzar el input de selección basado en las imágenes disponibles del catálogo
if lista_fechas:
    fecha_seleccionada_str = st.sidebar.selectbox(
        "5. Selecciona la Fecha Exacta de la Imagen:",
        options=lista_fechas,
        index=0
    )
    ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")
else:
    st.sidebar.warning("⚠️ No se encontraron imágenes limpias en el catálogo para este año.")
    ejecutar_analisis = False

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL (CRITERIOS HIDROLÓGICOS SOLICITADOS)
# =========================================================================

M = geemap.Map(center=[-34.9214, -57.9545], zoom=10)
M.add_basemap("HYBRID")

if ejecutar_analisis and lista_fechas:
    with st.spinner("Procesando capas de agua según criterios estadísticos anuales..."):
        
        # 1. Resolver límites geográficos precisos del Partido (GAUL Level 2)
        partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
        roi_partido = partidos_db.filter(ee.Filter.eq('adm0_name', pais_sel)) \
                                 .filter(ee.Filter.eq('adm1_name', prov_en)) \
                                 .filter(ee.Filter.stringContains('adm2_name', partido_sel))
        
        # Respaldo por si el nombre del partido difiere en la codificación de la FAO
        if roi_partido.size().getInfo() == 0:
            roi_final = roi_fechas
        else:
            roi_final = roi_partido

                # 2. Función estándar para calcular el NDWI en las imágenes
        def calcular_ndwi(img):
            qa = img.select('QA60')
            # CORREGIDO: .And() con mayúscula inicial para la API de Python
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)


        # 3. PROCESAMIENTO ANUAL (ESTADÍSTICA DE FONDO)
        coleccion_anual = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                            .filterBounds(roi_final) \
                            .filterDate(fecha_inicio_filtro, fecha_fin_filtro) \
                            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)) \
                            .map(calcular_ndwi).select('NDWI')
        
        # Calcular el promedio de frecuencia anual de agua (NDWI > 0)
        frecuencia_anual = coleccion_anual.map(lambda img: img.gt(0)).mean()
        
        # CAPA 1: Cuerpos de agua permanentes a lo largo de todo el año (Frecuencia >= 80%)
        agua_permanente_anual = frecuencia_anual.gte(0.80)

        # 4. PROCESAMIENTO DE LA FECHA ESPECÍFICA SELECCIONADA
        fecha_obj = datetime.datetime.strptime(fecha_seleccionada_str, '%Y-%m-%d')
        fecha_inicio_evt = (fecha_obj - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_evt = (fecha_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Extraer la imagen exacta de la órbita elegida
        imagen_fecha = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                         .filterBounds(roi_final) \
                         .filterDate(fecha_inicio_evt, fecha_fin_evt) \
                         .map(calcular_ndwi).select('NDWI').max()
                         
        # Identificar la masa hídrica total presente en el día seleccionado
        agua_en_fecha = imagen_fecha.gt(0)

        # 5. CLASIFICACIÓN CRUZADA SEGÚN TUS INSTRUCCIONES HIDROLÓGICAS
        # CAPA 2: Permanente en la fecha (Tiene agua hoy Y históricamente es permanente)
        capa_permanente_fecha = agua_en_fecha.And(agua_permanente_anual)
        
        # CAPA 3: Temporario en la fecha (Tiene agua hoy PERO su promedio anual está entre 20% y 55%)
        frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        capa_temporaria_fecha = agua_en_fecha.And(frecuencia_temporal)

        # 6. ENMASCARAR, RECORTAR Y DIBUJAR EN EL MAPA
        M.center_object(roi_final, zoom=10)
        
        recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
        recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
        recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

        # Imprimir las capas hídricas estrictas solicitadas
        M.addLayer(recorte_perm_anual, {'palette': ['#00008B']}, '1. Cuerpos de Agua Permanentes (Promedio Anual >80%)')
        M.addLayer(recorte_perm_fecha, {'palette': ['#0000FF']}, '2. Cuerpos de Agua Permanentes (En la Fecha Seleccionada)')
        M.addLayer(recorte_temp_fecha, {'palette': ['#00BFFF']}, '3. Cuerpos de Agua Temporarios (En la Fecha Seleccionada)')
        
        st.success(f"📊 ¡Capas hídricas calculadas con éxito para la escena del {fecha_seleccionada_str}!")

# Mostrar mapa en pantalla
M.to_streamlit(height=750)
