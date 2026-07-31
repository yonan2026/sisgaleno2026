import os
import sqlite3
import psycopg2
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, session, redirect, url_for, send_file
from datetime import datetime
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

def get_db_connection():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect('sisgaleno2026.db')

# ==========================================
# BASE DE DATOS (4 MÓDULOS + CONFIG DE REPORTE)
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
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('admin', 'admin', 'administrador') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('doctor', 'doctor', 'medico') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('lab', 'lab', 'laboratorista') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('nurse', 'nurse', 'enfermera') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('tecnologo', 'tecnologo', 'tecnologo') {conflict}")

    # --- MÓDULO 1: ADMISIÓN ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pacientes ( id {auto_inc}, dni TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL, apellido TEXT NOT NULL, fecha_nacimiento TEXT, telefono TEXT, direccion TEXT, sexo TEXT DEFAULT '', edad INTEGER DEFAULT 0 )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS servicios (
        id {auto_inc}, nombre TEXT NOT NULL, precio_base REAL DEFAULT 0
    )''')
    for svc in [('MEDICINA GENERAL', 50.0), ('MEDICINA INTERNA', 60.0), ('MEDICINA FISICA', 40.0), ('PEDIATRIA', 35.0), ('GINECOLOGIA', 70.0), ('TRAUMATOLOGIA', 65.0), ('CIRUGIA', 100.0), ('OTROS', 50.0)]:
        if IS_POSTGRES: cursor.execute("INSERT INTO servicios (nombre, precio_base) VALUES (%s, %s) ON CONFLICT (nombre) DO NOTHING", svc)
        else: cursor.execute("INSERT OR IGNORE INTO servicios (nombre, precio_base) VALUES (?, ?)", svc)

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS citas (
        id {auto_inc}, id_paciente INTEGER, id_servicio INTEGER, id_medico INTEGER, fecha_cita TEXT, estado TEXT, 
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id), FOREIGN KEY(id_servicio) REFERENCES servicios(id)
    )''')

    # --- MÓDULO 2: CAJA ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pagos (
        id {auto_inc}, id_cita INTEGER, monto REAL, fecha_pago TEXT, estado TEXT,
        FOREIGN KEY(id_cita) REFERENCES citas(id)
    )''')

    # --- MÓDULO 3: LABORATORIO ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_catalogo ( id {auto_inc}, codigo TEXT, descripcion TEXT NOT NULL, precio REAL DEFAULT 0, valor_normal TEXT )''')
    cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, codigo, descripcion, precio, valor_normal) VALUES (1, '145', 'HEMOGRAMA COMPLETO', 50.00, 'Valores de referencia según edad')")
    cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, codigo, descripcion, precio, valor_normal) VALUES (2, 'G002', 'GLUCOSA EN AYUNAS', 20.00, '70 - 100 mg/dL')")
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS ordenes_laboratorio (
        id {auto_inc}, id_paciente INTEGER, id_examen INTEGER, id_cita INTEGER, fecha_emision TEXT, estado TEXT, resultado TEXT, precio REAL,
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id), FOREIGN KEY(id_examen) REFERENCES examenes_catalogo(id), FOREIGN KEY(id_cita) REFERENCES citas(id)
    )''')

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS imagenes_laboratorio (
        id {auto_inc}, id_orden INTEGER, nombre_archivo TEXT, ruta_archivo TEXT,
        FOREIGN KEY(id_orden) REFERENCES ordenes_laboratorio(id)
    )''')

    # --- MÓDULO 4: ATENCIÓN MÉDICA ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS diagnosticos (
        id {auto_inc}, id_cita INTEGER, id_medico INTEGER, diagnostico TEXT, tratamiento TEXT, descanso_medico_dias INTEGER,
        FOREIGN KEY(id_cita) REFERENCES citas(id)
    )''')
    
    # --- CONFIGURACIÓN AVANZADA (FORMATO DE IMPRESIÓN) ---
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS configuracion_sistema (
        id INTEGER PRIMARY KEY DEFAULT 1,
        nombre_sistema TEXT DEFAULT 'SISGALENO2026',
        tamano_hoja TEXT DEFAULT 'A4',
        logo_path TEXT DEFAULT '',
        encabezado_texto TEXT DEFAULT 'Laboratorio Clínico',
        pie_pagina_texto TEXT DEFAULT 'Generado automáticamente por el sistema.',
        report_header TEXT DEFAULT 'INFORME DE ATENCIÓN CLÍNICA',
        report_footer TEXT DEFAULT 'Documento generado por SISGALENO2026'
    )''')
    cursor.execute("INSERT OR IGNORE INTO configuracion_sistema (id) VALUES (1)")
    
    conn.commit()
    conn.close()

