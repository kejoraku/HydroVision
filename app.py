import streamlit as st
import ee
import folium
from streamlit_folium import folium_static
import datetime
import traceback

# =========================================================================
# 1. CONFIGURACIÓN DE LA PÁGINA E INICIALIZACIÓN DE EARTH ENGINE
# =========================================================================
st.set_page_config(layout="wide", page_title="HydroVision Argentina NDWI", page_icon="💧")

@st.cache_resource
def iniciar_earth_engine():
    try:
        ee.Initialize(project='ee-raanidg') 
    except Exception as e:
        try:
            ee.Initialize(project='ee-raanidg')
        except Exception as e2:
            st.error(f"Error crítico al inicializar Earth Engine: {e2}")

iniciar_earth_engine()

# Base de datos local con las coordenadas exactas de encuadre [Oeste, Sur, Este, Norte]
DICCIONARIO_MUNICIPAL = {
    "Buenos Aires": {
        "Trenque Lauquen": [-63.15, -36.50, -62.30, -35.60],
        "La Plata": [-58.15, -35.10, -57.80, -34.80],
        "Bahia Blanca": [-62.50, -38.90, -62.00, -38.60],
        "Chascomus": [-58.30, -35.90, -57.80, -35.40],
        "Guamini": [-62.70, -37.30, -62.10, -36.60]
    }
}

if "dicc_fechas" not in st.session_state:
    st.session_state.dicc_fechas = {}
if "localidad_guardada" not in st.session_state:
    st.session_state.localidad_guardada = ""

# --- CONSTRUCTOR DE LA NOTIFICACIÓN MODAL EN EL MEDIO DE LA PANTALLA ---
@st.dialog("⚠️ Control de Calidad por Nubosidad")
def mostrar_popup_alerta(porcentaje_nubes):
    st.markdown("### ¡Mapas Hídricos Generados!")
    st.write(f"Nota: La información de las **Layer 2 y Layer 3 (En la Fecha)** puede no ser precisa debido al alto porcentaje de nubosidad registrado en la órbita actual.")
    st.info(f"☁️ **Índice de nubosidad detectado:** {porcentaje_nubes:.1f}% (Umbral máximo sugerido: 20.0%)")
    st.write("---")
    st.caption("Cierra este aviso desde la cruz (X) superior derecha para auditar el mapa de fondo.")

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR TRADICIONAL ESTABLE)
# =========================================================================
st.sidebar.title("💧 HydroVision Argentina")
st.sidebar.markdown("### Clasificación Hídrica - Límites del Partido")
st.sidebar.write("---")

provincia_usuario = st.sidebar.selectbox("1. Selecciona la Provincia:", options=list(DICCIONARIO_MUNICIPAL.keys()), index=0)
partido_usuario = st.sidebar.selectbox("2. Selecciona el Departamento/Partido:", options=list(DICCIONARIO_MUNICIPAL[provincia_usuario].keys()), index=0)

id_localidad = f"{provincia_usuario}_{partido_usuario}"
if id_localidad != st.session_state.localidad_guardada:
    st.session_state.dicc_fechas = {}
    st.session_state.localidad_guardada = id_localidad

st.sidebar.write("---")

btn_conectar_catalogo = st.sidebar.button("🔍 1. Buscar Fechas en Catálogo", use_container_width=True)

if btn_conectar_catalogo:
    with st.sidebar.spinner("Consultando catálogo completo de órbitas..."):
        try:
            limites_caja = DICCIONARIO_MUNICIPAL[provincia_usuario][partido_usuario]
            roi_coordenadas = ee.Geometry.Rectangle(limites_caja)

            coleccion_fechas = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(roi_coordenadas)
            lista_milisegundos = coleccion_fechas.aggregate_array('system:time_start').getInfo()

            if lista_milisegundos:
                tmp_dicc = {}
                for ms in lista_milisegundos:
                    if ms:
                        fecha_legible = datetime.datetime.fromtimestamp(ms / 1000.0).strftime('%Y-%m-%d')
                        tmp_dicc[fecha_legible] = int(ms)
                st.session_state.dicc_fechas = dict(sorted(tmp_dicc.items()))
            else:
                st.sidebar.warning("⚠️ No se encontraron pasadas satelitales en esta región geográfica.")
        except Exception as err_catalogo:
            st.sidebar.error("💥 ERROR AL EXTRAER FECHAS:")
            st.sidebar.code(traceback.format_exc())

