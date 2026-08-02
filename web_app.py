import os
import sqlite3
import json
import psycopg2
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, session, redirect, url_for, send_file
from datetime import datetime
from calendar import monthrange
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, LETTER, LEGAL
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import io
from werkzeug.utils import secure_filename

load_dotenv()
app = Flask(__name__)
app.secret_key = 'clave_super_secreta_sisgaleno2026'

DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith('postgresql')

@app.before_request
def proteger_rutas():
    if request.endpoint in {'login', 'logout', 'index', 'static'}:
        return None
    if not session.get('usuario') or not session.get('rol'):
        return redirect(url_for('login'))
    return None

def get_db_connection():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect('sisgaleno2026.db')

# ==========================================
# BASE DE DATOS 
# ==========================================
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if IS_POSTGRES: 
        auto_inc = "SERIAL"
        conflict = "ON CONFLICT (usuario) DO NOTHING"
    else: 
        auto_inc = "INTEGER PRIMARY KEY AUTOINCREMENT"
        conflict = ""

    # --- USUARIOS ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS usuarios ( id {auto_inc}, usuario TEXT UNIQUE NOT NULL, password TEXT NOT NULL, rol TEXT NOT NULL )''')
    if IS_POSTGRES:
        cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES ('admin', 'admin', 'administrador') ON CONFLICT (usuario) DO NOTHING")
        cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES ('doctor', 'doctor', 'medico') ON CONFLICT (usuario) DO NOTHING")
        cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES ('lab', 'lab', 'laboratorista') ON CONFLICT (usuario) DO NOTHING")
        cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES ('nurse', 'nurse', 'enfermera') ON CONFLICT (usuario) DO NOTHING")
        cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES ('tecnologo', 'tecnologo', 'tecnologo') ON CONFLICT (usuario) DO NOTHING")
    else:
        cursor.execute("INSERT OR IGNORE INTO usuarios (usuario, password, rol) VALUES ('admin', 'admin', 'administrador')")
        cursor.execute("INSERT OR IGNORE INTO usuarios (usuario, password, rol) VALUES ('doctor', 'doctor', 'medico')")
        cursor.execute("INSERT OR IGNORE INTO usuarios (usuario, password, rol) VALUES ('lab', 'lab', 'laboratorista')")
        cursor.execute("INSERT OR IGNORE INTO usuarios (usuario, password, rol) VALUES ('nurse', 'nurse', 'enfermera')")
        cursor.execute("INSERT OR IGNORE INTO usuarios (usuario, password, rol) VALUES ('tecnologo', 'tecnologo', 'tecnologo')")

    # --- PACIENTES ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pacientes (
        id {auto_inc}, 
        historia_clinica TEXT UNIQUE NOT NULL,
        dni TEXT UNIQUE NOT NULL, 
        nombre TEXT NOT NULL, 
        apellido TEXT NOT NULL, 
        fecha_nacimiento TEXT, 
        telefono TEXT, 
        celular TEXT,
        direccion TEXT, 
        sexo TEXT DEFAULT '', 
        edad INTEGER DEFAULT 0,
        convivientes TEXT, 
        acompanante_nombre TEXT,
        acompanante_telefono TEXT,
        acompanante_parentesco TEXT
    )''')
    
    # --- SERVICIOS ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS servicios ( id {auto_inc}, nombre TEXT NOT NULL, precio_base REAL DEFAULT 0 )''')
    for svc in [('MEDICINA GENERAL', 50.0), ('MEDICINA INTERNA', 60.0), ('MEDICINA FISICA', 40.0), ('PEDIATRIA', 35.0), ('GINECOLOGIA', 70.0), ('TRAUMATOLOGIA', 65.0), ('CIRUGIA', 100.0), ('OTROS', 50.0)]:
        if IS_POSTGRES: cursor.execute("INSERT INTO servicios (nombre, precio_base) VALUES (%s, %s) ON CONFLICT (nombre) DO NOTHING", svc)
        else: cursor.execute("INSERT OR IGNORE INTO servicios (nombre, precio_base) VALUES (?, ?)", svc)

    # --- CITAS ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS citas (
        id {auto_inc}, 
        id_paciente INTEGER, 
        id_servicio INTEGER, 
        id_medico INTEGER, 
        fecha_cita TEXT, 
        estado TEXT, 
        motivo_consulta TEXT,
        tipo_asegurado TEXT,
        numero_boleta TEXT,
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id), 
        FOREIGN KEY(id_servicio) REFERENCES servicios(id)
    )''')

    # --- CAJA (BOLETAS) ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pagos (
        id {auto_inc}, 
        id_cita INTEGER,
        id_paciente INTEGER, 
        numero_boleta TEXT UNIQUE NOT NULL,
        descripcion TEXT, 
        monto REAL, 
        fecha_pago TEXT, 
        estado TEXT,
        FOREIGN KEY(id_cita) REFERENCES citas(id),
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id)
    )''')

    # --- LABORATORIO ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_catalogo ( id {auto_inc}, codigo TEXT, descripcion TEXT NOT NULL, precio REAL DEFAULT 0 )''')
    cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, codigo, descripcion, precio) VALUES (1, '145', 'HEMOGRAMA COMPLETO', 50.00)")
    cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, codigo, descripcion, precio) VALUES (2, 'G002', 'GLUCOSA EN AYUNAS', 20.00)")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_parametros ( id {auto_inc}, id_examen_catalogo INTEGER, nombre_parametro TEXT NOT NULL, unidad TEXT, rango_referencia TEXT, orden INTEGER DEFAULT 0, FOREIGN KEY(id_examen_catalogo) REFERENCES examenes_catalogo(id) )''')

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS ordenes_laboratorio ( id {auto_inc}, id_paciente INTEGER, id_examen INTEGER, id_cita INTEGER, fecha_emision TEXT, estado TEXT, precio REAL, id_pago INTEGER, FOREIGN KEY(id_paciente) REFERENCES pacientes(id), FOREIGN KEY(id_examen) REFERENCES examenes_catalogo(id), FOREIGN KEY(id_cita) REFERENCES citas(id), FOREIGN KEY(id_pago) REFERENCES pagos(id) )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS resultados_lab ( id {auto_inc}, id_orden INTEGER, id_parametro INTEGER, resultado TEXT, FOREIGN KEY(id_orden) REFERENCES ordenes_laboratorio(id), FOREIGN KEY(id_parametro) REFERENCES examenes_parametros(id) )''')

    # --- ATENCIÓN MÉDICA ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS diagnosticos ( id {auto_inc}, id_cita INTEGER, id_medico INTEGER, diagnostico TEXT, tratamiento TEXT, descanso_medico_dias INTEGER, informe_pdf_path TEXT, FOREIGN KEY(id_cita) REFERENCES citas(id) )''')
    
    # --- CONFIGURACIÓN ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS configuracion_sistema ( id INTEGER PRIMARY KEY DEFAULT 1, nombre_sistema TEXT DEFAULT 'SISGALENO2026', tamano_hoja TEXT DEFAULT 'A4', logo_path TEXT DEFAULT '', encabezado_texto TEXT DEFAULT 'Laboratorio Clínico', pie_pagina_texto TEXT DEFAULT 'Generado automáticamente por el sistema.', report_header TEXT DEFAULT 'INFORME DE ATENCIÓN CLÍNICA', report_footer TEXT DEFAULT 'Documento generado por SISGALENO2026' )''')
    cursor.execute("INSERT OR IGNORE INTO configuracion_sistema (id) VALUES (1)")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS config_modulos ( id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE NOT NULL, activo INTEGER DEFAULT 1, descripcion TEXT )''')
    modulos_iniciales = [('Admisión', 'Gestión de pacientes y citas'), ('Caja', 'Cobros y emisión de boletas'), ('Laboratorio', 'Procesamiento de muestras y resultados'), ('Atención Médica', 'Evaluación médica e informes clínicos'), ('Configuración', 'Panel de control del sistema')]
    for mod in modulos_iniciales:
        if IS_POSTGRES: cursor.execute("INSERT INTO config_modulos (nombre, descripcion) VALUES (%s, %s) ON CONFLICT (nombre) DO NOTHING", mod)
        else: cursor.execute("INSERT OR IGNORE INTO config_modulos (nombre, descripcion) VALUES (?, ?)", mod)

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS medicos ( id {auto_inc}, id_usuario INTEGER UNIQUE, especialidad TEXT, horario TEXT, FOREIGN KEY(id_usuario) REFERENCES usuarios(id) )''')

    # --- NUEVA TABLA DE PERMISOS DINÁMICOS ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS permisos_roles (
        id {auto_inc},
        rol TEXT NOT NULL,
        modulo TEXT NOT NULL,
        UNIQUE(rol, modulo) ON CONFLICT IGNORE
    )''')
    
    # Asignar permisos iniciales por defecto
    roles_permisos = [
        ('administrador', 'Admisión'), ('administrador', 'Caja'), ('administrador', 'Laboratorio'), ('administrador', 'Atención Médica'), ('administrador', 'Configuración'),
        ('medico', 'Admisión'), ('medico', 'Caja'), ('medico', 'Atención Médica'),
        ('laboratorista', 'Laboratorio'), ('tecnologo', 'Laboratorio'), ('enfermera', 'Admisión')
    ]
    for r, m in roles_permisos:
        if IS_POSTGRES: cursor.execute("INSERT INTO permisos_roles (rol, modulo) VALUES (%s, %s) ON CONFLICT DO NOTHING", (r, m))
        else: cursor.execute("INSERT OR IGNORE INTO permisos_roles (rol, modulo) VALUES (?, ?)", (r, m))
    
    conn.commit()
    conn.close()

init_db()


def asegurar_columna_id_pago_ordenes():
    conn = get_db_connection(); cursor = conn.cursor()
    if IS_POSTGRES:
        try:
            cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name='ordenes_laboratorio' AND column_name='id_pago'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE ordenes_laboratorio ADD COLUMN id_pago INTEGER")
        except Exception:
            pass
    else:
        cursor.execute("PRAGMA table_info(ordenes_laboratorio)")
        columnas = [row[1] for row in cursor.fetchall()]
        if 'id_pago' not in columnas:
            cursor.execute("ALTER TABLE ordenes_laboratorio ADD COLUMN id_pago INTEGER")
    conn.commit(); conn.close()

asegurar_columna_id_pago_ordenes()

# ==========================================
# FUNCIONES AUXILIARES
# ==========================================
def obtener_configuracion():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT nombre_sistema, tamano_hoja, logo_path, encabezado_texto, pie_pagina_texto, report_header, report_footer FROM configuracion_sistema WHERE id = 1")
    config = cursor.fetchone()
    conn.close()
    return config

def obtener_tamano_pagina():
    config = obtener_configuracion()
    tamano_str = config[1] if config and config[1] else 'A4'
    if tamano_str == 'LETTER': return LETTER
    elif tamano_str == 'LEGAL': return LEGAL
    else: return A4

def get_user_modules(rol):
    if not rol:
        return []
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT modulo FROM permisos_roles WHERE rol=?", (rol,))
    mods = [row[0] for row in cursor.fetchall()]
    conn.close()
    return mods

def generar_siguiente_hc():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT historia_clinica FROM pacientes WHERE historia_clinica IS NOT NULL")
    historias = {row[0] for row in cursor.fetchall()}
    conn.close()
    numero = 1
    while True:
        hc = f"HC-{numero:04d}"
        if hc not in historias:
            return hc
        numero += 1

def generar_siguiente_boleta():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM pagos")
    max_id = cursor.fetchone()[0]
    conn.close()
    return f"B-{max_id+1:04d}" if max_id else "B-0001"

def crear_paciente_sistema(dni, nombre, apellido, telefono='', direccion='', fecha_nacimiento=''):
    dni = (dni or '').strip()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, historia_clinica FROM pacientes WHERE dni = ? LIMIT 1", (dni,))
    paciente_existente = cursor.fetchone()
    if paciente_existente:
        conn.close()
        return paciente_existente[1]

    hc = generar_siguiente_hc()
    cursor.execute("""
        INSERT INTO pacientes (historia_clinica, dni, nombre, apellido, fecha_nacimiento, telefono, direccion)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (hc, dni, nombre, apellido, fecha_nacimiento, telefono, direccion))
    conn.commit(); conn.close()
    return hc


def generar_pdf_boleta(id_pago):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT p.id, p.numero_boleta, p.monto, p.fecha_pago, p.descripcion, pa.nombre, pa.apellido, pa.dni, pa.historia_clinica, c.fecha_cita
        FROM pagos p
        LEFT JOIN pacientes pa ON p.id_paciente = pa.id
        LEFT JOIN citas c ON p.id_cita = c.id
        WHERE p.id = ?
    """, (id_pago,))
    pago_data = cursor.fetchone()
    conn.close()
    if not pago_data:
        return None

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    c.setFillColor(colors.HexColor('#0d2b45'))
    c.rect(40, height - 80, width - 80, 60, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 16)
    c.drawString(60, height - 52, 'BOLETA DE PAGO')
    c.setFont('Helvetica', 10)
    c.drawString(60, height - 72, 'SISGALENO2026')
    c.setFillColor(colors.black)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(60, height - 120, f'Boleta: {pago_data[1]}')
    c.setFont('Helvetica', 10)
    c.drawString(60, height - 140, f'Fecha: {pago_data[3]}')
    c.drawString(60, height - 160, f'Paciente: {pago_data[5]} {pago_data[6]}')
    c.drawString(60, height - 180, f'DNI: {pago_data[7]}')
    c.drawString(60, height - 200, f'Historia Clínica: {pago_data[8] or "N/E"}')
    c.drawString(60, height - 220, f'Concepto: {pago_data[4] or "Sin detalle"}')
    if pago_data[9]:
        c.drawString(60, height - 240, f'Cita: {pago_data[9]}')
    c.setFont('Helvetica-Bold', 12)
    c.drawString(60, height - 280, f'Monto: S/ {float(pago_data[2] or 0):.2f}')
    c.setFont('Helvetica', 9)
    c.drawString(60, height - 320, 'Documento generado por SISGALENO2026')
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer

# ==========================================
# DISEÑO Y ESTILOS (MENÚ DINÁMICO POR PERMISOS)
# ==========================================
LAYOUT_BASE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ nombre_sistema }} - Clínica</title>
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #f4f7f6; color: #333; }
        .navbar { background: linear-gradient(90deg, #0d2b45 0%, #1a4d70 100%); color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .navbar a { color: #f8f9fa; margin: 0 12px; font-weight: 500; transition: 0.3s; }
        .navbar a:hover { color: #72c6f7; transform: translateY(-1px); }
        .navbar .logo { font-size: 1.5rem; font-weight: bold; letter-spacing: 1px; color: white; }
        .navbar .logo span { color: #72c6f7; }
        .container { max-width: 1100px; margin: 30px auto; padding: 20px; background: white; border-radius: 16px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08); }
        .btn { display: inline-block; padding: 10px 24px; margin: 4px; border: none; border-radius: 50px; font-weight: 600; color: white; text-align: center; cursor: pointer; transition: 0.3s; }
        .btn-primary { background: #007bff; }.btn-success { background: #28a745; }.btn-danger { background: #dc3545; }.btn-warning { background: #ffc107; color: #333; }
        input, select, textarea { width: 100%; padding: 10px; margin: 6px 0 12px 0; border: 1px solid #ced4da; border-radius: 8px; box-sizing: border-box; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; border-radius: 12px; overflow: hidden; }
        th { background-color: #0d2b45; color: white; padding: 14px; }
        td { padding: 14px; background-color: #ffffff; border-bottom: 1px solid #eee; }
        .badge { display: inline-block; padding: 5px 12px; border-radius: 50px; font-size: 12px; font-weight: bold; }
        .badge-pendiente { background: #ffc107; color: #333; }
        .badge-pagado { background: #28a745; color: white; }
        .badge-muestra { background: #17a2b8; color: white; }
        .badge-atendido { background: #6c757d; color: white; }
        .menu-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-top: 20px; }
        .menu-item { background: #e9ecef; padding: 20px; text-align: center; border-radius: 12px; color: #333; font-weight: bold; }
        .menu-item:hover { background: #0d2b45; color: white; }
        .adm-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        @media (max-width: 600px) { .adm-form-grid { grid-template-columns: 1fr; } .navbar { flex-direction: column; } }
        .attribution { text-align: center; font-size: 12px; color: #666; padding: 5px 0; }
        .tabs { display: flex; border-bottom: 2px solid #ddd; margin-bottom: 20px; }
        .tab-btn { background: #f1f1f1; border: 1px solid #ccc; border-bottom: none; padding: 10px 20px; cursor: pointer; margin-right: 5px; border-radius: 8px 8px 0 0; font-weight: bold; color: #666; }
        .tab-btn.active { background: white; color: #0d2b45; border-bottom: 2px solid white; margin-bottom: -2px; box-shadow: 0 -2px 5px rgba(0,0,0,0.05); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">🏥 <span>{{ nombre_sistema }}</span></div>
        <div>
            {% if session.get('usuario') %}
                <span>👤 {{ session.get('usuario') }} ({{ session.get('rol') }})</span>
                <a href="{{ url_for('dashboard') }}">Inicio</a>
                {% if 'Admisión' in user_modules %}
                    <a href="{{ url_for('admision') }}">📋 Admisión</a>
                {% endif %}
                {% if 'Caja' in user_modules %}
                    <a href="{{ url_for('caja') }}">💰 Caja</a>
                {% endif %}
                {% if 'Laboratorio' in user_modules %}
                    <a href="{{ url_for('laboratorio') }}">🧪 Laboratorio</a>
                {% endif %}
                {% if 'Atención Médica' in user_modules %}
                    <a href="{{ url_for('atencion_medica') }}">🩺 Atención Médica</a>
                {% endif %}
                {% if 'Configuración' in user_modules %}
                    <a href="{{ url_for('configuracion_sistema') }}" class="btn btn-warning" style="padding:5px 15px;">⚙️ Config</a>
                {% endif %}
                <a href="{{ url_for('reportes') }}" class="btn btn-primary" style="padding:5px 15px;">📊 Reportes</a>
                <a href="{{ url_for('logout') }}" class="btn btn-danger" style="padding: 5px 15px;">Salir</a>
            {% endif %}
        </div>
    </nav>
    <div class="attribution">Creado por yonanT.b</div>
    <div class="container">
        <!-- CONTENIDO_DINAMICO -->
    </div>
</body>
</html>
"""

# ==========================================
# RUTAS: LOGIN Y DASHBOARD
# ==========================================
@app.route('/')
def index():
    if 'usuario' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('usuario') and session.get('rol'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        user, pwd = request.form['usuario'], request.form['password']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT rol FROM usuarios WHERE usuario=? AND password=?", (user, pwd))
        data = cursor.fetchone(); conn.close()
        if data:
            session['usuario'] = user; session['rol'] = data[0]
            return redirect(url_for('dashboard'))
    
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    
    contenido_login = """
    <h2 style="text-align:center;">Inicio de Sesión</h2>
    <form method="POST" style="max-width:400px; margin:auto;">
        <label>Usuario</label><input type="text" name="usuario" required>
        <label>Contraseña</label><input type="password" name="password" required>
        <button type="submit" class="btn btn-primary" style="width:100%;">Acceder</button>
    </form>
    """
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_login)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

# CORREGIDA LA FUNCIÓN DASHBOARD
@app.route('/dashboard')
def dashboard():
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    user_modules = get_user_modules(session.get('rol'))
    
    # ELIMINADA LA 'f' QUE CAUSABA EL ERROR DE SINTAXIS
    contenido_dashboard = """
    <div style="background:#0d2b45; color:white; padding:30px; border-radius:12px; text-align:center;">
        <h2>🏥 Bienvenido, {{ session.get('usuario') }}</h2><p>{{ nombre_sistema }}</p>
    </div>
    <div class="menu-grid">
        {% if 'Admisión' in user_modules %}<a href="{{ url_for('admision') }}" class="menu-item">📋 Admisión</a>{% endif %}
        {% if 'Caja' in user_modules %}<a href="{{ url_for('caja') }}" class="menu-item">💰 Caja</a>{% endif %}
        {% if 'Laboratorio' in user_modules %}<a href="{{ url_for('laboratorio') }}" class="menu-item">🧪 Laboratorio</a>{% endif %}
        {% if 'Atención Médica' in user_modules %}<a href="{{ url_for('atencion_medica') }}" class="menu-item">🩺 Atención Médica</a>{% endif %}
        <a href="{{ url_for('reportes') }}" class="menu-item">📊 Reportes</a>
    </div>
    """
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_dashboard)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=user_modules)

@app.route('/reportes')
def reportes():
    if not session.get('usuario') or not session.get('rol'):
        return redirect(url_for('login'))

    conn = get_db_connection(); cursor = conn.cursor()

    fecha = request.args.get('fecha') or datetime.now().strftime('%Y-%m-%d')
    mes = request.args.get('mes') or datetime.now().strftime('%Y-%m')
    fecha_inicio = request.args.get('fecha_inicio') or datetime.now().strftime('%Y-%m-01')
    fecha_fin = request.args.get('fecha_fin') or datetime.now().strftime('%Y-%m-%d')

    year, month = map(int, mes.split('-'))
    ultimo_dia = monthrange(year, month)[1]
    mes_inicio = f'{year:04d}-{month:02d}-01'
    mes_fin = f'{year:04d}-{month:02d}-{ultimo_dia:02d}'

    cursor.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita)=?", (fecha,))
    total_citas_dia = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita)=? AND estado='Pagado'", (fecha,))
    citas_pagadas_dia = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*)
        FROM citas c
        LEFT JOIN diagnosticos d ON d.id_cita = c.id
        WHERE date(c.fecha_cita)=? AND d.id IS NOT NULL
    """, (fecha,))
    atenciones_dia = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(monto), 0) FROM pagos WHERE estado='Pagado' AND date(fecha_pago)=?", (fecha,))
    ingresos_dia = float(cursor.fetchone()[0] or 0)

    cursor.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita) BETWEEN ? AND ?", (mes_inicio, mes_fin))
    total_citas_mes = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM citas WHERE date(fecha_cita) BETWEEN ? AND ? AND estado='Pagado'", (mes_inicio, mes_fin))
    citas_pagadas_mes = cursor.fetchone()[0]
    cursor.execute("""
        SELECT COUNT(*)
        FROM citas c
        LEFT JOIN diagnosticos d ON d.id_cita = c.id
        WHERE date(c.fecha_cita) BETWEEN ? AND ? AND d.id IS NOT NULL
    """, (mes_inicio, mes_fin))
    atenciones_mes = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(monto), 0) FROM pagos WHERE estado='Pagado' AND date(fecha_pago) BETWEEN ? AND ?", (mes_inicio, mes_fin))
    ingresos_mes = float(cursor.fetchone()[0] or 0)

    cursor.execute("""
        SELECT u.id, u.usuario, COUNT(DISTINCT d.id) AS atenciones, COUNT(DISTINCT c.id) AS citas_asignadas
        FROM usuarios u
        LEFT JOIN citas c ON c.id_medico = u.id AND date(c.fecha_cita) BETWEEN ? AND ?
        LEFT JOIN diagnosticos d ON d.id_cita = c.id
        WHERE u.rol = 'medico'
        GROUP BY u.id, u.usuario
        ORDER BY atenciones DESC, citas_asignadas DESC
    """, (fecha_inicio, fecha_fin))
    rendimiento_medicos = cursor.fetchall()
    conn.close()

    contenido_reportes = """
    <h2>📊 Reportes y Estadísticas</h2>
    <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:20px;">
        <a href="{{ url_for('reportes', fecha=fecha) }}" class="btn btn-primary">📅 Reporte diario</a>
        <a href="{{ url_for('reportes', mes=mes) }}" class="btn btn-success">📈 Reporte mensual</a>
        <a href="{{ url_for('reportes', fecha_inicio=fecha_inicio, fecha_fin=fecha_fin) }}" class="btn btn-warning">🧑‍⚕️ Rendimiento médico</a>
    </div>

    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:15px; margin-bottom:20px;">
        <div style="background:#f8f9fa; border-left:5px solid #007bff; padding:15px; border-radius:10px;">
            <div style="font-size:12px; color:#666;">Citas del día</div>
            <div style="font-size:28px; font-weight:bold;">{{ total_citas_dia }}</div>
        </div>
        <div style="background:#f8f9fa; border-left:5px solid #28a745; padding:15px; border-radius:10px;">
            <div style="font-size:12px; color:#666;">Citas pagadas hoy</div>
            <div style="font-size:28px; font-weight:bold;">{{ citas_pagadas_dia }}</div>
        </div>
        <div style="background:#f8f9fa; border-left:5px solid #17a2b8; padding:15px; border-radius:10px;">
            <div style="font-size:12px; color:#666;">Atenciones del día</div>
            <div style="font-size:28px; font-weight:bold;">{{ atenciones_dia }}</div>
        </div>
        <div style="background:#f8f9fa; border-left:5px solid #ffc107; padding:15px; border-radius:10px;">
            <div style="font-size:12px; color:#666;">Ingresos del día</div>
            <div style="font-size:28px; font-weight:bold;">S/ {{ '%.2f'|format(ingresos_dia) }}</div>
        </div>
    </div>

    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <h3>📅 Reporte diario</h3>
        <form method="GET" style="display:flex; flex-wrap:wrap; gap:10px; align-items:end;">
            <input type="hidden" name="tipo" value="diario">
            <div><label>Fecha</label><input type="date" name="fecha" value="{{ fecha }}"></div>
            <button type="submit" class="btn btn-primary">Ver reporte</button>
        </form>
        <p style="margin-top:10px;"><b>Fecha seleccionada:</b> {{ fecha }} · <b>Citas:</b> {{ total_citas_dia }} · <b>Pagadas:</b> {{ citas_pagadas_dia }} · <b>Atenciones:</b> {{ atenciones_dia }} · <b>Ingresos:</b> S/ {{ '%.2f'|format(ingresos_dia) }}</p>
    </div>

    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <h3>📈 Reporte mensual</h3>
        <form method="GET" style="display:flex; flex-wrap:wrap; gap:10px; align-items:end;">
            <input type="hidden" name="tipo" value="mensual">
            <div><label>Mes</label><input type="month" name="mes" value="{{ mes }}"></div>
            <button type="submit" class="btn btn-success">Ver reporte</button>
        </form>
        <p style="margin-top:10px;"><b>Mes seleccionado:</b> {{ mes }} · <b>Citas:</b> {{ total_citas_mes }} · <b>Pagadas:</b> {{ citas_pagadas_mes }} · <b>Atenciones:</b> {{ atenciones_mes }} · <b>Ingresos:</b> S/ {{ '%.2f'|format(ingresos_mes) }}</p>
    </div>

    <div style="background:#f8f9fa; padding:15px; border-radius:12px;">
        <h3>🧑‍⚕️ Rendimiento de médicos</h3>
        <form method="GET" style="display:flex; flex-wrap:wrap; gap:10px; align-items:end; margin-bottom:10px;">
            <input type="hidden" name="tipo" value="medicos">
            <div><label>Desde</label><input type="date" name="fecha_inicio" value="{{ fecha_inicio }}"></div>
            <div><label>Hasta</label><input type="date" name="fecha_fin" value="{{ fecha_fin }}"></div>
            <button type="submit" class="btn btn-warning">Ver rendimiento</button>
        </form>
        <table>
            <thead><tr><th>Médico</th><th>Citas asignadas</th><th>Atenciones registradas</th></tr></thead>
            <tbody>
                {% for med in rendimiento_medicos %}
                <tr>
                    <td>{{ med[1] }}</td>
                    <td>{{ med[3] }}</td>
                    <td>{{ med[2] }}</td>
                </tr>
                {% else %}
                <tr><td colspan="3" style="text-align:center;">No hay información de rendimiento aún.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """

    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_reportes)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), fecha=fecha, mes=mes, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, total_citas_dia=total_citas_dia, citas_pagadas_dia=citas_pagadas_dia, atenciones_dia=atenciones_dia, ingresos_dia=ingresos_dia, total_citas_mes=total_citas_mes, citas_pagadas_mes=citas_pagadas_mes, atenciones_mes=atenciones_mes, ingresos_mes=ingresos_mes, rendimiento_medicos=rendimiento_medicos)

# ==========================================
# MÓDULO 1: ADMISIÓN
# ==========================================
@app.route('/admision', methods=['GET', 'POST'])
def admision():
    if 'Admisión' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    mensaje = ""; tipo_mensaje = ""
    if request.method == 'POST' and request.form.get('accion') == 'registrar_paciente':
        dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
        try:
            hc = crear_paciente_sistema(dni, nombre, apellido)
            mensaje = f"Paciente {nombre} {apellido} registrado exitosamente (HC: {hc})."
            tipo_mensaje = "alert-success"
        except sqlite3.IntegrityError:
            mensaje = "Error: El DNI ya está registrado."
            tipo_mensaje = "alert-danger"
        except Exception as e:
            mensaje = f"Error: {str(e)}"
            tipo_mensaje = "alert-danger"

    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes ORDER BY id DESC")
    pacientes = cursor.fetchall()
    cursor.execute("SELECT id, nombre FROM servicios")
    servicios = cursor.fetchall()
    cursor.execute("SELECT id, usuario FROM usuarios WHERE rol='medico'")
    medicos = cursor.fetchall()
    cursor.execute("""
        SELECT c.id, p.historia_clinica, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, c.tipo_asegurado, c.numero_boleta
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        ORDER BY c.fecha_cita DESC
    """)
    citas = cursor.fetchall()
    conn.close()
    
    contenido_admision = """
    <h2>📋 Módulo de Admisión</h2>
    {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:15px;">
            <button onclick="toggleForm('form_cita')" class="btn btn-success">+ Nueva Cita</button>
            <button onclick="toggleForm('form_paciente')" class="btn btn-primary">+ Registrar Paciente</button>
        </div>
        <div id="form_paciente" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4 style="color:#0d2b45;">Registrar Nuevo Paciente</h4>
            <form method="POST">
                <input type="hidden" name="accion" value="registrar_paciente">
                <div class="adm-form-grid">
                    <div><label>DNI</label><input type="text" name="dni" required></div>
                    <div><label>Nombre</label><input type="text" name="nombre" required></div>
                    <div><label>Apellido</label><input type="text" name="apellido" required></div>
                </div>
                <button type="submit" class="btn btn-success">Guardar Paciente</button>
            </form>
        </div>
        <div id="form_cita" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4 style="color:#0d2b45;">Nueva Cita</h4>
            <form method="POST" action="{{ url_for('crear_cita') }}">
                <div class="adm-form-grid">
                    <div><label>Paciente (Buscar por DNI o HC)</label>
                        <select name="id_paciente" required>
                            <option value="">Seleccione Paciente</option>
                            {% for p in pacientes %}<option value="{{ p[0] }}">{{ p[1] }} - {{ p[2] }} {{ p[3] }} ({{ p[4] }})</option>{% endfor %}
                        </select>
                    </div>
                    <div><label>Servicio / Especialidad</label>
                        <select name="id_servicio" required>
                            <option value="">Seleccione</option>
                            {% for s in servicios %}<option value="{{ s[0] }}">{{ s[1] }}</option>{% endfor %}
                        </select>
                    </div>
                    <div><label>Médico</label>
                        <select name="id_medico" required>
                            <option value="">Seleccione</option>
                            {% for m in medicos %}<option value="{{ m[0] }}">{{ m[1] }}</option>{% endfor %}
                        </select>
                    </div>
                    
                    <div><label>Fecha</label><input type="date" name="fecha_cita" required></div>
                    <div><label>Hora</label><input type="time" name="hora_cita" step="60" required></div>

                    <div><label>Tipo de Asegurado</label>
                        <select name="tipo_asegurado" required>
                            <option value="Demanda">Demanda (Particular)</option>
                            <option value="SIS">SIS</option>
                            <option value="SOAT">SOAT</option>
                        </select>
                    </div>
                </div>
                <div class="adm-field" style="margin-top:10px;">
                    <label>Motivo de Consulta</label>
                    <textarea name="motivo_consulta" rows="2"></textarea>
                </div>
                <button type="submit" class="btn btn-primary">Agendar Cita</button>
            </form>
        </div>
    </div>
    <h3>Citas Registradas</h3>
    <table><thead><tr><th>HC</th><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Tipo</th><th>Boleta</th><th>Estado</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for c in citas %}
        <tr>
            <td>{{ c[1] }}</td>
            <td>{{ c[2] }} {{ c[3] }}</td>
            <td>{{ c[4] }}</td>
            <td>{{ c[5] }}</td>
            <td>{{ c[7] }}</td>
            <td>{{ c[8] if c[8] else 'Pendiente' }}</td>
            <td><span class="badge badge-{{ 'pagado' if c[6] == 'Pagado' else 'pendiente' }}">{{ c[6] }}</span></td>
            <td>
                <a href="{{ url_for('imprimir_ficha_admision', id_cita=c[0]) }}" target="_blank" class="btn btn-primary" style="padding:4px 10px; font-size:12px;">📄 Imprimir Ficha</a>
            </td>
        </tr>
        {% else %}
        <tr><td colspan="8" style="text-align:center;">No hay citas registradas aún.</td></tr>
        {% endfor %}
    </tbody></table>
    <script>
        function toggleForm(id) {
            var x = document.getElementById(id);
            x.style.display = x.style.display === 'none' ? 'block' : 'none';
        }
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_admision)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), pacientes=pacientes, servicios=servicios, medicos=medicos, citas=citas, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/admision/crear_cita', methods=['POST'])
def crear_cita():
    if 'Admisión' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    id_paciente = request.form['id_paciente']; id_servicio = request.form['id_servicio']; id_medico = request.form['id_medico']; fecha_str = request.form['fecha_cita']; hora_str = request.form['hora_cita']
    motivo = request.form.get('motivo_consulta', '')
    tipo_asegurado = request.form['tipo_asegurado']
    
    fecha_hora_completa = f"{fecha_str} {hora_str}:00"
    conn = get_db_connection(); cursor = conn.cursor()
    estado_inicial = 'Pagado' if tipo_asegurado in ['SIS', 'SOAT'] else 'Pendiente'
    cursor.execute("""
        INSERT INTO citas (id_paciente, id_servicio, id_medico, fecha_cita, estado, motivo_consulta, tipo_asegurado, numero_boleta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (id_paciente, id_servicio, id_medico, fecha_hora_completa, estado_inicial, motivo, tipo_asegurado, ''))
    conn.commit(); conn.close()
    return redirect(url_for('admision'))

@app.route('/admision/imprimir/<int:id_cita>')
def imprimir_ficha_admision(id_cita):
    if 'Admisión' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.dni, p.edad, p.sexo, p.historia_clinica, s.nombre, u.usuario, c.fecha_cita, c.tipo_asegurado, c.numero_boleta
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        JOIN usuarios u ON c.id_medico = u.id
        WHERE c.id = ?
    """, (id_cita,))
    cita_data = cursor.fetchone()
    conn.close()
    if not cita_data: return "Cita no encontrada.", 404
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    encabezado = config[3] if config else 'Laboratorio Clínico'
    pie_pagina = config[4] if config else 'Generado automáticamente por el sistema.'
    page_size = obtener_tamano_pagina()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=page_size, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    elements = []
    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try:
            img = ImageReader(os.path.join('static', logo_path))
            elements.append(Spacer(1, 10))
        except: pass
    title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], fontSize=16, alignment=1, spaceAfter=20)
    elements.append(Paragraph(f"<b>{nombre_sistema}</b>", title_style))
    elements.append(Paragraph(f"<i>{encabezado}</i>", styles['Heading2']))
    elements.append(Paragraph("<b>FICHA DE ADMISIÓN</b>", styles['Heading2']))
    elements.append(Spacer(1, 15))
    patient_info = f"""
    <b>Paciente:</b> {cita_data[0]} {cita_data[1]}<br/>
    <b>DNI:</b> {cita_data[2]}<br/>
    <b>Historia Clínica:</b> {cita_data[5]}<br/>
    <b>Servicio:</b> {cita_data[6]}<br/>
    <b>Médico:</b> Dr. {cita_data[7]}<br/>
    <b>Fecha de Cita:</b> {cita_data[8]}<br/>
    <b>Tipo Asegurado:</b> {cita_data[9]}
    """
    elements.append(Paragraph(patient_info, styles['Normal']))
    elements.append(Spacer(1, 15))
    elements.append(Paragraph("<b>Motivo de la Consulta:</b>", styles['Normal']))
    elements.append(Paragraph("No especificado" if not cita_data[10] else cita_data[10], styles['Normal']))
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(pie_pagina, styles['Italic']))
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Ficha_Admision_Cita_{id_cita}.pdf", mimetype='application/pdf')

# ==========================================
# MÓDULO 2: CAJA
# ==========================================
@app.route('/caja', methods=['GET', 'POST'])
def caja():
    if 'Caja' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    mensaje = ""; tipo_mensaje = ""

    conn = get_db_connection(); cursor = conn.cursor()
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'registrar_paciente_caja':
            dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
            try:
                hc = crear_paciente_sistema(dni, nombre, apellido)
                mensaje = f"Paciente registrado correctamente (HC: {hc})."
                tipo_mensaje = "alert-success"
            except sqlite3.IntegrityError:
                mensaje = "Error: El DNI ya está registrado."
                tipo_mensaje = "alert-danger"
            except Exception as e:
                mensaje = f"Error: {str(e)}"
                tipo_mensaje = "alert-danger"
        elif accion == 'registrar_cobro_directo':
            id_paciente = request.form.get('id_paciente')
            tipo_item = request.form.get('tipo_item', 'servicio')
            monto = request.form.get('monto', '').strip()
            descripcion = request.form.get('descripcion', '').strip() or 'Cobro directo'
            try:
                if tipo_item in ('laboratorio', 'analisis'):
                    id_examenes = request.form.getlist('id_examenes')
                    if not id_examenes:
                        raise ValueError('Seleccione al menos un examen válido.')
                    detalle_examenes = []
                    monto_total = 0.0
                    examenes_validos = []
                    for id_examen in id_examenes:
                        cursor.execute("SELECT descripcion, precio FROM examenes_catalogo WHERE id=?", (id_examen,))
                        examen = cursor.fetchone()
                        if not examen:
                            continue
                        detalle_examenes.append(examen[0])
                        monto_total += float(examen[1] or 0)
                        examenes_validos.append((id_examen, examen[0], float(examen[1] or 0)))
                    if not detalle_examenes:
                        raise ValueError('Seleccione al menos un examen válido.')
                    monto_final = float(monto) if monto else monto_total
                    fecha_emision = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    descripcion_final = f"{'Laboratorio' if tipo_item == 'laboratorio' else 'Análisis'}: {', '.join(detalle_examenes)}"
                    numero_boleta = generar_siguiente_boleta()
                    cursor.execute("""
                        INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion)
                        VALUES (?, ?, ?, ?, ?, 'Pagado', ?)
                    """, (None, id_paciente, numero_boleta, monto_final, fecha_emision, descripcion or descripcion_final))
                    id_pago = cursor.lastrowid
                    for id_examen, _, precio_examen in examenes_validos:
                        cursor.execute("""
                            INSERT INTO ordenes_laboratorio (id_paciente, id_examen, fecha_emision, estado, precio, id_pago)
                            VALUES (?, ?, ?, 'Pendiente', ?, ?)
                        """, (id_paciente, id_examen, fecha_emision, precio_examen, id_pago))
                    id_cita = None
                else:
                    id_servicio = request.form.get('id_servicio')
                    cursor.execute("SELECT nombre, precio_base FROM servicios WHERE id=?", (id_servicio,))
                    servicio = cursor.fetchone()
                    if not servicio:
                        raise ValueError('Seleccione un servicio válido.')
                    monto_final = float(monto) if monto else float(servicio[1] or 0)
                    descripcion_final = f"Servicio: {servicio[0]}"
                    if tipo_item == 'atencion':
                        descripcion_final = f"Atención: {servicio[0]}"
                    cursor.execute("""
                        INSERT INTO citas (id_paciente, id_servicio, fecha_cita, estado, tipo_asegurado, numero_boleta)
                        VALUES (?, ?, ?, 'Pagado', 'Demanda', ?)
                    """, (id_paciente, id_servicio, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), ''))
                    id_cita = cursor.lastrowid
                if tipo_item not in ('laboratorio', 'analisis'):
                    numero_boleta = generar_siguiente_boleta()
                    cursor.execute("""
                        INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion)
                        VALUES (?, ?, ?, ?, ?, 'Pagado', ?)
                    """, (id_cita, id_paciente, numero_boleta, monto_final, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), descripcion or descripcion_final))
                conn.commit()
                mensaje = f"Boleta emitida correctamente: {numero_boleta}."
                tipo_mensaje = "alert-success"
            except ValueError as e:
                mensaje = f"Error: {str(e)}"
                tipo_mensaje = "alert-danger"
            except Exception as e:
                mensaje = f"Error al registrar el cobro: {str(e)}"
                tipo_mensaje = "alert-danger"

    cursor.execute("""
        SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, s.precio_base, c.id_paciente
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        WHERE c.estado = 'Pendiente' AND c.tipo_asegurado = 'Demanda'
        ORDER BY c.fecha_cita ASC
    """)
    pendientes = cursor.fetchall()
    cursor.execute("""
        SELECT p.id, p.numero_boleta, p.monto, p.fecha_pago, p.descripcion, pa.nombre, pa.apellido, pa.dni
        FROM pagos p
        JOIN pacientes pa ON p.id_paciente = pa.id
        ORDER BY p.fecha_pago DESC
    """)
    historial = cursor.fetchall()
    cursor.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes ORDER BY id DESC")
    pacientes = cursor.fetchall()
    cursor.execute("SELECT id, nombre, precio_base FROM servicios ORDER BY id")
    servicios = cursor.fetchall()
    cursor.execute("SELECT id, descripcion, precio FROM examenes_catalogo ORDER BY id")
    examenes = cursor.fetchall()

    cursor.execute("""
        SELECT id_paciente, descripcion, numero_boleta, monto, fecha_pago
        FROM pagos
        WHERE estado = 'Pagado'
          AND (
              LOWER(descripcion) LIKE 'laboratorio:%'
              OR LOWER(descripcion) LIKE 'análisis:%'
              OR LOWER(descripcion) LIKE 'analisis:%'
          )
        ORDER BY fecha_pago DESC
    """)
    pagos_examenes = cursor.fetchall()

    pagos_resumen = {}
    for id_paciente, descripcion, numero_boleta, monto, fecha_pago in pagos_examenes:
        if not descripcion:
            continue
        if ':' in descripcion:
            detalle = descripcion.split(':', 1)[1].strip()
        else:
            detalle = descripcion.strip()
        pagos_resumen.setdefault(id_paciente, []).append({
            'detalle': detalle,
            'boleta': numero_boleta,
            'monto': float(monto or 0),
            'fecha': fecha_pago
        })

    conn.close()

    contenido_caja = """
    <h2>💰 Módulo de Caja (Cobros, Boletas y Exportación)</h2>
    {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:15px;">
            <button onclick="toggleForm('form_paciente_caja')" class="btn btn-primary">+ Ingresar Paciente</button>
            <button onclick="toggleForm('form_cobro_directo')" class="btn btn-success">+ Cobrar Servicio / Análisis / Atención</button>
        </div>
        <div id="form_paciente_caja" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4 style="color:#0d2b45;">👤 Registrar Paciente en Caja</h4>
            <form method="POST">
                <input type="hidden" name="accion" value="registrar_paciente_caja">
                <div class="adm-form-grid">
                    <div><label>DNI</label><input type="text" name="dni" required></div>
                    <div><label>Nombre</label><input type="text" name="nombre" required></div>
                    <div><label>Apellido</label><input type="text" name="apellido" required></div>
                </div>
                <button type="submit" class="btn btn-primary">Guardar Paciente</button>
            </form>
        </div>
        <div id="form_cobro_directo" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4 style="color:#0d2b45;">🧾 Nuevo Cobro Directo</h4>
            <form method="POST">
                <input type="hidden" name="accion" value="registrar_cobro_directo">
                <div class="adm-form-grid">
                    <div>
                        <label>Paciente</label>
                        <select name="id_paciente" required>
                            {% for p in pacientes %}<option value="{{ p[0] }}">{{ p[2] }} - {{ p[3] }} {{ p[4] }}</option>{% endfor %}
                        </select>
                    </div>
                    <div>
                        <label>Tipo</label>
                        <select name="tipo_item" id="tipo_item" required>
                            <option value="servicio">Servicio</option>
                            <option value="laboratorio">Laboratorio</option>
                            <option value="analisis">Análisis</option>
                            <option value="atencion">Atención</option>
                        </select>
                    </div>
                    <div id="bloque_servicio">
                        <label>Servicio</label>
                        <select name="id_servicio" id="id_servicio">
                            {% for s in servicios %}<option value="{{ s[0] }}" data-precio="{{ s[2] }}">{{ s[1] }} (S/ {{ s[2] }})</option>{% endfor %}
                        </select>
                    </div>
                    <div id="bloque_examenes" style="display:none;">
                        <label>Exámenes</label>
                        <select name="id_examenes" id="id_examenes" multiple size="6">
                            {% for e in examenes %}<option value="{{ e[0] }}" data-precio="{{ e[2] }}">{{ e[1] }} (S/ {{ e[2] }})</option>{% endfor %}
                        </select>
                        <div style="font-size:12px; color:#6c757d; margin-top:5px;">Seleccione uno o varios exámenes. El monto se calculará automáticamente.</div>
                    </div>
                    <div><label>Monto</label><input type="number" step="0.01" name="monto" id="monto" placeholder="Se calculará automáticamente" readonly></div>
                    <div><label>Observación</label><input type="text" name="descripcion" placeholder="Ej. Pago de consulta / análisis"></div>
                </div>
                <button type="submit" class="btn btn-success">Emitir Boleta</button>
            </form>
        </div>
        <div id="resumen_paciente" style="margin-top:15px; padding:15px; border-radius:12px; background:#fff; border:1px solid #e9ecef;">
            <h5 style="margin-top:0; color:#0d2b45;">👁️ Vista previa del paciente</h5>
            <div id="resumen_paciente_contenido" style="color:#6c757d;">Seleccione un paciente para ver los exámenes que ya pagó y el resumen del cobro actual.</div>
        </div>
    </div>
    <h3>Citas Pendientes de Pago (Demanda)</h3>
    <table><thead><tr><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Monto</th><th>Estado</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for c in pendientes %}
        <tr>
            <td>{{ c[1] }} {{ c[2] }}</td>
            <td>{{ c[3] }}</td>
            <td>{{ c[4] }}</td>
            <td>S/ {{ c[6] }}</td>
            <td><span class="badge badge-pendiente">Pendiente</span></td>
            <td>
                <a href="{{ url_for('generar_boleta', id_cita=c[0]) }}" class="btn btn-success" style="padding:4px 10px; font-size:12px;">💰 Generar Boleta</a>
            </td>
        </tr>
        {% else %}<tr><td colspan="6" style="text-align:center;">No hay citas de demanda pendientes.</td></tr>{% endfor %}
    </tbody></table>
    <h3 style="margin-top:30px;">📜 Boletas Emitidas</h3>
    <table><thead><tr><th>Boleta</th><th>Paciente</th><th>Concepto</th><th>Fecha</th><th>Monto</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for h in historial %}
        <tr>
            <td><b>{{ h[1] }}</b></td>
            <td>{{ h[5] }} {{ h[6] }} (DNI: {{ h[7] }})</td>
            <td>{{ h[4] }}</td>
            <td>{{ h[3] }}</td>
            <td>S/ {{ h[2] }}</td>
            <td>
                <a href="{{ url_for('imprimir_boleta_pdf', id_pago=h[0]) }}" target="_blank" class="btn btn-warning" style="padding:4px 10px; font-size:12px;">🖨️ Imprimir</a>
                <a href="{{ url_for('imprimir_boleta_pdf', id_pago=h[0], download='1') }}" class="btn btn-primary" style="padding:4px 10px; font-size:12px;">⬇️ Exportar PDF</a>
            </td>
        </tr>
        {% else %}<tr><td colspan="6" style="text-align:center;">No hay boletas emitidas.</td></tr>{% endfor %}
    </tbody></table>
    <script>
        function toggleForm(id) {
            var x = document.getElementById(id);
            x.style.display = x.style.display === 'none' ? 'block' : 'none';
        }

        var tipoItem = document.getElementById('tipo_item');
        var bloqueServicio = document.getElementById('bloque_servicio');
        var bloqueExamenes = document.getElementById('bloque_examenes');
        var montoInput = document.getElementById('monto');
        var servicioSelect = document.getElementById('id_servicio');
        var examenesSelect = document.getElementById('id_examenes');
        var pacienteSelect = document.querySelector('select[name="id_paciente"]');
        var resumenPaciente = document.getElementById('resumen_paciente_contenido');
        var pagosResumen = {{ pagos_resumen_json|safe }};

        function obtenerSeleccionExamenes() {
            if (!examenesSelect) {
                return [];
            }
            return Array.from(examenesSelect.selectedOptions || []).map(function(option) {
                return {
                    nombre: option.textContent,
                    precio: parseFloat(option.getAttribute('data-precio') || 0)
                };
            });
        }

        function calcularMonto() {
            var tipo = tipoItem ? tipoItem.value : '';
            var total = 0.0;
            if (tipo === 'servicio' || tipo === 'atencion') {
                var servicioOption = servicioSelect ? servicioSelect.options[servicioSelect.selectedIndex] : null;
                total = servicioOption ? parseFloat(servicioOption.getAttribute('data-precio') || 0) : 0;
            } else if (tipo === 'laboratorio' || tipo === 'analisis') {
                var seleccion = obtenerSeleccionExamenes();
                total = seleccion.reduce(function(sum, item) {
                    return sum + item.precio;
                }, 0);
            }
            if (montoInput) {
                montoInput.value = total.toFixed(2);
            }
            actualizarResumenPaciente();
        }

        function actualizarResumenPaciente() {
            if (!resumenPaciente) {
                return;
            }
            var pacienteId = pacienteSelect ? pacienteSelect.value : '';
            var tipo = tipoItem ? tipoItem.value : '';
            var html = [];

            if (pacienteId) {
                var pagosPaciente = pagosResumen[pacienteId] || [];
                if (pagosPaciente.length > 0) {
                    html.push('<div style="margin-bottom:10px;"><strong>Exámenes ya pagados por este paciente:</strong></div>');
                    html.push('<ul style="margin:0 0 10px 18px; padding:0;">');
                    pagosPaciente.forEach(function(item) {
                        html.push('<li>' + item.detalle + ' · Boleta ' + item.boleta + ' · S/ ' + item.monto.toFixed(2) + '</li>');
                    });
                    html.push('</ul>');
                } else {
                    html.push('<div style="margin-bottom:10px; color:#6c757d;">Este paciente aún no tiene exámenes de laboratorio/análisis pagados.</div>');
                }
            } else {
                html.push('<div style="color:#6c757d;">Seleccione un paciente para ver los exámenes que ya pagó.</div>');
            }

            if (tipo === 'laboratorio' || tipo === 'analisis') {
                var seleccion = obtenerSeleccionExamenes();
                if (seleccion.length > 0) {
                    html.push('<div><strong>Resumen del cobro actual:</strong></div>');
                    html.push('<ul style="margin:0 0 0 18px; padding:0;">');
                    seleccion.forEach(function(item) {
                        html.push('<li>' + item.nombre + ' · S/ ' + item.precio.toFixed(2) + '</li>');
                    });
                    html.push('</ul>');
                    html.push('<div style="margin-top:6px; font-weight:bold;">Total a cobrar: S/ ' + (parseFloat(montoInput ? montoInput.value : 0) || 0).toFixed(2) + '</div>');
                }
            }

            resumenPaciente.innerHTML = html.join('');
        }

        if (tipoItem) {
            tipoItem.addEventListener('change', function() {
                var tipo = this.value;
                if (bloqueServicio) bloqueServicio.style.display = (tipo === 'servicio' || tipo === 'atencion') ? 'block' : 'none';
                if (bloqueExamenes) bloqueExamenes.style.display = (tipo === 'laboratorio' || tipo === 'analisis') ? 'block' : 'none';
                calcularMonto();
            });
        }
        if (servicioSelect) servicioSelect.addEventListener('change', calcularMonto);
        if (examenesSelect) examenesSelect.addEventListener('change', calcularMonto);
        if (pacienteSelect) pacienteSelect.addEventListener('change', actualizarResumenPaciente);
        calcularMonto();
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_caja)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), pendientes=pendientes, historial=historial, pacientes=pacientes, servicios=servicios, examenes=examenes, pagos_resumen_json=json.dumps(pagos_resumen), mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/caja/generar_boleta/<int:id_cita>')
def generar_boleta(id_cita):
    if 'Caja' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT s.precio_base, c.id_paciente FROM citas c JOIN servicios s ON c.id_servicio = s.id WHERE c.id=?", (id_cita,))
    row = cursor.fetchone()
    if not row:
        conn.close(); return redirect(url_for('caja'))
    monto = row[0]; id_paciente = row[1]
    numero_boleta = generar_siguiente_boleta()
    descripcion = "Consulta médica"
    cursor.execute("INSERT INTO pagos (id_cita, id_paciente, numero_boleta, monto, fecha_pago, estado, descripcion) VALUES (?, ?, ?, ?, ?, 'Pagado', ?)",
                   (id_cita, id_paciente, numero_boleta, monto, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), descripcion))
    cursor.execute("UPDATE citas SET estado='Pagado', numero_boleta=? WHERE id=?", (numero_boleta, id_cita))
    conn.commit(); conn.close()
    return redirect(url_for('caja'))

@app.route('/caja/boleta_pdf/<int:id_pago>')
def imprimir_boleta_pdf(id_pago):
    if 'Caja' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    buffer = generar_pdf_boleta(id_pago)
    if not buffer:
        return redirect(url_for('caja'))
    download = request.args.get('download', '0') == '1'
    return send_file(buffer, as_attachment=download, download_name=f'boleta_{id_pago}.pdf', mimetype='application/pdf')

# ==========================================
# MÓDULO 3: LABORATORIO
# ==========================================
@app.route('/laboratorio', methods=['GET', 'POST'])
def laboratorio():
    if 'Laboratorio' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    mensaje = ""; tipo_mensaje = ""
    conn = get_db_connection(); cursor = conn.cursor()
    
    if request.method == 'POST':
        accion = request.form.get('accion')
        if accion == 'registrar_paciente_lab':
            dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
            try:
                hc = crear_paciente_sistema(dni, nombre, apellido)
                conn.commit()
                mensaje = f"Paciente {nombre} {apellido} agregado al sistema (HC: {hc})."
                tipo_mensaje = "alert-success"
            except sqlite3.IntegrityError:
                mensaje = "Error: El DNI ya está registrado."
                tipo_mensaje = "alert-danger"
        elif accion == 'editar_paciente_lab':
            id_pac = request.form['id_paciente']; nombre = request.form['nombre']; apellido = request.form['apellido']; dni = request.form['dni']
            try:
                cursor.execute("UPDATE pacientes SET nombre=?, apellido=?, dni=? WHERE id=?", (nombre, apellido, dni, id_pac))
                conn.commit()
                mensaje = "Paciente actualizado correctamente."
                tipo_mensaje = "alert-success"
            except Exception as e:
                mensaje = f"Error al actualizar: {str(e)}"
                tipo_mensaje = "alert-danger"
        elif accion == 'eliminar_paciente_lab':
            id_pac = request.form['id_paciente']
            cursor.execute("""
                SELECT COUNT(*) FROM ordenes_laboratorio o
                JOIN resultados_lab r ON r.id_orden = o.id
                WHERE o.id_paciente = ?
            """, (id_pac,))
            tiene_resultados = cursor.fetchone()[0] > 0
            if tiene_resultados:
                mensaje = "No se puede eliminar. El paciente tiene resultados de laboratorio registrados."
                tipo_mensaje = "alert-danger"
            else:
                cursor.execute("DELETE FROM pacientes WHERE id=?", (id_pac,))
                conn.commit()
                mensaje = "Paciente eliminado exitosamente."
                tipo_mensaje = "alert-success"
        elif accion == 'agregar_examen_lab':
            id_pac = request.form['id_paciente']
            id_examen = request.form['id_examen']
            cursor.execute("SELECT precio FROM examenes_catalogo WHERE id=?", (id_examen,))
            precio = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO ordenes_laboratorio (id_paciente, id_examen, fecha_emision, estado, precio) 
                VALUES (?, ?, ?, 'Pendiente', ?)
            """, (id_pac, id_examen, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), precio))
            conn.commit()
            mensaje = "Examen agregado a la orden del paciente."
            tipo_mensaje = "alert-success"

        conn.close()
        return redirect(url_for('laboratorio'))

    cursor.execute("SELECT id, historia_clinica, dni, nombre, apellido FROM pacientes ORDER BY id DESC")
    pacientes = cursor.fetchall()
    cursor.execute("SELECT id, descripcion, precio FROM examenes_catalogo")
    examenes = cursor.fetchall()
    
    q = request.args.get('q', '').strip()
    search_term = f'%{q}%' if q else '%'
    sql_pend = """
        SELECT o.id, p.nombre, p.apellido, e.descripcion, c.id as cita_id, o.estado,
               CASE
                  WHEN EXISTS (
                      SELECT 1 FROM pagos pg
                      WHERE pg.id_paciente = o.id_paciente
                        AND pg.estado = 'Pagado'
                        AND (
                            LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                            OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                            OR LOWER(pg.descripcion) LIKE '%análisis%'
                        )
                  ) THEN 'Pagado'
                  ELSE 'Pendiente'
               END AS estado_pago,
               (SELECT pg.numero_boleta FROM pagos pg
                WHERE pg.id_paciente = o.id_paciente
                 AND pg.estado = 'Pagado'
                 AND (
                     LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                     OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                     OR LOWER(pg.descripcion) LIKE '%análisis%'
                 )
                ORDER BY pg.id DESC LIMIT 1) AS numero_boleta,
               (SELECT pg.monto FROM pagos pg
                WHERE pg.id_paciente = o.id_paciente
                 AND pg.estado = 'Pagado'
                 AND (
                     LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                     OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                     OR LOWER(pg.descripcion) LIKE '%análisis%'
                 )
                ORDER BY pg.id DESC LIMIT 1) AS monto_pago
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        LEFT JOIN citas c ON o.id_cita = c.id
        WHERE o.estado = 'Pendiente'
          AND EXISTS (
              SELECT 1 FROM pagos pg
              WHERE pg.id_paciente = o.id_paciente
                AND pg.estado = 'Pagado'
                AND (
                   LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                   OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                   OR LOWER(pg.descripcion) LIKE '%análisis%'
                )
          )
          AND (
              p.dni LIKE ?
              OR EXISTS (
                 SELECT 1 FROM pagos pg
                 WHERE pg.id_paciente = o.id_paciente
                   AND pg.estado = 'Pagado'
                   AND pg.numero_boleta LIKE ?
              )
          )
        ORDER BY o.id DESC
    """
    cursor.execute(sql_pend, (search_term, search_term))
    pendientes_muestra = cursor.fetchall()
    
    cursor.execute("""
        SELECT o.id, p.nombre, p.apellido, e.descripcion, o.estado, o.id_cita,
               CASE
                  WHEN EXISTS (
                      SELECT 1 FROM pagos pg
                      WHERE pg.id_paciente = o.id_paciente
                        AND pg.estado = 'Pagado'
                        AND (
                            LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                            OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                            OR LOWER(pg.descripcion) LIKE '%análisis%'
                        )
                  ) THEN 'Pagado'
                  ELSE 'Pendiente'
               END AS estado_pago,
               (SELECT pg.numero_boleta FROM pagos pg
               WHERE pg.id_paciente = o.id_paciente
                 AND pg.estado = 'Pagado'
                 AND (
                     LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                     OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                     OR LOWER(pg.descripcion) LIKE '%análisis%'
                 )
               ORDER BY pg.id DESC LIMIT 1) AS numero_boleta,
               (SELECT pg.monto FROM pagos pg
               WHERE pg.id_paciente = o.id_paciente
                 AND pg.estado = 'Pagado'
                 AND (
                     LOWER(pg.descripcion) LIKE '%' || LOWER(e.descripcion) || '%'
                     OR LOWER(pg.descripcion) LIKE '%laboratorio%'
                     OR LOWER(pg.descripcion) LIKE '%análisis%'
                 )
               ORDER BY pg.id DESC LIMIT 1) AS monto_pago,
               CASE
                  WHEN EXISTS (
                      SELECT 1 FROM resultados_lab rl WHERE rl.id_orden = o.id
                  ) THEN 'Completado'
                  ELSE 'Pendiente'
               END AS resultado_estado
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.estado != 'Pendiente'
        ORDER BY o.id DESC
    """)
    ordenes_proceso = cursor.fetchall()
    conn.close()
    
    contenido_lab = """
    <h2>🧪 Módulo de Laboratorio</h2>
    {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:15px;">
            <button onclick="toggleForm('form_paciente_lab')" class="btn btn-success">+ Agregar Paciente (Laboratorio)</button>
        </div>
        <div id="form_paciente_lab" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
            <h4 style="color:#0d2b45;">📄 Registrar Nuevo Paciente (Directo en Lab)</h4>
            <form method="POST">
                <input type="hidden" name="accion" value="registrar_paciente_lab">
                <div class="adm-form-grid">
                    <div><label>DNI</label><input type="text" name="dni" required></div>
                    <div><label>Nombre</label><input type="text" name="nombre" required></div>
                    <div><label>Apellido</label><input type="text" name="apellido" required></div>
                </div>
                <button type="submit" class="btn btn-success">Guardar Paciente</button>
            </form>
        </div>
        <h4 style="margin-top:15px; color:#0d2b45;">👤 Lista de Pacientes Registrados</h4>
        <div style="overflow-x:auto;">
            <table>
                <thead><tr><th>HC</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead>
                <tbody>
                    {% for p in pacientes %}
                    <tr>
                        <td>{{ p[1] }}</td>
                        <td>{{ p[2] }}</td>
                        <td>{{ p[3] }}</td>
                        <td>{{ p[4] }}</td>
                        <td>
                            <button onclick="editarPacienteLab({{ p[0] }}, '{{ p[3] }}', '{{ p[4] }}', '{{ p[2] }}')" class="btn btn-warning" style="padding:2px 8px; font-size:12px;">✏️ Editar</button>
                            <form method="POST" style="display:inline;" onsubmit="return confirm('¿Está seguro de eliminar a este paciente? Se verificará si tiene resultados activos.');">
                                <input type="hidden" name="accion" value="eliminar_paciente_lab">
                                <input type="hidden" name="id_paciente" value="{{ p[0] }}">
                                <button type="submit" class="btn btn-danger" style="padding:2px 8px; font-size:12px;">🗑️ Eliminar</button>
                            </form>
                            <button onclick="agregarExamenLab({{ p[0] }}, '{{ p[3] }} {{ p[4] }}')" class="btn btn-primary" style="padding:2px 8px; font-size:12px;">➕ Examen</button>
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="5" style="text-align:center;">No hay pacientes registrados en el sistema.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <div id="form_editar_lab" style="display:none; background:#fff3cd; padding:15px; border-radius:12px; margin-bottom:20px; border:1px solid #ffeeba;">
        <h4 style="color:#0d2b45;">✏️ Editar Paciente</h4>
        <form method="POST">
            <input type="hidden" name="accion" value="editar_paciente_lab">
            <input type="hidden" name="id_paciente" id="edit_id_paciente">
            <div class="adm-form-grid">
                <div><label>Nombre</label><input type="text" name="nombre" id="edit_nombre" required></div>
                <div><label>Apellido</label><input type="text" name="apellido" id="edit_apellido" required></div>
                <div><label>DNI</label><input type="text" name="dni" id="edit_dni" required></div>
            </div>
            <button type="submit" class="btn btn-warning">Guardar Cambios</button>
            <button type="button" onclick="document.getElementById('form_editar_lab').style.display='none'" class="btn btn-danger">Cancelar</button>
        </form>
    </div>

    <div id="form_examen_lab" style="display:none; background:#d1ecf1; padding:15px; border-radius:12px; margin-bottom:20px; border:1px solid #bee5eb;">
        <h4 style="color:#0d2b45;">➕ Agregar Examen a Paciente</h4>
        <form method="POST">
            <input type="hidden" name="accion" value="agregar_examen_lab">
            <input type="hidden" name="id_paciente" id="exam_id_paciente">
            <div class="adm-form-grid">
                <div><label>Paciente</label><input type="text" id="exam_nombre_paciente" disabled style="background:#e9ecef;"></div>
                <div><label>Examen</label>
                    <select name="id_examen" required>
                        <option value="">Seleccione</option>
                        {% for e in examenes %}<option value="{{ e[0] }}">{{ e[1] }} - S/ {{ e[2] }}</option>{% endfor %}
                    </select>
                </div>
            </div>
            <button type="submit" class="btn btn-primary">Crear Orden de Examen</button>
            <button type="button" onclick="document.getElementById('form_examen_lab').style.display='none'" class="btn btn-danger">Cancelar</button>
        </form>
    </div>

    <h3 style="color: #17a2b8; margin-top:20px;">📥 Pendientes de Toma de Muestra (Exámenes Pagados)</h3>
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <form method="GET" style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:10px;">
            <div style="flex:1; min-width:200px;">
                <label style="margin:0; font-size:13px;">🔎 Buscar por DNI o N° de Boleta:</label>
                <input type="text" name="q" value="{{ q }}" style="margin:0; padding:8px;" placeholder="Ej: 12345678 o B-0001">
            </div>
            <div><button type="submit" class="btn btn-primary" style="padding:8px 20px;">Buscar</button></div>
            <div><a href="{{ url_for('laboratorio') }}" class="btn btn-warning" style="padding:8px 20px;">Limpiar</a></div>
        </form>
        {% if pendientes_muestra %}
        <table>
            <thead><tr><th>Código</th><th>Paciente</th><th>Examen</th><th>Pago</th><th>Boleta</th><th>Monto</th><th>Acciones</th></tr></thead>
            <tbody>
                {% for p in pendientes_muestra %}
                <tr>
                    <td><b>#{{ p[0] }}</b></td>
                    <td>{{ p[1] }} {{ p[2] }}</td>
                    <td>{{ p[3] }}</td>
                    <td><span class="badge badge-{{ 'pagado' if p[6] == 'Pagado' else 'pendiente' }}">{{ p[6] }}</span></td>
                    <td>{{ p[7] if p[7] else 'Sin boleta' }}</td>
                    <td>S/ {{ p[8] if p[8] is not none else '0.00' }}</td>
                    <td>
                        <a href="{{ url_for('tomar_muestra', id_orden=p[0]) }}" class="btn btn-primary" style="padding:4px 10px; font-size:12px;">🧪 Tomar Muestra</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}
        <div style="padding:20px; text-align:center; color:#666;">No hay exámenes pagados pendientes de muestra.</div>
        {% endif %}
    </div>

    <h3>🔬 Procesamiento de Muestras y Resultados</h3>
    <table><thead><tr><th>Código</th><th>Paciente</th><th>Examen</th><th>Estado</th><th>Pago</th><th>Boleta</th><th>Monto</th><th>Resultado</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for o in ordenes_proceso %}
        <tr>
            <td>#{{ o[0] }}</td>
            <td>{{ o[1] }} {{ o[2] }}</td>
            <td>{{ o[3] }}</td>
            <td>
                <span class="badge badge-{{ 'muestra' if o[4] == 'Muestra Tomada' else 'pagado' if o[4] == 'Completado' else 'pendiente' }}">{{ o[4] }}</span>
            </td>
            <td><span class="badge badge-{{ 'pagado' if o[6] == 'Pagado' else 'pendiente' }}">{{ o[6] }}</span></td>
            <td>{{ o[7] if o[7] else 'Sin boleta' }}</td>
            <td>S/ {{ o[8] if o[8] is not none else '0.00' }}</td>
            <td>{{ o[9] if o[9] else 'Pendiente' }}</td>
            <td>
                {% if o[4] == 'Muestra Tomada' %}
                    <a href="{{ url_for('ingresar_resultado', id_orden=o[0]) }}" class="btn btn-warning" style="padding:4px 10px; font-size:12px;">📝 Procesar</a>
                {% elif o[4] == 'Completado' %}
                    <a href="{{ url_for('imprimir_resultado_lab', id_orden=o[0]) }}" target="_blank" class="btn btn-primary" style="padding:4px 10px; font-size:12px;">🖨️ Imprimir / PDF</a>
                {% endif %}
            </td>
        </tr>
        {% else %}<tr><td colspan="9" style="text-align:center;">No hay muestras en proceso.</td></tr>{% endfor %}
    </tbody></table>
    
    <script>
        function toggleForm(id) {
            var x = document.getElementById(id);
            x.style.display = x.style.display === 'none' ? 'block' : 'none';
        }
        function editarPacienteLab(id, nombre, apellido, dni) {
            document.getElementById('edit_id_paciente').value = id;
            document.getElementById('edit_nombre').value = nombre;
            document.getElementById('edit_apellido').value = apellido;
            document.getElementById('edit_dni').value = dni;
            document.getElementById('form_editar_lab').style.display = 'block';
        }
        function agregarExamenLab(id, nombreCompleto) {
            document.getElementById('exam_id_paciente').value = id;
            document.getElementById('exam_nombre_paciente').value = nombreCompleto;
            document.getElementById('form_examen_lab').style.display = 'block';
        }
    </script>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_lab)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), pacientes=pacientes, examenes=examenes, pendientes_muestra=pendientes_muestra, ordenes_proceso=ordenes_proceso, q=q, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/laboratorio/tomar_muestra/<int:id_orden>', methods=['GET'])
def tomar_muestra(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE ordenes_laboratorio SET estado='Muestra Tomada' WHERE id=?", (id_orden,))
    conn.commit(); conn.close()
    return redirect(url_for('laboratorio'))

@app.route('/laboratorio/resultado/<int:id_orden>', methods=['GET', 'POST'])
def ingresar_resultado(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT o.id, p.nombre, p.apellido, e.descripcion, e.id as id_examen_cat
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id = ?
    """, (id_orden,))
    orden = cursor.fetchone()
    if not orden: return "Orden no encontrada.", 404

    if request.method == 'POST':
        cursor.execute("SELECT id FROM examenes_parametros WHERE id_examen_catalogo=?", (orden[4],))
        params = cursor.fetchall()
        for p in params:
            res_val = request.form.get(f'param_{p[0]}', '')
            cursor.execute("INSERT OR REPLACE INTO resultados_lab (id_orden, id_parametro, resultado) VALUES (?, ?, ?)", (id_orden, p[0], res_val))
        cursor.execute("UPDATE ordenes_laboratorio SET estado='Completado' WHERE id=?", (id_orden,))
        conn.commit(); conn.close()
        return redirect(url_for('laboratorio'))
    
    cursor.execute("SELECT id, nombre_parametro, unidad, rango_referencia FROM examenes_parametros WHERE id_examen_catalogo=? ORDER BY orden ASC", (orden[4],))
    parametros = cursor.fetchall()
    conn.close()

    contenido_res = """
    <h2>📝 Procesar Muestra - Orden #{{ id_orden }}</h2>
    <div style="background:#e9ecef; padding:15px; border-radius:12px;">
        <b>Paciente:</b> {{ orden[1] }} {{ orden[2] }} <br>
        <b>Examen:</b> {{ orden[3] }}
    </div>
    <div style="background:#f8f9fa; padding:20px; border-radius:12px; margin-top:20px;">
        <form method="POST">
            <table>
                <thead><tr><th>Parámetro</th><th>Unidad / Rango</th><th>Resultado</th></tr></thead>
                <tbody>
                    {% for p in parametros %}
                    <tr>
                        <td><b>{{ p[1] }}</b></td>
                        <td style="font-size:12px; color:#666;">{{ p[2] if p[2] else '' }} {{ p[3] if p[3] else '' }}</td>
                        <td><input type="text" name="param_{{ p[0] }}" placeholder="Ingrese valor" style="width:100%; margin:0;"></td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            <div style="margin-top:10px;">
                <button type="submit" class="btn btn-success">💾 Guardar Resultados</button>
                <a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a>
            </div>
        </form>
    </div>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_res)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), id_orden=id_orden, orden=orden, parametros=parametros)

@app.route('/laboratorio/imprimir/<int:id_orden>')
def imprimir_resultado_lab(id_orden):
    if 'Laboratorio' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.dni, e.descripcion, o.fecha_emision, p.edad, p.sexo
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id = ?
    """, (id_orden,))
    orden_data = cursor.fetchone()
    if not orden_data: return "Orden no encontrada.", 404
    
    cursor.execute("""
        SELECT ep.nombre_parametro, ep.unidad, ep.rango_referencia, rl.resultado
        FROM resultados_lab rl
        JOIN examenes_parametros ep ON rl.id_parametro = ep.id
        WHERE rl.id_orden = ?
        ORDER BY ep.orden ASC
    """, (id_orden,))
    resultados_parametros = cursor.fetchall()
    conn.close()
    
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    logo_path = config[2] if config else ''
    pie_pagina = config[4] if config else ''
    page_size = obtener_tamano_pagina()
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=page_size)
    width, height = page_size
    
    c.setFillColor(colors.HexColor("#1C9CD4"))
    c.rect(0, height - 80, width, 80, fill=1, stroke=0)
    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try:
            c.drawImage(os.path.join('static', logo_path), 30, height - 70, width=60, height=50, preserveAspectRatio=True)
        except: pass
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(100, height - 45, nombre_sistema)
    c.setFont("Helvetica", 10)
    c.drawString(100, height - 65, "Sistema de Gestión de Laboratorio Clínico")
    
    c.setStrokeColor(colors.HexColor("#1C9CD4"))
    c.setLineWidth(1)
    box_x, box_y, box_w, box_h = 30, height - 180, width - 60, 90
    c.roundRect(box_x, box_y, box_w, box_h, 10, fill=0, stroke=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, height - 120, "Paciente:")
    c.setFont("Helvetica", 10)
    c.drawString(120, height - 120, f"{orden_data[0]} {orden_data[1]}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(300, height - 120, "Edad:")
    c.setFont("Helvetica", 10)
    c.drawString(350, height - 120, f"{orden_data[5] if orden_data[5] else 'N/E'} años")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, height - 140, "Sexo:")
    c.setFont("Helvetica", 10)
    c.drawString(120, height - 140, f"{orden_data[6] if orden_data[6] else 'N/E'}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(300, height - 140, "Doc. Identidad:")
    c.setFont("Helvetica", 10)
    c.drawString(390, height - 140, orden_data[2])
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, height - 160, "Examen:")
    c.setFont("Helvetica", 10)
    c.drawString(120, height - 160, orden_data[3])
    c.setFont("Helvetica-Bold", 10)
    c.drawString(300, height - 160, "Fecha Emisión:")
    c.setFont("Helvetica", 10)
    c.drawString(390, height - 160, orden_data[4])

    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(colors.HexColor("#1C9CD4"))
    c.drawString(width/2 - 80, height - 220, "INFORME DE RESULTADOS")
    
    if resultados_parametros:
        table_data = [["PARÁMETRO", "UNIDAD / RANGO", "RESULTADO"]]
        for rp in resultados_parametros:
            table_data.append([rp[0], f"{rp[1] if rp[1] else ''} {rp[2] if rp[2] else ''}", rp[3] if rp[3] else ""])
    else:
        table_data = [["No hay parámetros configurados para este examen."]]
    
    t = Table(table_data, colWidths=[200, 150, 200])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#1C9CD4")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 10),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    t.wrapOn(c, width, height)
    t.drawOn(c, 30, height - 250 - (len(table_data) * 20))
    
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(colors.grey)
    c.drawString(30, 30, pie_pagina)
    c.save()
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Resultado_Lab_Orden_{id_orden}.pdf", mimetype='application/pdf')

# ==========================================
# MÓDULO 4: ATENCIÓN MÉDICA
# ==========================================
@app.route('/atencion_medica', methods=['GET'])
def atencion_medica():
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    q = request.args.get('q', '').strip()
    conn = get_db_connection(); cursor = conn.cursor()
    sql = """
        SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, d.id as d_id, d.informe_pdf_path, p.historia_clinica, c.numero_boleta
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        LEFT JOIN diagnosticos d ON d.id_cita = c.id
        WHERE c.estado = 'Pagado'
    """
    params = []
    if q:
        sql += " AND (p.nombre LIKE ? OR p.apellido LIKE ? OR p.dni LIKE ? OR c.id LIKE ? OR p.historia_clinica LIKE ? OR c.numero_boleta LIKE ?)"
        params = [f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%']
    if session.get('rol') == 'medico':
        cursor.execute("SELECT id FROM usuarios WHERE usuario=?", (session.get('usuario'),))
        id_medico = cursor.fetchone()[0]
        sql += " AND c.id_medico = ?"
        params.append(id_medico)
    sql += " ORDER BY c.fecha_cita DESC"
    cursor.execute(sql, params)
    citas = cursor.fetchall()
    conn.close()
    
    contenido_med = """
    <h2>🩺 Atención Médica</h2>
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <form method="GET" style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
            <div style="flex:1; min-width:200px;">
                <label style="margin:0; font-size:13px;">🔎 Buscar (DNI, HC, Boleta, Apellido):</label>
                <input type="text" name="q" value="{{ q }}" style="margin:0; padding:8px;">
            </div>
            <div><button type="submit" class="btn btn-primary" style="padding:8px 20px;">Buscar</button></div>
            <div><a href="{{ url_for('atencion_medica') }}" class="btn btn-warning" style="padding:8px 20px;">Limpiar</a></div>
        </form>
    </div>

    <table><thead><tr><th>HC</th><th>Boleta</th><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Estado</th><th>Informe</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for c in citas %}
        <tr>
            <td>{{ c[8] }}</td>
            <td>{{ c[9] if c[9] else '--' }}</td>
            <td>{{ c[1] }} {{ c[2] }}</td>
            <td>{{ c[3] }}</td>
            <td>{{ c[4] }}</td>
            <td><span class="badge badge-pagado">{{ c[5] }}</span></td>
            <td>
                {% if c[6] %}
                    <a href="{{ url_for('ver_informe', id_cita=c[0]) }}" target="_blank" class="btn btn-primary" style="padding:2px 8px; font-size:11px;">📄 Ver</a>
                    <a href="{{ url_for('exportar_informe', id_cita=c[0]) }}" class="btn btn-success" style="padding:2px 8px; font-size:11px;">⬇️ Exportar</a>
                {% else %}
                    <span style="color:#999; font-size:11px;">Sin informe</span>
                {% endif %}
            </td>
            <td>
                {% if c[6] %}
                    <a href="{{ url_for('editar_informe', id_cita=c[0]) }}" class="btn btn-warning" style="padding:4px 10px; font-size:12px;">✏️ Editar</a>
                {% else %}
                    <a href="{{ url_for('atender_cita', id_cita=c[0]) }}" class="btn btn-primary" style="padding:4px 10px; font-size:12px;">Atender</a>
                {% endif %}
            </td>
        </tr>
        {% else %}<tr><td colspan="8" style="text-align:center;">No hay citas pagadas.</td></tr>{% endfor %}
    </tbody></table>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_med)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), citas=citas, q=q)

@app.route('/atencion_medica/atender/<int:id_cita>', methods=['GET', 'POST'])
def atender_cita(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    if request.method == 'POST':
        diagnostico = request.form['diagnostico']; tratamiento = request.form['tratamiento']; descanso = int(request.form['descanso'])
        cursor.execute("SELECT id FROM usuarios WHERE usuario=?", (session.get('usuario'),))
        id_medico = cursor.fetchone()[0]
        cursor.execute("INSERT INTO diagnosticos (id_cita, id_medico, diagnostico, tratamiento, descanso_medico_dias, informe_pdf_path) VALUES (?, ?, ?, ?, ?, ?)",
                       (id_cita, id_medico, diagnostico, tratamiento, descanso, ''))
        generar_y_guardar_informe_pdf(id_cita)
        conn.commit(); conn.close()
        return redirect(url_for('atencion_medica'))
    
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.dni, s.nombre, c.fecha_cita
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        WHERE c.id = ?
    """, (id_cita,))
    cita = cursor.fetchone()
    cursor.execute("""
        SELECT e.descripcion,
               CASE
                   WHEN EXISTS (SELECT 1 FROM resultados_lab rl WHERE rl.id_orden = o.id) THEN 'Con resultados'
                   ELSE 'Pendiente'
               END AS resultado,
               o.id, o.estado
        FROM ordenes_laboratorio o
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id_cita = ?
    """, (id_cita,))
    lab_results = cursor.fetchall()
    conn.close()
    imagenes_dict = {}
    for lab in lab_results:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, nombre_archivo, ruta_archivo FROM imagenes_laboratorio WHERE id_orden=?", (lab[2],))
        imagenes_dict[lab[2]] = cursor.fetchall()
        conn.close()

    contenido_atender = """
    <h2>Atender Cita #{{ id_cita }}</h2>
    <div style="background:#e9ecef; padding:15px; border-radius:8px; margin-bottom:15px;">
        <b>{{ cita[0] }} {{ cita[1] }}</b> (DNI: {{ cita[2] }}) <br>
        <b>Servicio:</b> {{ cita[3] }} <br>
        <b>Fecha:</b> {{ cita[4] }}
    </div>
    <h3>Resultados de Laboratorio</h3>
    <table><thead><tr><th>Examen</th><th>Resultado</th><th>PDF Adjunto</th></tr></thead>
    <tbody>
        {% for lab in lab_results %}
        <tr>
            <td>{{ lab[0] }}</td>
            <td>{{ lab[1] if lab[1] else 'Pendiente' }}</td>
            <td>
                {% for img in imagenes[lab[2]] %}
                    <a href="/static/{{ img.ruta }}" target="_blank">📄 Ver PDF</a><br>
                {% endfor %}
            </td>
        </tr>
        {% else %}<tr><td colspan="3">No hay exámenes asociados.</td></tr>{% endfor %}
    </tbody></table>
    
    <form method="POST" style="margin-top:20px;">
        <div class="adm-form-grid">
            <div><label>Diagnóstico</label><textarea name="diagnostico" rows="3" required></textarea></div>
            <div><label>Tratamiento / Receta</label><textarea name="tratamiento" rows="3" required></textarea></div>
            <div><label>Días de Descanso Médico</label><input type="number" name="descanso" value="0"></div>
        </div>
        <button type="submit" class="btn btn-success">Guardar y Generar Informe</button>
        <a href="{{ url_for('atencion_medica') }}" class="btn btn-danger">Cancelar</a>
    </form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_atender)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), id_cita=id_cita, cita=cita, lab_results=lab_results, imagenes=imagenes_dict)

def generar_y_guardar_informe_pdf(id_cita):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("""
            SELECT p.nombre, p.apellido, p.dni, s.nombre, u.usuario, c.fecha_cita
            FROM citas c
            JOIN pacientes p ON c.id_paciente = p.id
            JOIN servicios s ON c.id_servicio = s.id
            JOIN usuarios u ON c.id_medico = u.id
            WHERE c.id = ?
        """, (id_cita,))
        cita_data = cursor.fetchone()
        cursor.execute("SELECT diagnostico, tratamiento, descanso_medico_dias FROM diagnosticos WHERE id_cita = ?", (id_cita,))
        diag_data = cursor.fetchone()
        conn.close()
        if not cita_data or not diag_data: return

        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        logo_path = config[2] if config else ''
        report_header = config[5] if config else 'INFORME DE ATENCIÓN CLÍNICA'
        report_footer = config[6] if config else 'Documento generado por SISGALENO2026'
        page_size = obtener_tamano_pagina()
        
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=page_size, topMargin=30, bottomMargin=30)
        styles = getSampleStyleSheet()
        elements = []
        if logo_path and os.path.exists(os.path.join('static', logo_path)):
            try:
                img = ImageReader(os.path.join('static', logo_path))
                elements.append(Spacer(1, 10))
            except: pass
        title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], fontSize=14, alignment=1, spaceAfter=20)
        elements.append(Paragraph(f"<b>{nombre_sistema}</b>", title_style))
        elements.append(Paragraph(f"<i>{report_header}</i>", styles['Heading2']))
        elements.append(Spacer(1, 15))
        patient_info = f"""
        <b>Paciente:</b> {cita_data[0]} {cita_data[1]} <br/>
        <b>DNI:</b> {cita_data[2]} <br/>
        <b>Servicio:</b> {cita_data[3]} <br/>
        <b>Médico:</b> Dr. {cita_data[4]} <br/>
        <b>Fecha de Atención:</b> {cita_data[5]}
        """
        elements.append(Paragraph(patient_info, styles['Normal']))
        elements.append(Spacer(1, 15))
        elements.append(Paragraph("<b>Diagnóstico Clínico:</b>", styles['Normal']))
        elements.append(Paragraph(diag_data[0] if diag_data[0] else "No especificado", styles['Normal']))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("<b>Tratamiento / Receta:</b>", styles['Normal']))
        elements.append(Paragraph(diag_data[1] if diag_data[1] else "No especificado", styles['Normal']))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(f"<b>Descanso Médico:</b> {diag_data[2]} días", styles['Normal']))
        elements.append(Spacer(1, 30))
        elements.append(Paragraph(report_footer, styles['Italic']))
        
        doc.build(elements)
        
        upload_folder = "static/informes_medicos"
        if not os.path.exists(upload_folder): os.makedirs(upload_folder)
        filename = f"informe_{id_cita}.pdf"
        filepath = os.path.join(upload_folder, filename)
        with open(filepath, 'wb') as f:
            f.write(buffer.getvalue())
        
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE diagnosticos SET informe_pdf_path=? WHERE id_cita=?", (f"informes_medicos/{filename}", id_cita))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"Error generando informe PDF: {e}")

@app.route('/atencion_medica/editar_informe/<int:id_cita>', methods=['GET', 'POST'])
def editar_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    if request.method == 'POST':
        diagnostico = request.form['diagnostico']; tratamiento = request.form['tratamiento']; descanso = int(request.form['descanso'])
        cursor.execute("UPDATE diagnosticos SET diagnostico=?, tratamiento=?, descanso_medico_dias=? WHERE id_cita=?", 
                       (diagnostico, tratamiento, descanso, id_cita))
        conn.commit(); conn.close()
        generar_y_guardar_informe_pdf(id_cita)
        return redirect(url_for('atencion_medica'))
    
    cursor.execute("SELECT id_cita, diagnostico, tratamiento, descanso_medico_dias FROM diagnosticos WHERE id_cita=?", (id_cita,))
    datos = cursor.fetchone()
    if not datos: return "Informe no encontrado", 404
    conn.close()
    
    contenido_editar = f"""
    <h2>✏️ Editar Informe Médico - Cita #{id_cita}</h2>
    <form method="POST" style="margin-top:20px;">
        <div class="adm-form-grid">
            <div><label>Diagnóstico</label><textarea name="diagnostico" rows="3" required>{datos[1]}</textarea></div>
            <div><label>Tratamiento / Receta</label><textarea name="tratamiento" rows="3" required>{datos[2]}</textarea></div>
            <div><label>Días de Descanso Médico</label><input type="number" name="descanso" value="{datos[3]}"></div>
        </div>
        <button type="submit" class="btn btn-success">Guardar Cambios y Actualizar PDF</button>
        <a href="{{ url_for('atencion_medica') }}" class="btn btn-danger">Cancelar</a>
    </form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

@app.route('/atencion_medica/ver_informe/<int:id_cita>')
def ver_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT informe_pdf_path FROM diagnosticos WHERE id_cita=?", (id_cita,))
    res = cursor.fetchone()
    conn.close()
    if res and res[0]:
        return send_file(os.path.join('static', res[0]), mimetype='application/pdf')
    return "Informe no encontrado", 404

@app.route('/atencion_medica/exportar_informe/<int:id_cita>')
def exportar_informe(id_cita):
    if 'Atención Médica' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT informe_pdf_path FROM diagnosticos WHERE id_cita=?", (id_cita,))
    res = cursor.fetchone()
    conn.close()
    if res and res[0]:
        return send_file(os.path.join('static', res[0]), as_attachment=True, download_name=f"Informe_Medico_Cita_{id_cita}.pdf", mimetype='application/pdf')
    return "Informe no encontrado", 404

# ==========================================
# CONFIGURACIÓN AVANZADA (NUEVA LÓGICA DE ROLES DINÁMICOS)
# ==========================================
@app.route('/configuracion', methods=['GET', 'POST'])
def configuracion_sistema():
    if 'Configuración' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    tab = request.args.get('tab', 'general')
    mensaje = ""; tipo_mensaje = ""

    # --- PESTAÑA GENERAL ---
    if tab == 'general':
        if request.method == 'POST':
            nombre_sistema = request.form['nombre_sistema']; tamano_hoja = request.form['tamano_hoja']; encabezado = request.form['encabezado_texto']; pie_pagina = request.form['pie_pagina_texto']; report_header = request.form['report_header']; report_footer = request.form['report_footer']
            try:
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("""
                    UPDATE configuracion_sistema SET nombre_sistema=?, tamano_hoja=?, encabezado_texto=?, pie_pagina_texto=?, report_header=?, report_footer=? WHERE id=1
                """, (nombre_sistema, tamano_hoja, encabezado, pie_pagina, report_header, report_footer))
                conn.commit(); conn.close()
                mensaje = "Configuración general actualizada."; tipo_mensaje = "alert-success"
            except Exception as e:
                mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
        config = obtener_configuracion()
        contenido_config = """
        <h2>⚙️ Configuración Avanzada</h2>
        <div class="tabs">
            <a href="{{ url_for('configuracion_sistema', tab='general') }}" class="tab-btn {% if tab == 'general' %}active{% endif %}">General</a>
            <a href="{{ url_for('configuracion_sistema', tab='modulos') }}" class="tab-btn {% if tab == 'modulos' %}active{% endif %}">Módulos</a>
            <a href="{{ url_for('configuracion_sistema', tab='roles') }}" class="tab-btn {% if tab == 'roles' %}active{% endif %}">Roles</a>
            <a href="{{ url_for('configuracion_sistema', tab='personal') }}" class="tab-btn {% if tab == 'personal' %}active{% endif %}">Personal</a>
            <a href="{{ url_for('configuracion_sistema', tab='examenes') }}" class="tab-btn {% if tab == 'examenes' %}active{% endif %}">Exámenes</a>
        </div>
        <div class="tab-content active">
            {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
            <form method="POST">
                <div class="adm-form-grid">
                    <div><label>Nombre del Sistema</label><input type="text" name="nombre_sistema" value="{{ config[0] }}" required></div>
                    <div><label>Tamaño de Hoja (PDF)</label>
                        <select name="tamano_hoja">
                            <option value="A4" {% if config[1] == 'A4' %}selected{% endif %}>A4</option>
                            <option value="LETTER" {% if config[1] == 'LETTER' %}selected{% endif %}>Carta</option>
                            <option value="LEGAL" {% if config[1] == 'LEGAL' %}selected{% endif %}>Oficio</option>
                        </select>
                    </div>
                </div>
                <div class="adm-form-grid" style="margin-top:15px;">
                    <div><label>Texto Encabezado General</label><input type="text" name="encabezado_texto" value="{{ config[3] }}"></div>
                    <div><label>Texto Pie de Página General</label><input type="text" name="pie_pagina_texto" value="{{ config[4] }}"></div>
                </div>
                <div class="adm-form-grid" style="margin-top:15px;">
                    <div><label>Título Reporte</label><input type="text" name="report_header" value="{{ config[5] }}"></div>
                    <div><label>Pie Reporte</label><input type="text" name="report_footer" value="{{ config[6] }}"></div>
                </div>
                <div style="border-top: 1px solid #ddd; margin-top: 20px; padding-top: 20px;">
                    <h4>Logo</h4>
                    {% if config[2] %}<p>Logo actual: <b>{{ config[2] }}</b></p><img src="/static/{{ config[2] }}" style="max-height:80px;">{% else %}<p>Sin logo.</p>{% endif %}
                    <a href="{{ url_for('subir_logo') }}" class="btn btn-primary">Subir Logo</a>
                </div>
                <div style="margin-top:20px;">
                    <button type="submit" class="btn btn-success">Guardar</button>
                    <button type="reset" class="btn btn-danger">Reset</button>
                </div>
            </form>
        </div>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_config)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), config=config, tab=tab, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

    # --- PESTAÑA MÓDULOS ---
    elif tab == 'modulos':
        if request.method == 'POST':
            conn = get_db_connection(); cursor = conn.cursor()
            accion = request.form.get('accion')
            if accion == 'guardar_estado':
                cursor.execute("UPDATE config_modulos SET activo=0")
                seleccionados = request.form.getlist('modulos')
                for mod in seleccionados:
                    cursor.execute("UPDATE config_modulos SET activo=1 WHERE nombre=?", (mod,))
                mensaje = "Estado de módulos actualizado."; tipo_mensaje = "alert-success"
            elif accion == 'agregar_modulo':
                nuevo_mod = request.form['nuevo_modulo'].strip()
                desc = request.form['descripcion_modulo'].strip()
                if nuevo_mod:
                    try:
                        cursor.execute("INSERT INTO config_modulos (nombre, descripcion, activo) VALUES (?, ?, 1)", (nuevo_mod, desc))
                        mensaje = f"Módulo '{nuevo_mod}' creado exitosamente. ¡Aparecerá en el menú!"; tipo_mensaje = "alert-success"
                    except sqlite3.IntegrityError:
                        mensaje = "El nombre del módulo ya existe."; tipo_mensaje = "alert-danger"
            conn.commit(); conn.close()
        
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT nombre, activo, descripcion FROM config_modulos")
        modulos = cursor.fetchall()
        conn.close()
        contenido_modulos = """
        <h2>⚙️ Configuración Avanzada</h2>
        <div class="tabs">
            <a href="{{ url_for('configuracion_sistema', tab='general') }}" class="tab-btn">General</a>
            <a href="{{ url_for('configuracion_sistema', tab='modulos') }}" class="tab-btn active">Módulos</a>
            <a href="{{ url_for('configuracion_sistema', tab='roles') }}" class="tab-btn">Roles</a>
            <a href="{{ url_for('configuracion_sistema', tab='personal') }}" class="tab-btn">Personal</a>
            <a href="{{ url_for('configuracion_sistema', tab='examenes') }}" class="tab-btn">Exámenes</a>
        </div>
        <div class="tab-content active">
            {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
            <h4>Agregar Nuevo Módulo (RX, Otros servicios)</h4>
            <form method="POST" style="background:#f0f0f0; padding:15px; border-radius:8px; margin-bottom:20px;">
                <input type="hidden" name="accion" value="agregar_modulo">
                <div class="adm-form-grid">
                    <div><label>Nombre del Nuevo Módulo</label><input type="text" name="nuevo_modulo" placeholder="Ej: Rayos X" required></div>
                    <div><label>Descripción</label><input type="text" name="descripcion_modulo" placeholder="Descripción del servicio"></div>
                </div>
                <button type="submit" class="btn btn-success">Crear Módulo</button>
            </form>
            <h4>Activar / Desactivar Módulos</h4>
            <form method="POST">
                <input type="hidden" name="accion" value="guardar_estado">
                <div class="adm-form-grid">
                    {% for m in modulos %}
                    <div style="display:flex; align-items:center;">
                        <input type="checkbox" name="modulos" value="{{ m[0] }}" {% if m[1] == 1 %}checked{% endif %}>
                        <label style="margin-left:10px; font-weight:bold;">{{ m[0] }}</label>
                        <span style="margin-left:10px; font-size:12px; color:#666;">- {{ m[2] }}</span>
                    </div>
                    {% endfor %}
                </div>
                <button type="submit" class="btn btn-success">Guardar Estado de Módulos</button>
            </form>
        </div>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_modulos)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), modulos=modulos, tab=tab, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

    # --- PESTAÑA ROLES (NUEVA LÓGICA DE PERMISOS DINÁMICOS) ---
    elif tab == 'roles':
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT rol FROM usuarios")
        roles = [r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT nombre FROM config_modulos")
        modulos = [r[0] for r in cursor.fetchall()]
        
        if request.method == 'POST':
            rol_seleccionado = request.form['rol_seleccionado']
            # Limpiar permisos anteriores de ese rol
            cursor.execute("DELETE FROM permisos_roles WHERE rol=?", (rol_seleccionado,))
            # Insertar los nuevos permisos seleccionados
            seleccionados = request.form.getlist('permisos')
            for mod in seleccionados:
                cursor.execute("INSERT INTO permisos_roles (rol, modulo) VALUES (?, ?)", (rol_seleccionado, mod))
            conn.commit()
            conn.close()
            mensaje = f"Permisos actualizados para el rol '{rol_seleccionado}'."
            tipo_mensaje = "alert-success"
            # Recargar la página para mostrar los cambios
            return redirect(url_for('configuracion_sistema', tab='roles'))
        
        # Preparar datos para mostrar la tabla de roles con sus permisos
        permisos_actuales = {}
        cursor.execute("SELECT rol, modulo FROM permisos_roles")
        for r, m in cursor.fetchall():
            if r not in permisos_actuales:
                permisos_actuales[r] = []
            permisos_actuales[r].append(m)
        conn.close()
        
        contenido_roles = """
        <h2>⚙️ Configuración Avanzada</h2>
        <div class="tabs">
            <a href="{{ url_for('configuracion_sistema', tab='general') }}" class="tab-btn">General</a>
            <a href="{{ url_for('configuracion_sistema', tab='modulos') }}" class="tab-btn">Módulos</a>
            <a href="{{ url_for('configuracion_sistema', tab='roles') }}" class="tab-btn active">Roles</a>
            <a href="{{ url_for('configuracion_sistema', tab='personal') }}" class="tab-btn">Personal</a>
            <a href="{{ url_for('configuracion_sistema', tab='examenes') }}" class="tab-btn">Exámenes</a>
        </div>
        <div class="tab-content active">
            {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
            <p>Selecciona un rol y marca las casillas para asignar o quitar el acceso a los módulos del sistema.</p>
            
            <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
                <form method="POST" id="form_roles">
                    <div class="adm-form-grid">
                        <div><label>Seleccionar Rol</label>
                            <select name="rol_seleccionado" id="select_rol" onchange="this.form.submit()" required>
                                <option value="">Elija un rol...</option>
                                {% for r in roles %}
                                <option value="{{ r }}">{{ r }}</option>
                                {% endfor %}
                            </select>
                        </div>
                    </div>
                </form>
                
                <div id="container_permisos" style="display:none; margin-top:15px; border-top:1px solid #ddd; padding-top:15px;">
                    <h5 style="color:#0d2b45;">Permisos del rol seleccionado:</h5>
                    <form method="POST" id="form_permisos">
                        <input type="hidden" name="rol_seleccionado" id="hidden_rol">
                        <div class="adm-form-grid">
                            {% for m in modulos %}
                            <div style="display:flex; align-items:center;">
                                <input type="checkbox" name="permisos" value="{{ m }}" id="mod_{{ m }}">
                                <label for="mod_{{ m }}" style="margin-left:10px; font-weight:bold;">{{ m }}</label>
                            </div>
                            {% endfor %}
                        </div>
                        <button type="submit" class="btn btn-success">Guardar Permisos</button>
                    </form>
                </div>
            </div>
            
            <h4>Roles actualmente registrados y sus módulos:</h4>
            <table>
                <thead><tr><th>Rol</th><th>Módulos a los que tiene acceso</th></tr></thead>
                <tbody>
                    {% for r in roles %}
                    <tr>
                        <td><b>{{ r }}</b></td>
                        <td>
                            {% if r in permisos_actuales %}
                                {{ permisos_actuales[r] | join(', ') }}
                            {% else %}
                                <span style="color:#999;">Ninguno</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% else %}
                    <tr><td colspan="2">No hay roles registrados.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
            
            <script>
                document.addEventListener('DOMContentLoaded', function() {
                    var select = document.getElementById('select_rol');
                    var container = document.getElementById('container_permisos');
                    var hiddenRol = document.getElementById('hidden_rol');
                    var permisosData = {{ permisos_actuales | tojson }};
                    
                    select.addEventListener('change', function() {
                        var rol = this.value;
                        if(rol) {
                            container.style.display = 'block';
                            hiddenRol.value = rol;
                            // Desmarcar todos los checkboxes
                            var checks = document.querySelectorAll('#form_permisos input[type="checkbox"]');
                            checks.forEach(function(chk) { chk.checked = false; });
                            // Marcar los que tiene el rol
                            if(rol in permisosData) {
                                permisosData[rol].forEach(function(mod) {
                                    var chk = document.getElementById('mod_' + mod);
                                    if(chk) chk.checked = true;
                                });
                            }
                        } else {
                            container.style.display = 'none';
                        }
                    });
                });
            </script>
        </div>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_roles)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), roles=roles, modulos=modulos, permisos_actuales=permisos_actuales, tab=tab, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

    # --- PESTAÑA PERSONAL ---
    elif tab == 'personal':
        if request.method == 'POST':
            accion = request.form.get('accion')
            conn = get_db_connection(); cursor = conn.cursor()
            if accion == 'crear_personal':
                nombre_usuario = request.form['nombre_usuario']
                password = request.form['password']
                rol = request.form['rol']
                especialidad = request.form['especialidad']
                horario = request.form['horario']
                try:
                    cursor.execute("INSERT INTO usuarios (usuario, password, rol) VALUES (?, ?, ?)", (nombre_usuario, password, rol))
                    id_usuario = cursor.lastrowid
                    if rol == 'medico':
                        cursor.execute("INSERT INTO medicos (id_usuario, especialidad, horario) VALUES (?, ?, ?)", (id_usuario, especialidad, horario))
                    mensaje = f"Usuario {nombre_usuario} creado y asignado correctamente."; tipo_mensaje = "alert-success"
                except sqlite3.IntegrityError:
                    mensaje = "El nombre de usuario ya existe."; tipo_mensaje = "alert-danger"
                except Exception as e:
                    mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
            conn.commit(); conn.close()
        
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT m.id, u.usuario, m.especialidad, m.horario FROM medicos m JOIN usuarios u ON m.id_usuario = u.id")
        lista_medicos = cursor.fetchall()
        conn.close()
        contenido_personal = """
        <h2>⚙️ Configuración Avanzada</h2>
        <div class="tabs">
            <a href="{{ url_for('configuracion_sistema', tab='general') }}" class="tab-btn">General</a>
            <a href="{{ url_for('configuracion_sistema', tab='modulos') }}" class="tab-btn">Módulos</a>
            <a href="{{ url_for('configuracion_sistema', tab='roles') }}" class="tab-btn">Roles</a>
            <a href="{{ url_for('configuracion_sistema', tab='personal') }}" class="tab-btn active">Personal</a>
            <a href="{{ url_for('configuracion_sistema', tab='examenes') }}" class="tab-btn">Exámenes</a>
        </div>
        <div class="tab-content active">
            {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
            <h4>Crear Nuevo Personal (Médico, Tecnólogo, Técnico)</h4>
            <form method="POST" style="background:#f0f0f0; padding:15px; border-radius:8px; margin-bottom:20px;">
                <input type="hidden" name="accion" value="crear_personal">
                <div class="adm-form-grid">
                    <div><label>Usuario</label><input type="text" name="nombre_usuario" required></div>
                    <div><label>Contraseña</label><input type="password" name="password" required></div>
                    <div><label>Rol</label>
                        <select name="rol" required>
                            <option value="medico">Médico</option>
                            <option value="tecnologo">Tecnólogo de Lab</option>
                            <option value="laboratorista">Técnico de Lab</option>
                            <option value="enfermera">Enfermera</option>
                            <option value="cajero">Cajero</option>
                            <option value="otro">Otro (Escriba abajo)</option>
                        </select>
                        <input type="text" name="rol_custom" placeholder="Escriba el nuevo rol..." style="margin-top:5px;">
                    </div>
                    <div><label>Especialidad</label><input type="text" name="especialidad" placeholder="Ej: Medicina General" required></div>
                    <div><label>Horario (Opcional)</label><input type="text" name="horario" placeholder="Ej: Lun-Vie 8am-5pm"></div>
                </div>
                <button type="submit" class="btn btn-success">Crear y Asignar</button>
            </form>

            <h4 style="margin-top:20px;">Lista de Personal Médico Asignado</h4>
            <table><thead><tr><th>Usuario</th><th>Especialidad</th><th>Horario</th></tr></thead>
            <tbody>
                {% for m in lista_medicos %}
                <tr><td>{{ m[1] }}</td><td>{{ m[2] }}</td><td>{{ m[3] }}</td></tr>
                {% else %}<tr><td colspan="3">Aún no hay personal registrado en el sistema.</td></tr>{% endfor %}
            </tbody></table>
        </div>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_personal)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), lista_medicos=lista_medicos, tab=tab, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

    # --- PESTAÑA EXÁMENES ---
    elif tab == 'examenes':
        if request.method == 'POST':
            accion = request.form.get('accion')
            conn = get_db_connection(); cursor = conn.cursor()
            if accion == 'crear_examen':
                codigo = request.form['codigo']
                descripcion = request.form['descripcion']
                precio = float(request.form['precio'])
                try:
                    cursor.execute("INSERT INTO examenes_catalogo (codigo, descripcion, precio) VALUES (?, ?, ?)", (codigo, descripcion, precio))
                    id_examen = cursor.lastrowid
                    nombres = request.form.getlist('param_nombre[]')
                    unidades = request.form.getlist('param_unidad[]')
                    rangos = request.form.getlist('param_rango[]')
                    for i in range(len(nombres)):
                        if nombres[i].strip():
                            cursor.execute("INSERT INTO examenes_parametros (id_examen_catalogo, nombre_parametro, unidad, rango_referencia, orden) VALUES (?, ?, ?, ?, ?)",
                                           (id_examen, nombres[i].strip(), unidades[i].strip(), rangos[i].strip(), i+1))
                    mensaje = f"Examen {descripcion} creado con sus parámetros."; tipo_mensaje = "alert-success"
                except Exception as e:
                    mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
            conn.commit(); conn.close()
        
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, codigo, descripcion, precio FROM examenes_catalogo ORDER BY id DESC")
        examenes = cursor.fetchall()
        conn.close()
        contenido_examenes = """
        <h2>⚙️ Configuración Avanzada</h2>
        <div class="tabs">
            <a href="{{ url_for('configuracion_sistema', tab='general') }}" class="tab-btn">General</a>
            <a href="{{ url_for('configuracion_sistema', tab='modulos') }}" class="tab-btn">Módulos</a>
            <a href="{{ url_for('configuracion_sistema', tab='roles') }}" class="tab-btn">Roles</a>
            <a href="{{ url_for('configuracion_sistema', tab='personal') }}" class="tab-btn">Personal</a>
            <a href="{{ url_for('configuracion_sistema', tab='examenes') }}" class="tab-btn active">Exámenes</a>
        </div>
        <div class="tab-content active">
            {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
            <h4>Agregar Nuevo Examen (Con Parámetros)</h4>
            <form method="POST" style="background:#f0f0f0; padding:15px; border-radius:8px; margin-bottom:20px;">
                <input type="hidden" name="accion" value="crear_examen">
                <div class="adm-form-grid">
                    <div><label>Código</label><input type="text" name="codigo" required></div>
                    <div><label>Descripción</label><input type="text" name="descripcion" required></div>
                    <div><label>Precio (S/.)</label><input type="number" step="0.01" name="precio" required></div>
                </div>
                <h5 style="margin-top:10px; color:#0d2b45;">Sub-campos / Parámetros del Examen</h5>
                <div id="params_container">
                    <div class="adm-form-grid" style="margin-bottom:10px;">
                        <div><label>Parámetro</label><input type="text" name="param_nombre[]" required placeholder="Glóbulos Blancos"></div>
                        <div><label>Unidad</label><input type="text" name="param_unidad[]" placeholder="x mmc"></div>
                        <div><label>Rango de Referencia</label><input type="text" name="param_rango[]" placeholder="4.0 - 10.0"></div>
                    </div>
                </div>
                <button type="button" onclick="agregarParam()" class="btn btn-warning" style="margin-bottom:15px;">+ Agregar Parámetro</button>
                <div><button type="submit" class="btn btn-success">Guardar Examen</button></div>
            </form>

            <h4 style="margin-top:20px;">Catálogo de Exámenes y Parámetros</h4>
            <table><thead><tr><th>Código</th><th>Descripción</th><th>Precio</th></tr></thead>
            <tbody>
                {% for e in examenes %}
                <tr><td>{{ e[1] }}</td><td><b>{{ e[2] }}</b></td><td>S/ {{ e[3] }}</td></tr>
                {% else %}<tr><td colspan="3">No hay exámenes configurados.</td></tr>{% endfor %}
            </tbody></table>
            
            <script>
                function agregarParam() {
                    var container = document.getElementById("params_container");
                    var newRow = document.createElement("div");
                    newRow.className = "adm-form-grid";
                    newRow.style.marginTop = "10px";
                    newRow.innerHTML = `
                        <div><label>Parámetro</label><input type="text" name="param_nombre[]" required placeholder="Ej: Hematocrito"></div>
                        <div><label>Unidad</label><input type="text" name="param_unidad[]" placeholder="Ej: %"></div>
                        <div><label>Rango de Referencia</label><input type="text" name="param_rango[]" placeholder="Ej: 40 - 54%"></div>
                    `;
                    container.appendChild(newRow);
                }
            </script>
        </div>
        """
        config = obtener_configuracion()
        nombre_sistema = config[0] if config else 'SISGALENO2026'
        base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_examenes)
        return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')), examenes=examenes, tab=tab, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

    return redirect(url_for('dashboard'))

@app.route('/configuracion/subir_logo', methods=['GET', 'POST'])
def subir_logo():
    if 'Configuración' not in get_user_modules(session.get('rol')): return redirect(url_for('dashboard'))
    if request.method == 'POST':
        file = request.files['logo_archivo']
        if file and file.filename:
            filename = secure_filename(file.filename)
            if not os.path.exists('static'): os.makedirs('static')
            for f in os.listdir('static'):
                if f.endswith(('.png', '.jpg', '.jpeg', '.gif')): os.remove(os.path.join('static', f))
            file.save(os.path.join('static', filename))
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("UPDATE configuracion_sistema SET logo_path=? WHERE id=1", (filename,))
            conn.commit(); conn.close()
            return redirect(url_for('configuracion_sistema'))
    contenido_logo = """
    <h2>Subir Nuevo Logo</h2>
    <form method="POST" enctype="multipart/form-data">
        <label>Seleccionar Imagen</label><input type="file" name="logo_archivo" accept="image/png, image/jpeg" required>
        <button type="submit" class="btn btn-success">Subir</button>
        <a href="{{ url_for('configuracion_sistema') }}" class="btn btn-danger">Cancelar</a>
    </form>
    """
    config = obtener_configuracion()
    nombre_sistema = config[0] if config else 'SISGALENO2026'
    base = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_logo)
    return render_template_string(base, nombre_sistema=nombre_sistema, user_modules=get_user_modules(session.get('rol')))

# ==========================================
# EJECUCIÓN
# ==========================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
