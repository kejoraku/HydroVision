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

# Diccionario optimizado con los nombres de provincias exactamente como los busca la FAO (sin tildes)
DATA_GEOGRAFICA = {
    "Argentina": ["Buenos Aires", "Cordoba", "Santa Fe", "Mendoza", "Chaco", "Formosa", "Misiones", "Corrientes", "Entre Rios", "La Pampa", "Rio Negro", "Chubut", "Santa Cruz", "Tierra del Fuego", "San Juan", "San Luis", "La Rioja", "Catamarca", "Santiago del Estero", "Tucuman", "Salta", "Jujuy", "Neuquen"],
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

pais_seleccionado = st.sidebar.selectbox(
    "1. Selecciona o escribe el País:",
    options=list(DATA_GEOGRAFICA.keys()),
    index=0
)

provincias_disponibles = DATA_GEOGRAFICA[pais_seleccionado]
provincia_seleccionada = st.sidebar.selectbox(
    "2. Selecciona o escribe la Provincia/Estado (Obligatorio):",
    options=provincias_disponibles,
    index=0
)

anio_actual = datetime.datetime.now().year
anio_seleccionado = st.sidebar.number_input(
    "3. Año de Análisis:",
    min_value=2015,
    max_value=anio_actual,
    value=2023,
    step=1
)

ejecutar_analisis = st.sidebar.button("Calcular y Mostrar Mapas", type="primary")

# =========================================================================
# 3. LÓGICA DE PROCESAMIENTO ESPACIAL Y RENDERIZADO DEL MAPA
# =========================================================================

M = geemap.Map(center=[-38.4161, -63.6167], zoom=4)
M.add_basemap("HYBRID") 

if ejecutar_analisis:
    with st.spinner("Procesando imágenes satelitales Sentinel-2 en Google Earth Engine..."):
        
        fecha_inicio = f"{anio_seleccionado}-01-01"
        fecha_fin = f"{anio_seleccionado}-12-31"
        
        provincias_db = ee.FeatureCollection("FAO/GAUL/2015/level1")
        
        roi = provincias_db.filter(ee.Filter.eq('adm0_name', pais_seleccionado)) \
                           .filter(ee.Filter.eq('adm1_name', provincia_seleccionada))
        
        # Filtro con un umbral de nubes más flexible (40%) para garantizar que siempre haya imágenes
        coleccion_s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(roi) \
            .filterDate(fecha_inicio, fecha_fin) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 40))
            
        # CONTROL DE SEGURIDAD INTERNO: Comprobamos si la colección contiene datos reales
        conteo_imagenes = coleccion_s2.size().getInfo()
        
        if conteo_imagenes == 0:
            st.error(f"❌ No se encontraron imágenes satelitales válidas para la región de {provincia_seleccionada} en el año {anio_seleccionado}. Intenta seleccionando otro año.")
        else:
            st.info(f"📸 Procesando {conteo_imagenes} escenas satelitales encontradas...")
            
            def calcular_ndwi(img):
                qa = img.select('QA60')
                cloud_bit_mask = 1 << 10
                cirrus_bit_mask = 1 << 11
                mask = qa.bitwiseAnd(cloud_bit_mask).eq(0).And(qa.bitwiseAnd(cirrus_bit_mask).eq(0))
                
                ndwi = img.normalizedDifference(['B3', 'B8']).rename('NDWI')
                return img.addBands(ndwi).updateMask(mask)
                
            coleccion_ndwi = coleccion_s2.map(calcular_ndwi).select('NDWI')
            
            # Frecuencia de agua
            frecuencia_agua = coleccion_ndwi.map(lambda img: img.gt(0)).mean()
            
            # Clasificación hídrica
            agua_temporaria = frecuencia_agua.gte(0.20).And(frecuencia_agua.lte(0.55))
            agua_semi_temporaria = frecuencia_agua.gt(0.55).And(frecuencia_agua.lt(0.80))
            agua_permanente = frecuencia_agua.gte(0.80)
            
            capa_permanente = agua_permanente.updateMask(agua_permanente).clip(roi)
            capa_semi_temporaria = agua_semi_temporaria.updateMask(agua_semi_temporaria).clip(roi)
            capa_temporaria = agua_temporaria.updateMask(agua_temporaria).clip(roi)
            
            # Anomalías de inundación
            agua_normal = frecuencia_agua.gte(0.55)
            ndwi_maximo = coleccion_ndwi.max()
            agua_en_crecida = ndwi_maximo.gt(0)
            zona_inundada = agua_en_crecida.where(agua_normal, 0).clip(roi)
            
            # Dibujar capas en el mapa de forma segura
            M.addLayer(capa_permanente, {'palette': ['#0000FF']}, 'Cuerpos de Agua Permanentes (>80%)')
            M.addLayer(capa_semi_temporaria, {'palette': ['#4169E1']}, 'Cuerpos de Agua Semi Temporarios (55%-80%)')
            M.addLayer(capa_temporaria, {'palette': ['#00BFFF']}, 'Cuerpos de Agua Temporarios (20%-55%)')
            M.addLayer(zona_inundada, {'palette': ['#FF0000']}, 'Zonas de Inundación / Crecidas Excesivas')

# Renderizar el mapa en pantalla
M.to_streamlit(height=750)