fecha_seleccionada_texto = "inicial"

if st.session_state.dicc_fechas:
    fecha_seleccionada_texto = st.sidebar.selectbox(
        "3. Selecciona la Fecha Real de la Imagen:",
        options=list(st.session_state.dicc_fechas.keys()),
        index=len(st.session_state.dicc_fechas) - 1
    )
    milisegundos_seleccionados = st.session_state.dicc_fechas[fecha_seleccionada_texto]
    st.sidebar.write("---")
    ejecutar_analisis = st.sidebar.button("🚀 2. Calcular y Mostrar Mapas", type="primary", use_container_width=True)
else:
    st.sidebar.info("💡 Haz clic arriba en 'Buscar Fechas en Catálogo' para desplegar los días disponibles.")
    ejecutar_analisis = False
# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO POR TILE DIRECTO
# =========================================================================

limites_actuales = DICCIONARIO_MUNICIPAL[provincia_usuario][partido_usuario]

# Promedio de coordenadas indexadas exactas para centrar la cámara de Folium
centro_lon = (limites_actuales[0] + limites_actuales[2]) / 2.0
centro_lat = (limites_actuales[1] + limites_actuales[3]) / 2.0

# CORRECCIÓN DE ESTABILIDAD: Inicializamos con OpenStreetMap nativo para que el lienzo dibuje su estructura sí o sí
mapa_final_render = folium.Map(location=[centro_lat, centro_lon], zoom_start=10, control_scale=True, tiles='OpenStreetMap')

# SOLUCCIÓN AL GRIS: Inyectamos el satélite híbrido real de Google como un componente superpuesto indestructible
folium.TileLayer(
    tiles='https://google.com{x}&y={y}&z={z}',
    attr='Google Satélite Híbrido',
    name='💻 Vista Satelital Google',
    overlay=True, # Lo seteamos como overlay activo por defecto sobre el mapa claro
    opacity=1.0
).add_to(mapa_final_render)

