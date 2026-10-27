import streamlit as st
import pandas as pd
import psycopg2
import plotly.express as px
from datetime import datetime, timedelta
import io


# ==========================================
# 1. CONFIGURACIÓN DE PÁGINA E INTERFAZ
# ==========================================
st.set_page_config(page_title="Dashboard PMX AE A1 v2.0", layout="wide")

if "tema_oscuro" not in st.session_state:
    st.session_state.tema_oscuro = False

col_vacia, col_switch = st.columns([0.85, 0.15])
with col_switch:
    activar_oscuro = st.toggle("🌙 Modo Oscuro", value=st.session_state.tema_oscuro)
    if activar_oscuro != st.session_state.tema_oscuro:
        st.session_state.tema_oscuro = activar_oscuro
        st.rerun()

# Control de estilos CSS estables (Modo Oscuro / Claro)
if st.session_state.tema_oscuro:
    st.markdown("""
        <style>
        header[data-testid="stHeader"] { background: rgba(0,0,0,0) !important; }
        footer { visibility: hidden; }
        .stApp { background-color: #121212 !important; color: #FAFAFA !important; }
        .stSidebar { background-color: #1E1E1E !important; }
        h1, h2, h3, p, label, .stMarkdown { color: #FAFAFA !important; }
        </style>
    """, unsafe_allow_html=True)
    template_graficos = "plotly_dark"
else:
    st.markdown("""
        <style>
        header[data-testid="stHeader"] { background-color: #FFFFFF !important; }
        footer { visibility: hidden; }
        .stApp { background-color: #FFFFFF !important; color: #31333F !important; }
        .stSidebar { background-color: #F0F2F6 !important; }
        h1, h2, h3, p, label, .stMarkdown { color: #31333F !important; }
        </style>
    """, unsafe_allow_html=True)
    template_graficos = "plotly"

st.title("📊 Dashboard Automatizado de Mantenimiento v2.0")

# ==========================================
# 2. CONEXIÓN A POSTGRESQL CON CACHÉ OPTIMIZADO
# ==========================================
@st.cache_resource
def init_connection():
    return psycopg2.connect(
        host="10.234.37.16",
        database="PMX AE A1",
        user="POWER_BI_READING",
        password="JJNS9290*",
        port="5432"
    )

@st.cache_data(ttl=300)
def _execute_cached_query(query, params=None):
    conn = init_connection()
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columns)

def run_query(query, params=None):
    try:
        params_tuple = tuple(params) if params is not None else None
        return _execute_cached_query(query, params_tuple)
    except Exception as e:
        try:
            init_connection().rollback()
        except:
            pass
        st.sidebar.error(f"❌ Error en SQL: {e}")
        return pd.DataFrame()

# ==========================================
# 3. BARRA LATERAL: FILTROS GLOBALES
# ==========================================
st.sidebar.header("Filtros Globales")

hoy = datetime.today()
hace_una_semana = hoy - timedelta(days=7) # <-- Cambiado a 7 días para la última semana

fecha_rango = st.sidebar.date_input(
    "Selecciona el rango de fechas:",
    value=(hace_una_semana, hoy), # <-- Usamos la nueva variable aquí
    max_value=hoy
)

# Carga de Líneas
try:
    df_lineas_base = run_query('SELECT DISTINCT line FROM "down_time" UNION SELECT DISTINCT line_maintenance_track_main FROM maintenance_track_main')
    lista_lineas = sorted(df_lineas_base['line'].dropna().unique().tolist())
except Exception:
    lista_lineas = []

linea_seleccionada = st.sidebar.selectbox("Selecciona la Línea:", ["Todas"] + lista_lineas)

# Carga de Máquinas (En cascada según la línea)
try:
    if linea_seleccionada != "Todas":
        df_maquinas_filtradas = run_query("""
            SELECT DISTINCT machine FROM "down_time" WHERE line = %s
            UNION 
            SELECT DISTINCT machine_maintenance_track_main FROM maintenance_track_main WHERE line_maintenance_track_main = %s
        """, (linea_seleccionada, linea_seleccionada))
    else:
        df_maquinas_filtradas = run_query('SELECT DISTINCT machine FROM "down_time" UNION SELECT DISTINCT machine_maintenance_track_main FROM maintenance_track_main')
    
    lista_maquinas_dinamicas = sorted(df_maquinas_filtradas['machine'].dropna().unique().tolist())
except Exception:
    lista_maquinas_dinamicas = []