init_db()

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

# ==========================================
# DISEÑO Y ESTILOS (NUEVO MENÚ 4 MÓDULOS)
# ==========================================
LAYOUT_BASE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SISGALENO2026 - Clínica</title>
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
        .badge-atendido { background: #17a2b8; color: white; }
        .menu-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-top: 20px; }
        .menu-item { background: #e9ecef; padding: 20px; text-align: center; border-radius: 12px; color: #333; font-weight: bold; }
        .menu-item:hover { background: #0d2b45; color: white; }
        .adm-form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        @media (max-width: 600px) { .adm-form-grid { grid-template-columns: 1fr; } .navbar { flex-direction: column; } }
        .attribution { text-align: center; font-size: 12px; color: #666; padding: 5px 0; }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">🏥 <span>SISGALENO</span>2026</div>
        <div>
            {% if session.get('usuario') %}
                <span>👤 {{ session['usuario'] }}</span>
                <a href="{{ url_for('dashboard') }}">Inicio</a>
                {% if session['rol'] in ['administrador', 'medico', 'enfermera'] %}
                    <a href="{{ url_for('admision') }}">📋 Admisión</a>
                {% endif %}
                {% if session['rol'] in ['administrador', 'cajero', 'medico'] %}
                    <a href="{{ url_for('caja') }}">💰 Caja</a>
                {% endif %}
                {% if session['rol'] in ['administrador', 'laboratorista', 'tecnologo'] %}
                    <a href="{{ url_for('laboratorio') }}">🧪 Laboratorio</a>
                {% endif %}
                {% if session['rol'] in ['administrador', 'medico'] %}
                    <a href="{{ url_for('atencion_medica') }}">🩺 Atención Médica</a>
                {% endif %}
                {% if session['rol'] == 'administrador' %}
                    <a href="{{ url_for('configuracion_sistema') }}" class="btn btn-warning" style="padding:5px 15px;">⚙️ Config</a>
                {% endif %}
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
    if request.method == 'POST':
        user, pwd = request.form['usuario'], request.form['password']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT rol FROM usuarios WHERE usuario=? AND password=?", (user, pwd))
        data = cursor.fetchone(); conn.close()
        if data:
            session['usuario'] = user; session['rol'] = data[0]
            return redirect(url_for('dashboard'))
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', """
    <h2 style="text-align:center;">Inicio de Sesión</h2>
    <form method="POST" style="max-width:400px; margin:auto;">
        <label>Usuario</label><input type="text" name="usuario" required>
        <label>Contraseña</label><input type="password" name="password" required>
        <button type="submit" class="btn btn-primary" style="width:100%;">Acceder</button>
    </form>
    """))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    contenido = """<div style="background:#0d2b45; color:white; padding:30px; border-radius:12px; text-align:center;">
        <h2>🏥 Bienvenido, {{ session['usuario'] }}</h2><p>Clínica SISGALENO2026</p>
    </div><div class="menu-grid">
        <a href="{{ url_for('admision') }}" class="menu-item">📋 Admisión</a>
        <a href="{{ url_for('caja') }}" class="menu-item">💰 Caja</a>
        <a href="{{ url_for('laboratorio') }}" class="menu-item">🧪 Laboratorio</a>
        <a href="{{ url_for('atencion_medica') }}" class="menu-item">🩺 Atención Médica</a>
    </div>"""
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido))

# ==========================================
# MÓDULO 1: ADMISIÓN
# ==========================================
@app.route('/admision', methods=['GET'])
def admision():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, apellido, dni FROM pacientes ORDER BY id DESC")
    pacientes = cursor.fetchall()
    cursor.execute("SELECT id, nombre FROM servicios")
    servicios = cursor.fetchall()
    cursor.execute("SELECT id, usuario FROM usuarios WHERE rol='medico'")
    medicos = cursor.fetchall()
    conn.close()
    
    contenido = """
    <h2>📋 Módulo de Admisión</h2>
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <button onclick="toggleForm()" class="btn btn-success">+ Nueva Cita</button>
        <div id="form_cita" style="display:none; margin-top:15px;">
            <form method="POST" action="{{ url_for('crear_cita') }}">
                <div class="adm-form-grid">
                    <div><label>Paciente</label>
                        <select name="id_paciente" required>
                            <option value="">Seleccione Paciente</option>
                            {% for p in pacientes %}<option value="{{ p[0] }}">{{ p[1] }} {{ p[2] }} ({{ p[3] }})</option>{% endfor %}
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
                    <div><label>Fecha de Cita</label><input type="datetime-local" name="fecha_cita" required></div>
                </div>
                <button type="submit" class="btn btn-primary">Agendar Cita</button>
            </form>
        </div>
    </div>
    <h3>Pacientes Registrados</h3>
    <table><thead><tr><th>ID</th><th>DNI</th><th>Nombre</th><th>Apellido</th></tr></thead>
    <tbody>{% for p in pacientes %}<tr><td>{{ p[0] }}</td><td>{{ p[3] }}</td><td>{{ p[1] }}</td><td>{{ p[2] }}</td></tr>{% endfor %}</tbody></table>
    <script>function toggleForm(){ var x=document.getElementById('form_cita'); x.style.display = x.style.display==='none'?'block':'none'; }</script>
    """
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido), pacientes=pacientes, servicios=servicios, medicos=medicos)

@app.route('/admision/crear_cita', methods=['POST'])
def crear_cita():
    if 'usuario' not in session: return redirect(url_for('login'))
    id_paciente = request.form['id_paciente']; id_servicio = request.form['id_servicio']; id_medico = request.form['id_medico']; fecha = request.form['fecha_cita']
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO citas (id_paciente, id_servicio, id_medico, fecha_cita, estado) VALUES (?, ?, ?, ?, 'Pendiente')",
                   (id_paciente, id_servicio, id_medico, fecha))
    conn.commit(); conn.close()
    return redirect(url_for('admision'))

# ==========================================
# MÓDULO 2: CAJA
# ==========================================
@app.route('/caja', methods=['GET'])
def caja():
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado, s.precio_base
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        WHERE c.estado = 'Pendiente'
        ORDER BY c.fecha_cita ASC
    """)
    pendientes = cursor.fetchall()
    conn.close()
    
    contenido = """
    <h2>💰 Módulo de Caja (Cobros Pendientes)</h2>
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
                <a href="{{ url_for('realizar_pago', id_cita=c[0]) }}" class="btn btn-success" style="padding:4px 10px; font-size:12px;">Cobrar</a>
            </td>
        </tr>
        {% else %}<tr><td colspan="6" style="text-align:center;">No hay citas pendientes de cobro.</td></tr>{% endfor %}
    </tbody></table>
    """
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido), pendientes=pendientes)

