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

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR REACTIVO)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Clasificación de Cuerpos de Agua en Fecha Específica vs Fondo Anual")
st.sidebar.write("---")

pais_sel = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_sel = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_sel].keys()), index=0)
partido_sel = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_sel][provincia_sel], index=5) # Apunta a Chascomús por defecto

anio_actual = datetime.datetime.now().year
anio_seleccionado = st.sidebar.number_input("4. Año de la Imagen del Catálogo:", min_value=2016, max_value=anio_actual, value=2023, step=1)

# --- EXTRACTOR DE GEOMETRÍA BLINDADO (EVITA ERRORES DE LA FAO) ---
fecha_inicio_filtro = f"{anio_seleccionado}-01-01"
fecha_fin_filtro = f"{anio_seleccionado}-12-31"

# 1. Resolvemos la geometría del país usando la base de datos LSIB de Google (Ultra estable)
paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
roi_pais = paises_db.filter(ee.Filter.eq('country_na', pais_sel))

# 2. Buscamos el partido de forma difusa cruzando la intersección espacial con el país para no depender de nombres de provincias
partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_sel))

# Si la base de datos de partidos falla, usamos el país completo como resguardo de seguridad
if roi_final.size().getInfo() == 0:
    roi_final = roi_pais

# 3. Consultar el catálogo de Google para ver qué fechas reales existen sin nubes (<30%)
coleccion_fechas = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
    .filterBounds(roi_final) \
    .filterDate(fecha_inicio_filtro, fecha_fin_filtro) \
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30))

# Extraer y ordenar la lista de fechas disponibles de forma segura
lista_fechas = []
if coleccion_fechas.size().getInfo() > 0:
    lista_fechas = coleccion_fechas.map(lambda img: ee.Feature(None, {'f': img.date().format('YYYY-MM-DD')})) \
                                   .aggregate_array('f').distinct().sort().getInfo()

# Mostrar el selector de fechas dinámico alimentado directamente por GEE
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

M = geemap.Map(center=[-35.5772, -58.0125], zoom=10) # Centrado inicial en la Laguna de Chascomús
M.add_basemap("HYBRID")

if ejecutar_analisis and lista_fechas:
    with st.spinner("Procesando capas de agua según criterios estadísticos anuales..."):
        
        fecha_obj = datetime.datetime.strptime(fecha_seleccionada_str, '%Y-%m-%d')
        fecha_inicio_evt = (fecha_obj - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        fecha_fin_evt = (fecha_obj + datetime.timedelta(days=1)).strftime('%Y-%m-%d')
        
        def calcular_ndwi(img):
            qa = img.select('QA60')
            mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)

        # PROCESAMIENTO ANUAL (ESTADÍSTICA DE FONDO)
        coleccion_anual = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                            .filterBounds(roi_final) \
                            .filterDate(fecha_inicio_filtro, fecha_fin_filtro) \
                            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40)) \
                            .map(calcular_ndwi).select('NDWI')
        
        frecuencia_anual = coleccion_anual.map(lambda img: img.gt(0)).mean()
        
        # CAPA 1: Cuerpos de agua permanentes a lo largo de todo el año (Frecuencia >= 80%)
        agua_permanente_anual = frecuencia_anual.gte(0.80)

        # PROCESAMIENTO DE LA FECHA ESPECÍFICA SELECCIONADA
        imagen_fecha = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                         .filterBounds(roi_final) \
                         .filterDate(fecha_inicio_evt, fecha_fin_evt) \
                         .map(calcular_ndwi).select('NDWI').max()
                         
        agua_en_fecha = imagen_fecha.gt(0)

        # CLASIFICACIÓN CRUZADA SEGÚN TUS INSTRUCCIONES HIDROLÓGICAS
        # CAPA 2: Permanente en la fecha (Tiene agua hoy Y históricamente es permanente)
        capa_permanente_fecha = agua_en_fecha.And(agua_permanente_anual)
        
        # CAPA 3: Temporario en la fecha (Tiene agua hoy PERO su promedio anual está entre 20% y 55%)
        frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        capa_temporaria_fecha = agua_en_fecha.And(frecuencia_temporal)

        # Ajustar cámara y recortar capas de forma segura
        M.center_object(roi_final, zoom=10)
        
        recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
        recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
        recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

        # Imprimir las 3 capas hídricas estrictas en el mapa
        M.addLayer(recorte_perm_anual, {'palette': ['#00008B']}, '1. Cuerpos de Agua Permanentes (Promedio Anual >80%)')
        M.addLayer(recorte_perm_fecha, {'palette': ['#0000FF']}, '2. Cuerpos de Agua Permanentes (En la Fecha Seleccionada)')
        M.addLayer(recorte_temp_fecha, {'palette': ['#00BFFF']}, '3. Cuerpos de Agua Temporarios (En la Fecha Seleccionada)')
        
        st.success(f"📊 ¡Capas hídricas calculadas con éxito para Chascomús el día {fecha_seleccionada_str}!")

# Mostrar mapa en pantalla
M.to_streamlit(height=750)