if ejecutar_analisis and st.session_state.dicc_fechas:
    with st.sidebar.spinner("Procesando evento hídrico e índices estadísticos..."):
        try:
            roi_final = ee.Geometry.Rectangle(limites_actuales)

            ee_fecha_base = ee.Date(milisegundos_seleccionados)
            anio_imagen_elegida = ee_fecha_base.get('year').getInfo()
            
            if anio_imagen_elegida >= 2026:
                anio_linea_base = 2025
            else:
                anio_linea_base = anio_imagen_elegida
                
            fecha_inicio_anio = f"{anio_linea_base}-01-01"
            fecha_fin_anio = f"{anio_linea_base}-12-31"
            fecha_inicio_evt = ee_fecha_base.advance(-1, 'day')
            fecha_fin_evt = ee_fecha_base.advance(1, 'day')
            
            # Dibujar contorno discontinuo negro del partido seleccionado
            geometria_geojson = roi_final.getInfo()
            folium.GeoJson(
                geometria_geojson,
                name=f"Límites Geográficos de {partido_usuario}",
                style_function=lambda x: {'fillColor': 'none', 'color': '#000000', 'weight': 3.0, 'dashArray': '6, 6'}
            ).add_to(mapa_final_render)

            coleccion_evt_base = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                             .filterBounds(roi_final) \
                             .filterDate(fecha_inicio_evt, fecha_fin_evt)
            
            nubes_órbita = 0.0
            if coleccion_evt_base.size().getInfo() > 0:
                nubes_órbita = float(ee.Image(coleccion_evt_base.first()).get('CLOUDY_PIXEL_PERCENTAGE').getInfo() or 0.0)

            def calcular_ndwi_nativo(img):
                ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
                return img.addBands(ndwi)

            # A) PROMEDIO ANUAL AUTOMÁTICO DE FONDO
            coleccion_anual_base = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
                                .filterBounds(roi_final) \
                                .filterDate(fecha_inicio_anio, fecha_fin_anio)
                                
            coleccion_anual_ndwi = coleccion_anual_base.map(calcular_ndwi_nativo).select('NDWI')
            frecuencia_anual = coleccion_anual_ndwi.map(lambda img: img.gt(0)).mean()
            agua_permanente_anual = frecuencia_anual.gte(0.80).int()

            # B) PROCESAMIENTO DE LA IMAGEN DE LA FECHA SELECCIONADA
            imagen_fecha = coleccion_evt_base.map(calcular_ndwi_nativo).select('NDWI').max()
            agua_en_fecha = imagen_fecha.gt(0).int()

            # C) INTERSECCIÓN DE LAS 3 CAPAS INDEPENDIENTES SUPERPUESTAS
            capa_anual_perm = agua_permanente_anual
            
            # Ajuste dinámico de la capa 2 permanente en la fecha para interceptar lagos reales
            agua_estable_fecha = frecuencia_anual.gte(0.60).int()
            capa_fecha_perm = agua_en_fecha.multiply(agua_estable_fecha)
            
            frecuencia_temporal = frecuencia_anual.gte(0.20).multiply(frecuencia_anual.lte(0.55)).int()
            capa_fecha_temp = agua_en_fecha.multiply(frecuencia_temporal)
            
            # Recorte por máscara nativa de transparencia pura
            recorte_anual_perm = capa_anual_perm.updateMask(capa_anual_perm).clip(roi_final)
            recorte_fecha_perm = capa_fecha_perm.updateMask(capa_fecha_perm).clip(roi_final)
            recorte_fecha_temp = capa_fecha_temp.updateMask(capa_fecha_temp).clip(roi_final)

            # Inyección limpia usando el url_format oficial nativo de Google Earth Engine
            try:
                map_id_1 = ee.data.getMapId({'image': recorte_anual_perm, 'visParams': {'min': 0, 'max': 1, 'palette': ['#2ECC71']}})
                folium.TileLayer(tiles=map_id_1['tile_fetcher'].url_format, attr='GEE', name='🟢 1. Fondo Anual Permanente (>80%)', overlay=True, opacity=0.75).add_to(mapa_final_render)
            except:
                pass

            try:
                map_id_2 = ee.data.getMapId({'image': recorte_fecha_perm, 'visParams': {'min': 0, 'max': 1, 'palette': ['#9B59B6']}})
                folium.TileLayer(tiles=map_id_2['tile_fetcher'].url_format, attr='GEE', name='🟣 2. Permanente en la Fecha', overlay=True, opacity=0.75).add_to(mapa_final_render)
            except:
                pass

            try:
                map_id_3 = ee.data.getMapId({'image': recorte_fecha_temp, 'visParams': {'min': 0, 'max': 1, 'palette': ['#E74C3C']}})
                folium.TileLayer(tiles=map_id_3['tile_fetcher'].url_format, attr='GEE', name='🔴 3. Temporaria en la Fecha (20%-55%)', overlay=True, opacity=0.75).add_to(mapa_final_render)
            except:
                pass
            
            # ACTIVAR EL POP-UP MODAL SI SUPERA EL 20% DE NUBES
            if nubes_órbita > 20.0:
                mostrar_popup_alerta(nubes_órbita)
            else:
                st.success(f"📊 ¡Mapas de superposición hídrica generados con éxito para {partido_usuario} el {fecha_seleccionada_texto}! Índice óptimo: {nubes_órbita:.1f}%")

        except Exception as err_mapa:
            st.error("💥 ERROR AL PROCESAR LAS CAPAS HIDROLÓGICAS:")
            st.code(traceback.format_exc())

folium.LayerControl().add_to(mapa_final_render)
folium_static(mapa_final_render, width=1100, height=750)