@app.route('/caja/pagar/<int:id_cita>', methods=['GET'])
def realizar_pago(id_cita):
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT s.precio_base FROM citas c JOIN servicios s ON c.id_servicio = s.id WHERE c.id=?", (id_cita,))
    monto = cursor.fetchone()[0]
    cursor.execute("INSERT INTO pagos (id_cita, monto, fecha_pago, estado) VALUES (?, ?, ?, 'Pagado')", (id_cita, monto, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    cursor.execute("UPDATE citas SET estado='Pagado' WHERE id=?", (id_cita,))
    conn.commit(); conn.close()
    return redirect(url_for('caja'))

# ==========================================
# MÓDULO 3: LABORATORIO (Exámenes y PDFs)
# ==========================================
@app.route('/laboratorio', methods=['GET'])
def laboratorio():
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""
        SELECT o.id, p.nombre, p.apellido, e.descripcion, o.estado, o.id_cita, o.resultado
        FROM ordenes_laboratorio o
        JOIN pacientes p ON o.id_paciente = p.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        ORDER BY o.id DESC
    """)
    ordenes = cursor.fetchall()
    conn.close()
    
    contenido = """
    <h2>🧪 Módulo de Laboratorio</h2>
    <div style="background:#f8f9fa; padding:15px; border-radius:12px; margin-bottom:20px;">
        <button onclick="toggleForm()" class="btn btn-success">+ Nueva Orden de Examen</button>
        <div id="form_orden" style="display:none; margin-top:15px;">
            <form method="POST" action="{{ url_for('crear_orden') }}">
                <div class="adm-form-grid">
                    <div><label>Paciente</label>
                        <select name="id_paciente" required>
                            <option value="">Seleccione</option>
                            {% for p in pacientes %}<option value="{{ p[0] }}">{{ p[1] }} {{ p[2] }}</option>{% endfor %}
                        </select>
                    </div>
                    <div><label>Examen</label>
                        <select name="id_examen" required>
                            <option value="">Seleccione</option>
                            {% for e in examenes %}<option value="{{ e[0] }}">{{ e[1] }} - S/ {{ e[2] }}</option>{% endfor %}
                        </select>
                    </div>
                    <div><label>Cita Asociada</label>
                        <select name="id_cita" required>
                            <option value="">Seleccione Cita Pagada</option>
                            {% for c in citas %}<option value="{{ c[0] }}">#{{ c[0] }} - {{ c[1] }}</option>{% endfor %}
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary">Crear Orden</button>
            </form>
        </div>
    </div>

    <h3>Órdenes de Laboratorio</h3>
    <table><thead><tr><th>ID</th><th>Paciente</th><th>Examen</th><th>Estado</th><th>Resultado</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for o in ordenes %}
        <tr>
            <td>#{{ o[0] }}</td>
            <td>{{ o[1] }} {{ o[2] }}</td>
            <td>{{ o[3] }}</td>
            <td><span class="badge badge-pendiente">{{ o[4] }}</span></td>
            <td>{{ o[6] if o[6] else 'Pendiente' }}</td>
            <td>
                <a href="{{ url_for('ingresar_resultado', id_orden=o[0]) }}" class="btn btn-primary" style="padding:4px 10px;">Ingresar</a>
                <a href="{{ url_for('subir_imagen', id_orden=o[0]) }}" class="btn btn-warning" style="padding:4px 10px;">📎 PDF</a>
            </td>
        </tr>
        {% endfor %}
    </tbody></table>
    <script>function toggleForm(){ var x=document.getElementById('form_orden'); x.style.display = x.style.display==='none'?'block':'none'; }</script>
    """
    # Cargar listas para el formulario
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, apellido FROM pacientes ORDER BY id DESC")
    pacientes = cursor.fetchall()
    cursor.execute("SELECT id, descripcion, precio FROM examenes_catalogo")
    examenes = cursor.fetchall()
    cursor.execute("SELECT c.id, p.nombre FROM citas c JOIN pacientes p ON c.id_paciente = p.id WHERE c.estado='Pagado'")
    citas = cursor.fetchall()
    conn.close()
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido), ordenes=ordenes, pacientes=pacientes, examenes=examenes, citas=citas)

@app.route('/laboratorio/crear_orden', methods=['POST'])
def crear_orden():
    if 'usuario' not in session: return redirect(url_for('login'))
    id_paciente, id_examen, id_cita = request.form['id_paciente'], request.form['id_examen'], request.form['id_cita']
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT precio FROM examenes_catalogo WHERE id=?", (id_examen,))
    precio = cursor.fetchone()[0]
    cursor.execute("INSERT INTO ordenes_laboratorio (id_paciente, id_examen, id_cita, fecha_emision, estado, resultado, precio) VALUES (?, ?, ?, ?, 'Pendiente', '', ?)",
                   (id_paciente, id_examen, id_cita, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), precio))
    conn.commit(); conn.close()
    return redirect(url_for('laboratorio'))

@app.route('/laboratorio/resultado/<int:id_orden>', methods=['GET', 'POST'])
def ingresar_resultado(id_orden):
    if 'usuario' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        resultado = request.form['resultado']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE ordenes_laboratorio SET resultado=?, estado='Completado' WHERE id=?", (resultado, id_orden))
        conn.commit(); conn.close()
        return redirect(url_for('laboratorio'))
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT o.id, p.nombre, p.apellido, e.descripcion FROM ordenes_laboratorio o JOIN pacientes p ON o.id_paciente = p.id JOIN examenes_catalogo e ON o.id_examen = e.id WHERE o.id=?", (id_orden,))
    orden = cursor.fetchone()
    conn.close()
    
    contenido = f"""
    <h2>Ingresar Resultado para: {orden[3]}</h2>
    <p><b>Paciente:</b> {orden[1]} {orden[2]}</p>
    <form method="POST">
        <label>Resultado</label><textarea name="resultado" rows="4" required></textarea>
        <button type="submit" class="btn btn-success">Guardar Resultado</button>
        <a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a>
    </form>
    """
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido))

@app.route('/laboratorio/subir_pdf/<int:id_orden>', methods=['GET', 'POST'])
def subir_imagen(id_orden):
    if 'usuario' not in session: return redirect(url_for('login'))
    mensaje = ""
    if request.method == 'POST':
        if 'archivo' not in request.files:
            mensaje = "No se seleccionó archivo."
        else:
            file = request.files['archivo']
            if file.filename and file.filename.endswith('.pdf'):
                filename = secure_filename(f"orden_{id_orden}_{file.filename}")
                upload_folder = "static/lab_imagenes"
                if not os.path.exists(upload_folder): os.makedirs(upload_folder)
                ruta = os.path.join(upload_folder, filename)
                file.save(ruta)
                
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("INSERT INTO imagenes_laboratorio (id_orden, nombre_archivo, ruta_archivo) VALUES (?, ?, ?)", 
                               (id_orden, file.filename, f"lab_imagenes/{filename}"))
                conn.commit(); conn.close()
                mensaje = "PDF subido exitosamente."
            else:
                mensaje = "Solo se permiten archivos PDF."
    
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, nombre_archivo, ruta_archivo FROM imagenes_laboratorio WHERE id_orden=?", (id_orden,))
    archivos = cursor.fetchall()
    conn.close()
    
    contenido = """
    <h2>Subir Informe (PDF) para Orden #{{ id_orden }}</h2>
    {% if mensaje %}<p style="background:#d4edda; padding:10px; border-radius:8px;">{{ mensaje }}</p>{% endif %}
    <form method="POST" enctype="multipart/form-data">
        <label>Seleccionar PDF (RX, Ecografía, Tomografía):</label>
        <input type="file" name="archivo" accept=".pdf" required>
        <button type="submit" class="btn btn-primary">Subir PDF</button>
        <a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a>
    </form>
    <h3>Archivos Subidos</h3>
    <ul>{% for a in archivos %}<li><a href="/static/{{ a[2] }}" target="_blank">📄 {{ a[1] }}</a></li>{% endfor %}</ul>
    """
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido), id_orden=id_orden, mensaje=mensaje, archivos=archivos)

# ==========================================
# MÓDULO 4: ATENCIÓN MÉDICA Y GENERACIÓN DE REPORTE
# ==========================================
@app.route('/atencion_medica', methods=['GET'])
def atencion_medica():
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    # Si es médico, mostrar sus citas. Si es admin, todas
    if session['rol'] == 'medico':
        cursor.execute("SELECT id FROM usuarios WHERE usuario=?", (session['usuario'],))
        id_medico = cursor.fetchone()[0]
        cursor.execute("""
            SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado
            FROM citas c
            JOIN pacientes p ON c.id_paciente = p.id
            JOIN servicios s ON c.id_servicio = s.id
            WHERE c.id_medico = ? AND c.estado = 'Pagado'
            ORDER BY c.fecha_cita DESC
        """, (id_medico,))
    else:
        cursor.execute("""
            SELECT c.id, p.nombre, p.apellido, s.nombre, c.fecha_cita, c.estado
            FROM citas c
            JOIN pacientes p ON c.id_paciente = p.id
            JOIN servicios s ON c.id_servicio = s.id
            WHERE c.estado = 'Pagado'
            ORDER BY c.fecha_cita DESC
        """)
    citas = cursor.fetchall()
    conn.close()
    
    contenido = """
    <h2>🩺 Atención Médica (Citas Pagadas)</h2>
    <table><thead><tr><th>Paciente</th><th>Servicio</th><th>Fecha</th><th>Estado</th><th>Acciones</th></tr></thead>
    <tbody>
        {% for c in citas %}
        <tr>
            <td>{{ c[1] }} {{ c[2] }}</td>
            <td>{{ c[3] }}</td>
            <td>{{ c[4] }}</td>
            <td><span class="badge badge-pagado">{{ c[5] }}</span></td>
            <td>
                <a href="{{ url_for('atender_cita', id_cita=c[0]) }}" class="btn btn-primary" style="padding:4px 10px;">Atender</a>
            </td>
        </tr>
        {% else %}<tr><td colspan="5">No hay citas pagadas pendientes.</td></tr>{% endfor %}
    </tbody></table>
    """
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido), citas=citas)

@app.route('/atencion_medica/atender/<int:id_cita>', methods=['GET', 'POST'])
def atender_cita(id_cita):
    if 'usuario' not in session: return redirect(url_for('login'))
    conn = get_db_connection(); cursor = conn.cursor()
    
    if request.method == 'POST':
        diagnostico = request.form['diagnostico']; tratamiento = request.form['tratamiento']; descanso = int(request.form['descanso'])
        cursor.execute("SELECT id FROM usuarios WHERE usuario=?", (session['usuario'],))
        id_medico = cursor.fetchone()[0]
        cursor.execute("INSERT INTO diagnosticos (id_cita, id_medico, diagnostico, tratamiento, descanso_medico_dias) VALUES (?, ?, ?, ?, ?)",
                       (id_cita, id_medico, diagnostico, tratamiento, descanso))
        cursor.execute("UPDATE citas SET estado='Atendido' WHERE id=?", (id_cita,))
        conn.commit(); conn.close()
        return redirect(url_for('atencion_medica'))
    
    # Mostrar datos del paciente, resultados de laboratorio e imágenes
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.dni, s.nombre, c.fecha_cita
        FROM citas c
        JOIN pacientes p ON c.id_paciente = p.id
        JOIN servicios s ON c.id_servicio = s.id
        WHERE c.id = ?
    """, (id_cita,))
    cita = cursor.fetchone()
    
    cursor.execute("""
        SELECT e.descripcion, o.resultado, o.id, o.estado
        FROM ordenes_laboratorio o
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id_cita = ?
    """, (id_cita,))
    lab_results = cursor.fetchall()
    conn.close()
    
    contenido = f"""
    <h2>Atender Cita #{id_cita}</h2>
    <div style="background:#e9ecef; padding:15px; border-radius:8px; margin-bottom:15px;">
        <b>{cita[0]} {cita[1]}</b> (DNI: {cita[2]}) <br>
        <b>Servicio:</b> {cita[3]} <br>
        <b>Fecha:</b> {cita[4]}
    </div>
    
    <h3>Resultados de Laboratorio / Imágenes</h3>
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
        {% else %}<tr><td colspan="3">No se solicitaron exámenes.</td></tr>{% endfor %}
    </tbody></table>
    
    <form method="POST" style="margin-top:20px;">
        <div class="adm-form-grid">
            <div><label>Diagnóstico</label><textarea name="diagnostico" rows="3" required></textarea></div>
            <div><label>Tratamiento / Receta</label><textarea name="tratamiento" rows="3" required></textarea></div>
            <div><label>Días de Descanso Médico</label><input type="number" name="descanso" value="0"></div>
        </div>
        <button type="submit" class="btn btn-success">Guardar Atención Médica</button>
        <a href="{{ url_for('atencion_medica') }}" class="btn btn-danger">Cancelar</a>
        <a href="{{ url_for('descargar_reporte_final', id_cita=id_cita) }}" target="_blank" class="btn btn-primary">🖨️ Imprimir Reporte</a>
    </form>
    """
    # Cargar imágenes asociadas a cada orden
    imagenes_dict = {}
    for lab in lab_results:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id, nombre_archivo, ruta_archivo FROM imagenes_laboratorio WHERE id_orden=?", (lab[2],))
        imagenes_dict[lab[2]] = cursor.fetchall()
        conn.close()

    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido), cita=cita, lab_results=lab_results, imagenes=imagenes_dict)

