import streamlit as st
import ee
import folium
from streamlit_folium import st_folium
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

if "diccionario_fechas" not in st.session_state:
    st.session_state.diccionario_fechas = {}
if "localidad_actual" not in st.session_state:
    st.session_state.localidad_actual = ""

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR SECUENCIAL)
# =========================================================================
st.sidebar.title("💧 HydroVision Pro")
st.sidebar.markdown("### Selección de Imagen Real del Catálogo")
st.sidebar.write("---")

pais_usuario = st.sidebar.selectbox("1. Selecciona el País:", options=list(DATA_GEOGRAFICA.keys()), index=0)
provincia_usuario = st.sidebar.selectbox("2. Selecciona la Provincia/Estado:", options=list(DATA_GEOGRAFICA[pais_usuario].keys()), index=0)
partido_usuario = st.sidebar.selectbox("3. Selecciona el Partido/Ciudad:", options=DATA_GEOGRAFICA[pais_usuario][provincia_usuario], index=3) # Trenque Lauquen por defecto

id_localidad = f"{pais_usuario}_{provincia_usuario}_{partido_usuario}"
if id_localidad != st.session_state.localidad_actual:
    st.session_state.diccionario_fechas = {}
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

        lista_propiedades = coleccion_fechas.map(lambda img: ee.Feature(None, {
            'texto': img.date().format('YYYY-MM-DD'),
            'milisegundos': img.get('system:time_start')
        })).reduceColumns(ee.Reducer.toList(2), ['texto', 'milisegundos']).get('list').getInfo()

        if lista_propiedades:
            dicc_temporal = {}
            for item in lista_propiedades:
                if item and len(item) == 2:
                    fecha_sucia = str(item[0])
                    if len(fecha_sucia) > 10:
                        fecha_sucia = fecha_sucia[:10]
                    dicc_temporal[fecha_sucia] = item[1]
            st.session_state.diccionario_fechas = dict(sorted(dicc_temporal.items()))

if st.session_state.diccionario_fechas:
    fecha_seleccionada_texto = st.sidebar.selectbox(
        "4. Selecciona la Fecha Exacta de la Imagen:",
        options=list(st.session_state.diccionario_fechas.keys()),
        index=len(st.session_state.diccionario_fechas) - 1
    )
    milisegundos_seleccionados = st.session_state.diccionario_fechas[fecha_seleccionada_texto]
    st.sidebar.write("---")
    ejecutar_analisis = st.sidebar.button("🚀 2. Calcular y Mostrar Mapas", type="primary", use_container_width=True)
else:
    st.sidebar.info("💡 Haz clic arriba en 'Buscar Fechas del Catálogo' para desplegar los días disponibles.")
    ejecutar_analisis = False
# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO DEL MAPA (FOLIUM SEGURO)
# =========================================================================

# Crear objeto de mapa base nativo de Folium con capa satelital híbrida de Google
mapa_folium = folium.Map(location=[-35.9722, -62.7145], zoom_start=10, control_scale=True)
folium.TileLayer(
    tiles='https://google.com{x}&y={y}&z={z}',
    attr='Google Hybrid',
    name='Google Satélite Híbrido',
    overlay=False,
    control=True
).add_to(mapa_folium)

if ejecutar_analisis and st.session_state.diccionario_fechas:
    with st.spinner("Procesando evento hídrico e índices estadísticos anuales..."):
        
        # Reconstruir las variables de tiempo nativas en Earth Engine
        ee_fecha_base = ee.Date(milisegundos_seleccionados)
        anio_automatico = ee_fecha_base.get('year').getInfo()
        
        fecha_inicio_anio = f"{anio_automatico}-01-01"
        fecha_fin_anio = f"{anio_automatico}-12-31"
        
        fecha_inicio_evt = ee_fecha_base.advance(-1, 'day')
        fecha_fin_evt = ee_fecha_base.advance(1, 'day')
        
        paises_db = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
        roi_pais = paises_db.filter(ee.Filter.eq('country_na', 'Argentina'))
        partidos_db = ee.FeatureCollection("FAO/GAUL/2015/level2")
        roi_final = partidos_db.filterBounds(roi_pais).filter(ee.Filter.stringContains('adm2_name', partido_usuario))
        
        if roi_final.size().getInfo() == 0:
            roi_final = roi_pais

        # Mover dinámicamente el centro del mapa de Folium a las coordenadas reales de la ROI
        coords_centro = roi_final.geometry().centroid().coordinates().getInfo()
        # Línea 121 CORREGIDA:
        mapa_folium.location = [coords_centro[1], coords_centro[0]]

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
        
        # Enmascarar y recortar capas
        recorte_perm_anual = agua_permanente_anual.updateMask(agua_permanente_anual).clip(roi_final)
        recorte_perm_fecha = capa_permanente_fecha.updateMask(capa_permanente_fecha).clip(roi_final)
        recorte_temp_fecha = capa_temporaria_fecha.updateMask(capa_temporaria_fecha).clip(roi_final)

        # Extraer los enlaces de mosaico (Tile URLs) oficiales desde los servidores de Google
        map_id_anual = ee.data.getMapId({'image': recorte_perm_anual, 'visParams': {'palette': ['#00008B']}})
        map_id_perm_fecha = ee.data.getMapId({'image': recorte_perm_fecha, 'visParams': {'palette': ['#0000FF']}})
        map_id_temp_fecha = ee.data.getMapId({'image': recorte_temp_fecha, 'visParams': {'palette': ['#00BFFF']}})

        # Inyectar las capas de Earth Engine directamente sobre el mapa base de Folium
        folium.TileLayer(tiles=map_id_anual['tile_fetcher'].url_format, attr='GEE', name='1. Cuerpos de Agua Permanentes Anuales (>80%)', overlay=True).add_to(mapa_folium)
        folium.TileLayer(tiles=map_id_perm_fecha['tile_fetcher'].url_format, attr='GEE', name='2. Cuerpos de Agua Permanentes (En la Fecha)', overlay=True).add_to(mapa_folium)
        folium.TileLayer(tiles=map_id_temp_fecha['tile_fetcher'].url_format, attr='GEE', name='3. Cuerpos de Agua Temporarios (En la Fecha)', overlay=True).add_to(mapa_folium)
        
        st.success(f"📊 ¡Mapas hídricos generados con éxito para la escena del {fecha_seleccionada_texto}! Año analizado automáticamente: {anio_automatico}")

# Agregar el gestor de capas interactivo arriba a la derecha del mapa
folium.LayerControl().add_to(mapa_folium)

# Renderizar el mapa de Folium de forma segura usando el componente oficial de Streamlit
st_folium(mapa_folium, width="100%", height=750, returned_objects=[])
