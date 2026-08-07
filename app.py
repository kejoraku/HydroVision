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
    st.sidebar.info("💡 Haz clic arriba en 'Buscar Fechas en Catálogo' para desplegar los días disponibles.")
    ejecutar_analisis = False
# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO DEL MAPA (FOLIUM SEGURO)
# =========================================================================

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

        # Mover la cámara de Folium e inyectar el reborde general del Partido
        coords_centro = roi_final.geometry().centroid().coordinates().getInfo()
        mapa_folium.location = [coords_centro[1], coords_centro[0]]
        
        geometria_geojson = roi_final.geometry().getInfo()
        folium.GeoJson(
            geometria_geojson,
            name=f"Límites Políticos de {partido_usuario}",
            style_function=lambda x: {'fillColor': 'none', 'color': '#FFFFFF', 'weight': 2, 'dashArray': '5, 5'}
        ).add_to(mapa_folium)

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

        # B) PROCESAMIENTO DE LA IMAGEN DE LA FECHA ESPECÍFICA
        imagen_fecha = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                         .filterBounds(roi_final) \
                         .filterDate(fecha_inicio_evt, fecha_fin_evt) \
                         .map(calcular_ndwi).select('NDWI').max()
                         
        agua_en_fecha = imagen_fecha.gt(0)

        # C) INTERSECCIÓN DE LAS 3 CAPAS INDEPENDIENTES SOLICITADAS
        capa_anual_perm = agua_permanente_anual
        capa_fecha_perm = agua_en_fecha.And(agua_permanente_anual)
        
        frecuencia_temporal = frecuencia_anual.gte(0.20).And(frecuencia_anual.lte(0.55))
        capa_fecha_temp = agua_en_fecha.And(frecuencia_temporal)
        
        # Recortar las geometrías crudas al Partido
        recorte_anual_perm = capa_anual_perm.updateMask(capa_anual_perm).clip(roi_final)
        recorte_fecha_perm = capa_fecha_perm.updateMask(capa_fecha_perm).clip(roi_final)
        recorte_fecha_temp = capa_fecha_temp.updateMask(capa_fecha_temp).clip(roi_final)

        # FUNCIÓN INTERNA PARA CREAR CONTORNOS CON BORDES MARCADOS
        # Detecta los cambios de píxel (0 a 1) para pintar una línea sólida en el perímetro
        def crear_borde_marcado(capa_binaria):
            borde = capa_binaria.subtract(capa_binaria.focal_min(1, 'plus', 'pixels')).gt(0)
            return capa_binaria.where(borde, 2) # Píxeles internos = 1, Píxeles de borde = 2

        borde_anual_perm = crear_borde_marcado(recorte_anual_perm)
        borde_fecha_perm = crear_borde_marcado(recorte_fecha_perm)
        borde_fecha_temp = crear_borde_marcado(recorte_fecha_temp)

        # Configurar paletas visuales personalizadas con gradiente: [Fondo semitransparente, Borde sólido marcado]
        # Capa 1: Verde | Capa 2: Violeta | Capa 3: Rojo
        map_id_anual = ee.data.getMapId({'image': borde_anual_perm, 'visParams': {'min': 1, 'max': 2, 'palette': ['#2ECC71', '#006400']}})
        map_id_fecha_perm = ee.data.getMapId({'image': borde_fecha_perm, 'visParams': {'min': 1, 'max': 2, 'palette': ['#9B59B6', '#4A235A']}})
        map_id_fecha_temp = ee.data.getMapId({'image': borde_fecha_temp, 'visParams': {'min': 1, 'max': 2, 'palette': ['#E74C3C', '#7B241C']}})

        # Inyectar las capas de forma apilada e independiente con control de opacidad de fondo
        folium.TileLayer(tiles=map_id_anual['tile_fetcher'].url_format, attr='GEE', name='🟢 1. Fondo Anual Permanente (>80%)', overlay=True, opacity=0.45).add_to(mapa_folium)
        folium.TileLayer(tiles=map_id_fecha_perm['tile_fetcher'].url_format, attr='GEE', name='🟣 2. Permanente en la Fecha', overlay=True, opacity=0.55).add_to(mapa_folium)
        folium.TileLayer(tiles=map_id_fecha_temp['tile_fetcher'].url_format, attr='GEE', name='🔴 3. Temporaria en la Fecha (20%-55%)', overlay=True, opacity=0.60).add_to(mapa_folium)

        
        st.success(f"📊 ¡Mapas de superposición hídrica generados con éxito para el {fecha_seleccionada_texto}!")

# Agregar el gestor de capas interactivo arriba a la derecha del mapa
folium.LayerControl().add_to(mapa_folium)

# Renderizar el mapa de Folium de forma segura usando el componente oficial de Streamlit
st_folium(mapa_folium, width="100%", height=750, returned_objects=[])