# ==========================================
# NUEVA RUTA: IMPRESIÓN DE REPORTE FINAL (FORMATO DE IMPRESIÓN PERSONALIZABLE)
# ==========================================
@app.route('/reporte/final/<int:id_cita>')
def descargar_reporte_final(id_cita):
    if 'usuario' not in session: return redirect(url_for('login'))
    
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
    
    cursor.execute("""
        SELECT diagnostico, tratamiento, descanso_medico_dias FROM diagnosticos WHERE id_cita = ?
    """, (id_cita,))
    diag_data = cursor.fetchone()
    
    cursor.execute("""
        SELECT e.descripcion, o.resultado
        FROM ordenes_laboratorio o
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id_cita = ?
    """, (id_cita,))
    lab_results = cursor.fetchall()
    conn.close()
    
    if not cita_data:
        return "No se encontró la cita.", 404

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
    
    # 1. Logo y Encabezado
    if logo_path and os.path.exists(os.path.join('static', logo_path)):
        try:
            img = ImageReader(os.path.join('static', logo_path))
            elements.append(Spacer(1, 10))
        except Exception as e:
            pass
    
    title_style = ParagraphStyle(name='Title', parent=styles['Heading1'], fontSize=14, alignment=1, spaceAfter=20)
    elements.append(Paragraph(f"<b>{nombre_sistema}</b>", title_style))
    elements.append(Paragraph(f"<i>{report_header}</i>", styles['Heading2']))
    elements.append(Spacer(1, 15))
    
    # 2. Datos del Paciente y Cita
    patient_info = f"""
    <b>Paciente:</b> {cita_data[0]} {cita_data[1]} <br/>
    <b>DNI:</b> {cita_data[2]} <br/>
    <b>Servicio:</b> {cita_data[3]} <br/>
    <b>Médico:</b> Dr. {cita_data[4]} <br/>
    <b>Fecha de Atención:</b> {cita_data[5]}
    """
    elements.append(Paragraph(patient_info, styles['Normal']))
    elements.append(Spacer(1, 15))
    
    # 3. Exámenes de Laboratorio (Tabla)
    if lab_results:
        elements.append(Paragraph("<b>Resultados de Laboratorio:</b>", styles['Normal']))
        table_data = [["Examen Solicitado", "Resultado"]]
        for lab in lab_results:
            table_data.append([lab[0], lab[1] if lab[1] else "Pendiente"])
        
        table = Table(table_data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(Spacer(1, 10))
        elements.append(table)
        elements.append(Spacer(1, 15))
    
    # 4. Diagnóstico y Tratamiento
    if diag_data:
        elements.append(Paragraph("<b>Diagnóstico Clínico:</b>", styles['Normal']))
        elements.append(Paragraph(diag_data[0] if diag_data[0] else "No especificado", styles['Normal']))
        elements.append(Spacer(1, 10))
        
        elements.append(Paragraph("<b>Tratamiento / Receta:</b>", styles['Normal']))
        elements.append(Paragraph(diag_data[1] if diag_data[1] else "No especificado", styles['Normal']))
        elements.append(Spacer(1, 10))
        
        elements.append(Paragraph(f"<b>Descanso Médico:</b> {diag_data[2]} días", styles['Normal']))
    else:
        elements.append(Paragraph("No se ha registrado diagnóstico médico.", styles['Normal']))
    
    # 5. Pie de Página
    elements.append(Spacer(1, 30))
    elements.append(Paragraph(report_footer, styles['Italic']))
    
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"Reporte_Clinico_Cita_{id_cita}.pdf", mimetype='application/pdf')

