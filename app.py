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
        # Coloca tu ID de proyecto de Google Cloud / Earth Engine aquí adentro
        ee.Initialize(project='ee-raanidg') 
    except Exception as e:
        st.error(f"Error al inicializar Earth Engine: {e}")

iniciar_earth_engine()

# ... (El resto del diccionario DATA_GEOGRAFICA y la lógica del mapa quedan exactamente igual)


# Diccionario estático rápido País -> Provincias (FAO GAUL Level 1)
DATA_GEOGRAFICA = {
    "Argentina": ["Buenos Aires", "Cordoba", "Santa Fe", "Mendoza", "Chaco", "Formosa", "Misiones", "Corrientes", "Entre Rios", "La Pampa", "Rio Negro", "Chubut", "Santa Cruz", "Tierra del Fuego", "San Juan", "San Luis", "La Rioja", "Catamarca", "Santiago del Estero", "Tucuman", "Salta", "Jujuy", "Neuquén"],
    "Brazil": ["Sao Paulo", "Rio de Janeiro", "Minas Gerais", "Bahia", "Parana", "Rio Grande do Sul", "Amazonas", "Mato Grosso"],
    "Chile": ["Santiago", "Valparaiso", "Antofagasta", "Biobio", "Araucania", "Magallanes"],
    "Mexico": ["Distrito Federal", "Jalisco", "Nuevo Leon", "Veracruz", "Puebla", "Baja California"],
    "Spain": ["Madrid", "Cataluna", "Andalucia", "Valencia", "Galicia", "Pais Vasco"],
    "United States": ["California", "Texas", "Florida", "New York", "Illinois", "Washington"]
}

# =========================================================================
# 2. DISEÑO DE LA INTERFAZ DE USUARIO (SIDEBAR PREDICTIVO)
# =========================================================================
st.sidebar.title("💧 HydroVision")
st.sidebar.markdown("### Visor Global de Dinámica Hídrica y NDWI")
st.sidebar.write("---")

# Selectores dinámicos con opción de autocompletado y búsqueda predictiva
pais_seleccionado = st.sidebar.selectbox(
    "1. Selecciona o escribe el País:",
    options=list(DATA_GEOGRAFICA.keys()),
    index=0
)

# Al ser obligatorio, recuperamos las provincias específicas del país elegido
provincias_disponibles = DATA_GEOGRAFICA[pais_seleccionado]
provincia_seleccionada = st.sidebar.selectbox(
    "2. Selecciona o escribe la Provincia/Estado (Obligatorio):",
    options=provincias_disponibles,
    index=0
)

# Cuadro para ingresar el año de análisis
anio_actual = datetime.datetime.now().year
anio_seleccionado = st.sidebar.number_input(
    "3. Año de Análisis:",
    min_value=2015,  # Lanzamiento de Sentinel-2
    max_value=anio_actual,
    value=2023,
    step=1
)

ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO DEL MAPA
# =========================================================================

# Usamos el constructor principal de geemap para evitar el error de foliumap
M = geemap.Map()
M.add_basemap("HYBRID") # Fondo satelital híbrido oficial de Google

if ejecutar_analisis:
    with st.spinner("Procesando imágenes satelitales Sentinel-2 en Google Earth Engine..."):
        
        # Definir fechas del año completo elegido
        fecha_inicio = f"{anio_seleccionado}-01-01"
        fecha_fin = f"{anio_seleccionado}-12-31"
        
        # Cargar base de datos vectorial de provincias (FAO GAUL Level 1)
        provincias_db = ee.FeatureCollection("FAO/GAUL/2015/level1")
        
        # Filtrado estricto por País y Provincia elegida
        roi = provincias_db.filter(ee.Filter.eq('adm0_name', pais_seleccionado)) \
                           .filter(ee.Filter.eq('adm1_name', provincia_seleccionada))
        
        # Centrar el mapa dinámicamente en la geometría recortada de la provincia
        M.centerObject(roi, 7)
        
        # Cargar colección de imágenes Sentinel-2 (Nivel 2A corregido por atmósfera)
        coleccion_s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(roi) \
            .filterDate(fecha_inicio, fecha_fin) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30))

            
        # Función para enmascarar nubes y calcular el NDWI
        def calcular_ndwi(img):
            qa = img.select('QA60')
            cloud_bit_mask = 1 << 10
            cirrus_bit_mask = 1 << 11
            mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cirrus_bit_mask).eq(0))
            
            # NDWI = (B3_Verde - B8_NIR) / (B3_Verde + B8_NIR)
            ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
            return img.addBands(ndwi).updateMask(mask)
            
        # Aplicar el cálculo a toda la serie temporal de imágenes del año
        coleccion_ndwi = coleccion_s2.map(calcular_ndwi).select('NDWI')
        
        # Calcular el promedio de frecuencia de agua (Píxeles donde NDWI > 0)
        frecuencia_agua = coleccion_ndwi.map(lambda img: img.gt(0)).mean()
        
        # Clasificación estricta usando tus umbrales modificados
        agua_temporaria = frecuencia_agua.gte(0.20).And(frecuencia_agua.lte(0.55))
        agua_semi_temporaria = frecuencia_agua.gt(0.55).And(frecuencia_agua.lt(0.80))
        agua_permanente = frecuencia_agua.gte(0.80)
        
        # Aislar y recortar capas a la geometría exacta de la provincia
        capa_permanente = agua_permanente.updateMask(agua_permanente).clip(roi)
        capa_semi_temporaria = agua_semi_temporaria.updateMask(agua_semi_temporaria).clip(roi)
        capa_temporaria = agua_temporaria.updateMask(agua_temporaria).clip(roi)
        
        # Detección automática de anomalías por inundaciones extremas
        agua_normal = frecuencia_agua.gte(0.55)
        ndwi_maximo = coleccion_ndwi.max()
        agua_en_crecida = ndwi_maximo.gt(0)
        zona_inundada = agua_en_crecida.where(agua_normal, 0).clip(roi)
        
        # Inyectar las capas calculadas de Earth Engine en el mapa interactivo
        M.addLayer(capa_permanente, {'palette': ['#0000FF']}, 'Cuerpos de Agua Permanentes (>80%)')
        M.addLayer(capa_semi_temporaria, {'palette': ['#4169E1']}, 'Cuerpos de Agua Semi Temporarios (55%-80%)')
        M.addLayer(capa_temporaria, {'palette': ['#00BFFF']}, 'Cuerpos de Agua Temporarios (20%-55%)');
        M.addLayer(zona_inundada, {'palette': ['#FF0000']}, 'Zonas de Inundación / Crecidas Excesivas')

# Renderizar el mapa directamente en Streamlit
M.to_streamlit(height=750)