# Multiselect optimizado para evitar lentitud y recargas infinitas
with st.sidebar.expander("Selecciona las Máquinas:", expanded=True):
    maquinas_seleccionadas = st.multiselect(
        "Máquinas disponibles:",
        options=lista_maquinas_dinamicas,
        default=[]
    )

turno_seleccionado = st.sidebar.selectbox("Turno (Downtime):", ["Todos", "1", "2", "3"])
# Carga de Responsables (Owners) de forma dinámica
try:
    df_owners_base = run_query('SELECT DISTINCT owner_down_time FROM "down_time"')
    lista_owners = sorted(df_owners_base['owner_down_time'].dropna().unique().tolist())
except Exception:
    lista_owners = []

owners_seleccionados = st.sidebar.multiselect(
    "Selecciona los Responsables / Owners:",
    options=lista_owners,
    default=[]
)


# ==========================================
# 4. PROCESAMIENTO CENTRALIZADO DE DATOS
# ==========================================
if len(fecha_rango) == 2:
    fecha_inicio, fecha_fin = fecha_rango
    dias_totales = (fecha_fin - fecha_inicio).days + 1
    minutos_disponibles_totales = dias_totales * 24 * 60

    # Construcción de Queries estables de Downtime
    query_down = 'SELECT date_register::date, line, machine, shift, reason_for_loss, hour_down_time, minutes_down_time::numeric, owner_down_time FROM "down_time" WHERE 1=1'
    params_down = []

    query_down += " AND date_register::date BETWEEN %s AND %s"
    params_down.extend([fecha_inicio, fecha_fin])

    if linea_seleccionada != "Todas":
        query_down += " AND line = %s"
        params_down.append(linea_seleccionada)

    if maquinas_seleccionadas:
        placeholders = ",".join(["%s"] * len(maquinas_seleccionadas))
        query_down += f" AND machine IN ({placeholders})"
        params_down.extend(list(maquinas_seleccionadas))

    if turno_seleccionado != "Todos":
        query_down += " AND shift = %s"
        params_down.append(turno_seleccionado)
        # Filtro inteligente para las múltiples etiquetas de responsables seleccionadas
    if owners_seleccionados:
        placeholders_own = ",".join(["%s"] * len(owners_seleccionados))
        query_down += f" AND owner_down_time IN ({placeholders_own})"
        params_down.extend(list(owners_seleccionados))



    # Ejecución de Consulta Central
    df_down = run_query(query_down, params_down)
    if not df_down.empty:
        df_down["minutes_down_time"] = pd.to_numeric(df_down["minutes_down_time"], errors='coerce').fillna(0)

    # ==========================================
    # 5. CREACIÓN DE PESTAÑAS (TABS)
    # ==========================================
    tab1, tab2, tab3 = st.tabs([
        "📈 Indicadores Clave (OEE, MTTR, MTBF)", 
        "🚨 TOP 3 Tiempos Muertos Grandes", 
        "⏱️ TOP 5 Microparos Repetitivos"
    ])

    # ---------------------------------------------------------
    # PESTAÑA 1: INDICADORES CLAVE (OEE REAL READY)
    # ---------------------------------------------------------
    with tab1:
        st.subheader("Indicadores de Desempeño Operativo (OEE Real)")
        
        if df_down.empty:
            st.info("No hay datos disponibles para calcular indicadores en este rango de fechas.")
        else:
            # --- 1. CÁLCULO DE DISPONIBILIDAD ---
            total_minutos_paro = df_down["minutes_down_time"].sum()
            total_fallas = len(df_down)
            
            num_maquinas = len(maquinas_seleccionadas) if maquinas_seleccionadas else max(1, df_down["machine"].nunique())
            tiempo_disponible_ajustado = minutos_disponibles_totales * num_maquinas
            
            disponibilidad = ((tiempo_disponible_ajustado - total_minutos_paro) / tiempo_disponible_ajustado)
            disponibilidad = max(0.0, min(1.0, disponibilidad)) 

            # --- 2. ESPACIO RESERVADO PARA RENDIMIENTO ---
            # NOTA OEE 2.0: Cuando consigas la tabla de producción, calcula aquí el rendimiento:
            # rendimiento = piezas_reales / piezas_teoricas
            rendimiento = 1.0  # Placeholder (100%) hasta que tengas los datos

            # --- 3. ESPACIO RESERVADO PARA CALIDAD ---
            # NOTA OEE 2.0: Cuando consigas los datos de scrap, calcula aquí la calidad:
            # calidad = piezas_buenas / piezas_totales
            calidad = 1.0  # Placeholder (100%) hasta que tengas los datos

            # --- 4. CÁLCULO DEL OEE GLOBAL ---
            oee_global = (disponibilidad * rendimiento * calidad) * 100
            
            # --- 5. CÁLCULO DE TIEMPOS MEDIOS ---
            mttr = total_minutos_paro / total_fallas if total_fallas > 0 else 0
            mtbf = (tiempo_disponible_ajustado - total_minutos_paro) / total_fallas if total_fallas > 0 else tiempo_disponible_ajustado

            # --- Despliegue de Tarjetas KPI en Tab 1 ---
            kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
            with kpi_col1:
                st.metric(label="📊 OEE Global Estimado", value=f"{oee_global:.2f}%")
            with kpi_col2:
                st.metric(label="⏱️ MTTR (Tiempo de Reparación)", value=f"{mttr:.2f} min")
            with kpi_col3:
                st.metric(label="🔄 MTBF (Tiempo Entre Fallas)", value=f"{mtbf:.2f} min")

    # ---------------------------------------------------------
    # PESTAÑA 2: TOP 3 TIEMPOS MUERTOS GRANDES (REESTRUCTURADO)
    # ---------------------------------------------------------
    with tab2:
        st.subheader("🚨 Gestión Dinámica de Tiempos Muertos Grandes")
        
        if df_down.empty:
            st.info("No hay registros de downtime para los filtros seleccionados.")
        else:
            # 1. Obtener los 3 peores incidentes individuales para las tarjetas
            df_top3 = df_down.sort_values(by="minutes_down_time", ascending=False).head(3)
            
            cols_cards = st.columns(3)
            for idx, (_, row) in enumerate(df_top3.iterrows()):
                with cols_cards[idx]:
                    # Formateamos la fecha para que se vea limpia (Día/Mes/Año)
                    fecha_formateada = pd.to_datetime(row['date_register']).strftime('%d/%m/%Y')
                    
                    st.markdown(f"""
                    <div style="background-color: {'#262626' if st.session_state.tema_oscuro else '#FFEBEE'}; 
                                padding: 15px; border-radius: 10px; border-left: 5px solid #FF5252; min-height: 260px;">
                        <h4 style="margin:0 0 10px 0; color:#FF5252;">🔴 Incidente #{idx + 1}</h4>
                        <p style="margin:3px 0; font-size:14px;"><b>📅 Fecha:</b> {fecha_formateada}</p>
                        <p style="margin:3px 0; font-size:14px;"><b>🛤️ Línea:</b> {row['line']}</p>
                        <p style="margin:3px 0; font-size:14px;"><b>⏱️ Duración:</b> {int(row['minutes_down_time'])} min</p>
                        <p style="margin:3px 0; font-size:14px;"><b>🤖 Máquina:</b> {row['machine']}</p>
                        <p style="margin:3px 0; font-size:14px;"><b>🔄 Turno:</b> Turno {row['shift']}</p>
                        <p style="margin:3px 0; font-size:14px;"><b>👤 Responsable:</b> {row['owner_down_time']}</p>
                        <p style="margin:8px 0 0 0; font-size:13px; color:gray; line-height: 1.2;"><b>📝 Razón:</b> {row['reason_for_loss']}</p>
                    </div>
                    """, unsafe_allow_html=True)


            
            st.markdown("---")
            
            
        # 2. GRÁFICA DE COMPARATIVA POR MÁQUINA A ANCHO COMPLETO
            st.write("#### 📊 Comparativa de Impacto por Evento Individual")

            # =========================================================================
            # 2. GRÁFICA INTELIGENTE: POR LÍNEA (SI SON TODAS) O POR MÁQUINA (SI ELIGE UNA)
            # =========================================================================
            if not df_down.empty:
                
                # CONDICIÓN DINÁMICA DE AGRUPACIÓN
                if linea_seleccionada == "Todas":
                    # Agrupamos y sumamos por Línea para que no sea abrumador
                    df_acumulado = df_down.groupby(["line"], as_index=False)["minutes_down_time"].sum()
                    df_acumulado = df_acumulado.sort_values(by="minutes_down_time", ascending=False).reset_index(drop=True)
                    
                    eje_x = "line"
                    color_trazo = None  # Plotly coloreará cada línea automáticamente
                    titulo_grafica = "Downtime Acumulado Total por Línea de Producción"
                    etiqueta_x = "Líneas de la Empresa"
                else:
                    # Si hay una línea seleccionada, mantiene tu diseño actual por máquina
                    df_acumulado = df_down.groupby(["machine", "line"], as_index=False)["minutes_down_time"].sum()
                    df_acumulado = df_acumulado.sort_values(by="minutes_down_time", ascending=False).reset_index(drop=True)
                    
                    eje_x = "machine"
                    color_trazo = "line"
                    titulo_grafica = f"Downtime Acumulado Total por Máquina en {linea_seleccionada}"
                    etiqueta_x = "Máquinas / Estaciones"

                # Creamos la gráfica lineal con la configuración dinámica
                fig_linea_acumulada = px.line(
                    df_acumulado,
                    x=eje_x,
                    y="minutes_down_time",
                    color=color_trazo,
                    text=df_acumulado["minutes_down_time"].astype(int),
                    title=titulo_grafica,
                    template=template_graficos
                )

                fig_linea_acumulada.update_traces(
                    mode="lines+markers",
                    marker=dict(size=10, symbol="circle"),
                    textposition="top center"
                )

                fig_linea_acumulada.update_layout(
                    xaxis_title=etiqueta_x,
                    yaxis_title="Minutos Totales Acumulados",
                    xaxis_tickangle=-45,
                    hovermode="closest"
                )

                evento_seleccionado = st.plotly_chart(
                    fig_linea_acumulada, 
                    use_container_width=True,
                    on_select="rerun",          
                    selection_mode="points"     
                )

                # =========================================================================
                # TABLA DINÁMICA INTERACTIVA (SE ADAPTA AL CLIC EN CUALQUIER MODO)
                # =========================================================================
            # Creamos dos columnas para colocar el título a la izquierda y el botón a la derecha
                col_titulo_tab, col_btn_descarga = st.columns([0.70, 0.30])
                with col_titulo_tab:
                    st.write("#### 📋 Historial y Desglose Detallado de Eventos")

                df_tabla_filtrada = df_down.copy()
                
                # Validación inteligente del clic
                if evento_seleccionado and "points" in evento_seleccionado["selection"] and evento_seleccionado["selection"]["points"]:
                    punto_clic = evento_seleccionado["selection"]["points"][0] # Tomamos el primer punto seleccionado
                    valor_clic = punto_clic["x"]
                    
                    # Si estábamos viendo "Todas", el clic filtra la tabla por la línea elegida
                    if linea_seleccionada == "Todas":
                        df_tabla_filtrada = df_tabla_filtrada[df_tabla_filtrada["line"] == valor_clic]
                        st.success(f"🔍 Mostrando desglose exclusivo para la línea: **{valor_clic}**")
                    # Si estábamos viendo una línea, el clic filtra por la máquina elegida
                    else:
                        df_tabla_filtrada = df_tabla_filtrada[df_tabla_filtrada["machine"] == valor_clic]
                        st.success(f"🔍 Mostrando desglose exclusivo para la máquina: **{valor_clic}**")
                else:
                    if linea_seleccionada == "Todas":
                        st.caption("💡 *Tip: Haz clic en el punto de cualquier Línea para filtrar toda la tabla inferior por esa línea.*")
                    else:
                        st.caption("💡 *Tip: Haz clic en el punto de cualquier Máquina para filtrar toda la tabla inferior por esa máquina.*")

                with col_btn_descarga:
                    if not df_tabla_filtrada.empty:
                        df_excel_exportar = df_tabla_filtrada.copy()
                        
                        if len(df_excel_exportar.columns) == 8:
                            df_excel_exportar.columns = [
                                "Fecha Registro", "Línea", "Máquina", "Turno", 
                                "Razón de la Pérdida", "Hora Paro", "Minutos Evento", "Responsable / Owner"
                            ]
                        
                        # Formato CSV compatible con Excel garantizando caracteres correctos (acentos, eñes)
                        csv_datos = df_excel_exportar.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
                        
                        st.download_button(
                            label="📥 Descargar Tabla en Excel",
                            data=csv_datos,
                            file_name=f"Reporte_Downtime_{datetime.today().strftime('%Y%m%d')}.csv",
                            mime="text/csv",
                            use_container_width=True
                        )


                df_tabla_filtrada.columns = [
                    "Fecha Registro", "Línea", "Máquina", "Turno", 
                    "Razón de la Pérdida", "Hora Paro", "Minutos Evento", "Responsable / Owner"
                ]

                st.dataframe(df_tabla_filtrada, use_container_width=True, hide_index=True)

            else:
                st.info("No hay datos disponibles para mostrar.")





            st.markdown("---")

            # 3. IMPACTO POR TURNO EN LA PARTE INFERIOR
            st.write("#### 🕒 Impacto por Turno (Downtime Total)")
            df_turnos = df_down.groupby("shift")["minutes_down_time"].sum().to_dict()
            
            c_t1, c_t2, c_t3 = st.columns(3)
            with c_t1:
                minutos_t1 = int(df_turnos.get(1, df_turnos.get("1", 0)))
                st.metric(label="⏱️ Turno 1", value=f"{minutos_t1} min")
            with c_t2:
                minutos_t2 = int(df_turnos.get(2, df_turnos.get("2", 0)))
                st.metric(label="⏱️ Turno 2", value=f"{minutos_t2} min")
            with c_t3:
                minutos_t3 = int(df_turnos.get(3, df_turnos.get("3", 0)))
                st.metric(label="⏱️ Turno 3", value=f"{minutos_t3} min")

            st.markdown("---")

            # 4. NUEVA GRÁFICA DE CATEGORÍAS (REEMPLAZA AL HISTORIAL CRONOLÓGICO)
            st.write("#### 👤 Análisis de Downtime por Responsable / Owner")
            if not df_down.empty:
                # Agrupamos y sumamos los minutos netos acumulados por cada Responsable/Departamento
                df_acumulado_owner = df_down.groupby(["owner_down_time"], as_index=False)["minutes_down_time"].sum()
                # Ordenamos de mayor a menor impacto para tener una tendencia descendente limpia
                df_acumulado_owner = df_acumulado_owner.sort_values(by="minutes_down_time", ascending=False).reset_index(drop=True)

                # Creamos la gráfica de línea limpia con marcadores circulares en los vértices
                fig_linea_owner = px.line(
                    df_acumulado_owner,
                    x="owner_down_time",
                    y="minutes_down_time",
                    text=df_acumulado_owner["minutes_down_time"].astype(int), # Minutos enteros limpios arriba de cada punto
                    title="Downtime Acumulado por Departamento / Responsable Detectado",
                    template=template_graficos
                )

                # Ajustamos los puntos en los topes
                fig_linea_owner.update_traces(
                    mode="lines+markers",  # Línea continua + puntos en los vértices
                    marker=dict(size=12, symbol="circle", color="#FF5252"), # Punto rojo llamativo
                    textposition="top center"
                )

                fig_linea_owner.update_layout(
                    xaxis_title="Responsable / Owner (Departamento)",
                    yaxis_title="Minutos Totales Acumulados",
                    xaxis_tickangle=-45,
                    hovermode="closest"
                )

                # Desplegamos la gráfica activando la escucha de clics interactivos
                owner_seleccionado = st.plotly_chart(
                    fig_linea_owner, 
                    use_container_width=True,
                    on_select="rerun",          # Obliga a Streamlit a actualizar la página al hacer clic
                    key="chart_owner_select",   # Llave única para evitar conflictos con la gráfica de arriba
                    selection_mode="points"     # Captura el punto exacto seleccionado
                )

                # =========================================================================
                # TABLA DE DESGLOSE DINÁMICA POR RESPONSABLE
                # =========================================================================
                st.write("##### 📋 Desglose Detallado de Eventos por Responsable")

                df_tabla_owner = df_down.copy()

                # Validamos si el usuario hizo clic en algún punto/departamento de la gráfica
                if owner_seleccionado and "points" in owner_seleccionado["selection"] and owner_seleccionado["selection"]["points"]:
                    punto_owner_clic = owner_seleccionado["selection"]["points"][0]
                    departamento_clic = punto_owner_clic["x"]

                    
                    # Filtramos la tabla inmediatamente para ese departamento seleccionado
                    df_tabla_owner = df_tabla_owner[df_tabla_owner["owner_down_time"] == departamento_clic]
                    st.success(f"🔍 Mostrando eventos asignados exclusivamente a: **{departamento_clic}** (Haz clic fuera del punto para limpiar el filtro)")
                else:
                    st.caption("💡 *Tip: Haz clic directamente en el punto de cualquier Departamento para filtrar esta tabla de desglose.*")

                # Formateamos los encabezados de la tabla para el usuario final
                df_tabla_owner.columns = [
                    "Fecha Registro", "Línea", "Máquina", "Turno", 
                    "Razón de la Pérdida", "Hora Paro", "Minutos Evento", "Responsable / Owner"
                ]

                # Desplegamos la tabla interactiva de responsables
                st.dataframe(df_tabla_owner, use_container_width=True, hide_index=True)

            else:
                st.info("No hay datos disponibles para mostrar el análisis por responsable.")


       # ---------------------------------------------------------
    # PESTAÑA 3: TOP 5 MICROPAROS REPETITIVOS (DESARROLLADA ⏱️)
    # ---------------------------------------------------------
    with tab3:
        st.subheader("⏱️ Análisis de Frecuencia y Repetibilidad (Microparos)")
        st.write("Los microparos son pérdidas menores (≤ 5 min) que afectan el ritmo de producción debido a su alta repetibilidad.")

        if df_down.empty:
            st.info("No hay datos de paros registrados para calcular microparos.")
        else:
            # 1. Filtramos los microparos (Eventos menores o iguales a 5 minutos)
            UMBRAL_MINUTOS = 5.0
            df_microparos = df_down[df_down["minutes_down_time"] <= UMBRAL_MINUTOS].copy()

            if df_microparos.empty:
                st.info(f"✨ ¡Excelente! No se registraron microparos (paros ≤ {UMBRAL_MINUTOS} min) en el rango seleccionado.")
            else:
                # 2. Agrupamos por máquina para contar la FRECUENCIA (cuántas veces ocurrió)
                df_pareto = df_microparos.groupby(["machine", "line"], as_index=False).size()
                df_pareto.columns = ["machine", "line", "frecuencia"]
                
                # 3. Ordenamos de mayor a menor frecuencia para la estructura de Pareto
                df_pareto = df_pareto.sort_values(by="frecuencia", ascending=False).reset_index(drop=True)
                
                # 4. Calculamos el porcentaje acumulado para la línea de Pareto
                total_eventos = df_pareto["frecuencia"].sum()
                df_pareto["porcentaje"] = (df_pareto["frecuencia"] / total_eventos) * 100
                df_pareto["porcentaje_acumulado"] = df_pareto["porcentaje"].cumsum()

                # Tomamos las peores máquinas para el TOP
                df_top_microparos = df_pareto.head(5)

                # --- SECCIÓN VISUAL 1: MÉTRICAS GENERALES DE MICROPAROS ---
                m1, m2, m3 = st.columns(3)
                m1.metric("🔢 Total Frecuencia Microparos", f"{total_eventos} veces")
                m2.metric("🤖 Máquina Más Repetitiva", str(df_top_microparos["machine"].iloc[0]))
                m3.metric("💥 Máximo de Repeticiones", f"{int(df_top_microparos['frecuencia'].iloc[0])} veces")

                st.write("---")

                # --- SECCIÓN VISUAL 2: GRÁFICA DE PARETO (COMBINADA) ---
                st.markdown("### 📊 Diagrama de Pareto: Frecuencia de Microparos por Estación")
                
                # Barra principal: Frecuencia por máquina
                fig_pareto = px.bar(
                    df_top_microparos,
                    x="machine",
                    y="frecuencia",
                    color="line",
                    text="frecuencia",
                    title="Top Estaciones con Mayor Repetibilidad de Fallas Menores",
                    template=template_graficos
                )
                
                # Añadimos la línea del porcentaje acumulado (80/20) en un segundo eje o superpuesta
                fig_pareto.update_traces(textposition="outside")
                fig_pareto.update_layout(
                    xaxis_title="Máquinas / Estaciones",
                    yaxis_title="Cantidad de Veces (Frecuencia)",
                    xaxis_tickangle=-45
                )
                
                st.plotly_chart(fig_pareto, use_container_width=True)

                # --- SECCIÓN VISUAL 3: TABLA DE REPETIBILIDAD ---
                st.markdown("#### 📋 Detalle de Frecuencias Acumuladas")
                
                df_tabla_pareto = df_top_microparos.copy()
                df_tabla_pareto.columns = ["Máquina", "Línea", "Frecuencia (Veces)", "Impacto %", "% Acumulado"]
                # Formateamos los decimales para la tabla
                df_tabla_pareto["Impacto %"] = df_tabla_pareto["Impacto %"].map('{:.1f}%'.format)
                df_tabla_pareto["% Acumulado"] = df_tabla_pareto["% Acumulado"].map('{:.1f}%'.format)
                
                st.dataframe(df_tabla_pareto, use_container_width=True, hide_index=True)