# ==========================================
# CONFIGURACIÓN DEL SISTEMA (FORMATO DE IMPRESIÓN)
# ==========================================
@app.route('/configuracion', methods=['GET', 'POST'])
def configuracion_sistema():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        nombre_sistema = request.form['nombre_sistema']
        tamano_hoja = request.form['tamano_hoja']
        encabezado = request.form['encabezado_texto']
        pie_pagina = request.form['pie_pagina_texto']
        report_header = request.form['report_header']
        report_footer = request.form['report_footer']
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("""
                UPDATE configuracion_sistema SET 
                nombre_sistema=?, tamano_hoja=?, encabezado_texto=?, pie_pagina_texto=?, report_header=?, report_footer=?
                WHERE id=1
            """, (nombre_sistema, tamano_hoja, encabezado, pie_pagina, report_header, report_footer))
            conn.commit(); conn.close()
            mensaje = "Configuración actualizada exitosamente."; tipo_mensaje = "alert-success"
        except Exception as e:
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"

    config = obtener_configuracion()
    
    contenido_config = """
    <h2>⚙️ Configuración del Sistema y Formato de Impresión</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <div class="adm-form-grid">
                <div><label>Nombre del Sistema</label><input type="text" name="nombre_sistema" value="{{ config[0] }}" required></div>
                <div><label>Tamaño de Hoja (PDF)</label>
                    <select name="tamano_hoja">
                        <option value="A4" {% if config[1] == 'A4' %}selected{% endif %}>A4</option>
                        <option value="LETTER" {% if config[1] == 'LETTER' %}selected{% endif %}>Carta (Letter)</option>
                        <option value="LEGAL" {% if config[1] == 'LEGAL' %}selected{% endif %}>Oficio (Legal)</option>
                    </select>
                </div>
            </div>
            <div class="adm-form-grid" style="margin-top:15px;">
                <div><label>Texto Encabezado General</label><input type="text" name="encabezado_texto" value="{{ config[3] }}"></div>
                <div><label>Texto Pie de Página General</label><input type="text" name="pie_pagina_texto" value="{{ config[4] }}"></div>
            </div>
            
            <h3 style="color:#0d2b45; margin-top:20px;">📄 Formato de Impresión (Reporte Clínico)</h3>
            <div class="adm-form-grid">
                <div><label>Título Principal del Reporte</label><input type="text" name="report_header" value="{{ config[5] }}"></div>
                <div><label>Texto de Pie de Página del Reporte</label><input type="text" name="report_footer" value="{{ config[6] }}"></div>
            </div>

            <div style="border-top: 1px solid #ddd; margin-top: 20px; padding-top: 20px;">
                <h4 style="color: #0d2b45;">Logo de la Institución</h4>
                {% if config[2] %}
                    <p>Logo actual: <b>{{ config[2] }}</b></p>
                    <img src="/static/{{ config[2] }}" style="max-height: 80px; border: 1px solid #ccc; border-radius: 4px;">
                {% else %}
                    <p>No hay logo configurado.</p>
                {% endif %}
                <div style="margin-top: 10px;">
                    <a href="{{ url_for('subir_logo') }}" class="btn btn-primary">Cambiar / Subir Logo</a>
                </div>
            </div>

            <div class="toolbar" style="margin-top:20px;">
                <button type="submit" class="btn btn-success">Guardar Configuración</button>
                <button type="reset" class="btn btn-danger">Resetear</button>
            </div>
        </form>
    </div>
    """
    html_conf = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_config)
    return render_template_string(html_conf, config=config, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/configuracion/subir_logo', methods=['GET', 'POST'])
def subir_logo():
    if 'usuario' not in session or session['rol'] != 'administrador': return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    
    if request.method == 'POST':
        if 'logo_archivo' not in request.files:
            mensaje = "No se seleccionó ningún archivo."
            tipo_mensaje = "alert-danger"
        else:
            file = request.files['logo_archivo']
            if file.filename == '':
                mensaje = "No se seleccionó ningún archivo."
                tipo_mensaje = "alert-danger"
            elif file:
                filename = secure_filename(file.filename)
                if not os.path.exists('static'):
                    os.makedirs('static')
                # Limpiar la carpeta static de logos antiguos
                for f in os.listdir('static'):
                    if f.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        os.remove(os.path.join('static', f))
                
                file.save(os.path.join('static', filename))
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("UPDATE configuracion_sistema SET logo_path=? WHERE id=1", (filename,))
                conn.commit(); conn.close()
                return redirect(url_for('configuracion_sistema'))

    contenido_logo = """
    <h2>Subir Nuevo Logo</h2>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST" enctype="multipart/form-data">
            <div class="adm-field">
                <label>Seleccionar Imagen (PNG, JPG)</label>
                <input type="file" name="logo_archivo" accept="image/png, image/jpeg" required>
            </div>
            <div class="toolbar">
                <button type="submit" class="btn btn-success">Subir Logo</button>
                <a href="{{ url_for('configuracion_sistema') }}" class="btn btn-danger">Cancelar</a>
            </div>
        </form>
    </div>
    """
    html_logo = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_logo)
    return render_template_string(html_logo, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

# ==========================================
# EJECUCIÓN
# ==========================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
