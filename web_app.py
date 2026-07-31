import os
import sqlite3
import psycopg2
from dotenv import load_dotenv
from flask import Flask, request, render_template_string, session, redirect, url_for, send_file
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
import io

# Cargar variables de entorno (para la base de datos de la nube)
load_dotenv()

app = Flask(__name__)
app.secret_key = 'clave_super_secreta_sisgaleno2026'

# Detectar si usamos la base de datos de la nube (PostgreSQL) o la local (SQLite)
DATABASE_URL = os.environ.get('DATABASE_URL')
IS_POSTGRES = DATABASE_URL is not None and DATABASE_URL.startswith('postgresql')

def get_db_connection():
    if IS_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    else:
        return sqlite3.connect('sisgaleno2026.db')

# ==========================================
# FUNCIONES DE BASE DE DATOS (Compatibles con PostgreSQL y SQLite)
# ==========================================
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Definir sintaxis según el motor de base de datos
    if IS_POSTGRES:
        auto_inc = "SERIAL"
        conflict = "ON CONFLICT (usuario) DO NOTHING"
    else:
        auto_inc = "INTEGER PRIMARY KEY AUTOINCREMENT"
        conflict = ""

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS usuarios (
        id {auto_inc},
        usuario TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        rol TEXT NOT NULL
    )''')
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('admin', 'admin', 'administrador') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('doctor', 'doctor', 'medico') {conflict}")
    cursor.execute(f"INSERT INTO usuarios (usuario, password, rol) VALUES ('lab', 'lab', 'laboratorista') {conflict}")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS pacientes (
        id {auto_inc},
        dni TEXT UNIQUE NOT NULL,
        nombre TEXT NOT NULL,
        apellido TEXT NOT NULL,
        fecha_nacimiento TEXT,
        telefono TEXT,
        direccion TEXT
    )''')
    
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS examenes_catalogo (
        id {auto_inc},
        nombre_examen TEXT NOT NULL
    )''')
    if IS_POSTGRES:
        cursor.execute("INSERT INTO examenes_catalogo (id, nombre_examen) VALUES (1, 'Hemograma Completo') ON CONFLICT (id) DO NOTHING")
        cursor.execute("INSERT INTO examenes_catalogo (id, nombre_examen) VALUES (2, 'Glucosa en Ayunas') ON CONFLICT (id) DO NOTHING")
        cursor.execute("INSERT INTO examenes_catalogo (id, nombre_examen) VALUES (3, 'Perfil Lipídico') ON CONFLICT (id) DO NOTHING")
        cursor.execute("INSERT INTO examenes_catalogo (id, nombre_examen) VALUES (4, 'Orina Completa') ON CONFLICT (id) DO NOTHING")
        cursor.execute("INSERT INTO examenes_catalogo (id, nombre_examen) VALUES (5, 'Colesterol Total') ON CONFLICT (id) DO NOTHING")
    else:
        cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, nombre_examen) VALUES (1, 'Hemograma Completo')")
        cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, nombre_examen) VALUES (2, 'Glucosa en Ayunas')")
        cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, nombre_examen) VALUES (3, 'Perfil Lipídico')")
        cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, nombre_examen) VALUES (4, 'Orina Completa')")
        cursor.execute("INSERT OR IGNORE INTO examenes_catalogo (id, nombre_examen) VALUES (5, 'Colesterol Total')")

    cursor.execute(f'''CREATE TABLE IF NOT EXISTS atenciones (
        id {auto_inc},
        id_paciente INTEGER,
        fecha_atencion TEXT,
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id)
    )''')
    cursor.execute(f'''CREATE TABLE IF NOT EXISTS ordenes_laboratorio (
        id {auto_inc},
        id_paciente INTEGER,
        id_examen INTEGER,
        id_atencion INTEGER,
        fecha_emision TEXT,
        estado TEXT,
        resultado TEXT,
        FOREIGN KEY(id_paciente) REFERENCES pacientes(id),
        FOREIGN KEY(id_examen) REFERENCES examenes_catalogo(id),
        FOREIGN KEY(id_atencion) REFERENCES atenciones(id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

# ==========================================
# DISEÑO Y ESTILOS VISUALES
# ==========================================
LAYOUT_BASE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SISGALENO2026 - Laboratorio Clínico</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background: #f4f7f6; color: #333; }
        a { text-decoration: none; }
        .navbar { background: linear-gradient(90deg, #0d2b45 0%, #1a4d70 100%); color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .navbar a { color: #f8f9fa; margin: 0 12px; font-weight: 500; transition: 0.3s; }
        .navbar a:hover { color: #72c6f7; transform: translateY(-1px); }
        .navbar .logo { font-size: 1.5rem; font-weight: bold; letter-spacing: 1px; color: white; }
        .navbar .logo span { color: #72c6f7; }
        .container { max-width: 1000px; margin: 30px auto; padding: 20px; background: #ffffff; border-radius: 16px; box-shadow: 0 8px 30px rgba(0, 0, 0, 0.08); }
        .btn { display: inline-block; padding: 10px 24px; margin: 4px; border: none; border-radius: 50px; font-weight: 600; color: white; text-align: center; cursor: pointer; transition: all 0.3s ease; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .btn:hover { transform: translateY(-3px); box-shadow: 0 8px 15px rgba(0,0,0,0.2); }
        .btn-primary { background: linear-gradient(135deg, #007bff, #0056b3); }
        .btn-success { background: linear-gradient(135deg, #28a745, #1e7e34); }
        .btn-danger { background: linear-gradient(135deg, #dc3545, #a71d2a); }
        .btn-warning { background: linear-gradient(135deg, #ffc107, #d39e00); color: #212529; }
        .btn-whatsapp { background: linear-gradient(135deg, #25d366, #128C7E); }
        input, select, textarea { width: 100%; padding: 12px; margin: 8px 0 16px 0; border: 1px solid #ced4da; border-radius: 8px; box-sizing: border-box; font-size: 16px; background: #fcfcfc; transition: 0.3s; }
        input:focus, textarea:focus { border-color: #007bff; outline: none; background: white; box-shadow: 0 0 0 3px rgba(0,123,255,0.1); }
        label { font-weight: 600; color: #495057; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; border-radius: 12px; overflow: hidden; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        th { background-color: #0d2b45; color: white; font-weight: 600; padding: 14px; text-align: left; }
        td { padding: 14px; background-color: #ffffff; border-bottom: 1px solid #eee; }
        tr:last-child td { border-bottom: none; }
        .estado-verde td { background-color: #eaf6ed !important; }
        .estado-rojo td { background-color: #fdecea !important; }
        .estado-amarillo td { background-color: #fff8e1 !important; }
        .badge { display: inline-block; padding: 5px 12px; border-radius: 50px; font-size: 12px; font-weight: bold; }
        .badge-verde { background: #28a745; color: white; }
        .badge-rojo { background: #dc3545; color: white; }
        .badge-amarillo { background: #ffc107; color: #212529; }

        .dashboard-banner {
            background-image: url('/static/fondo_lab.jpg');
            background-size: cover;
            background-position: center;
            margin: -20px -20px 20px -20px;
            padding: 40px 30px;
            border-radius: 16px 16px 0 0;
            color: white;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
        }

        .menu-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-top: 20px; }
        .menu-item { background: #ffffff; padding: 25px 15px; text-align: center; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); color: #333; font-weight: 600; transition: 0.3s; border: 1px solid #eee; }
        .menu-item:hover { background: #0d2b45; color: white; transform: translateY(-5px); box-shadow: 0 8px 25px rgba(13, 43, 69, 0.3); border-color: #0d2b45; }
        .alert { padding: 15px; border-radius: 10px; margin: 15px 0; border-left: 6px solid; }
        .alert-success { background: #eaf6ed; border-color: #28a745; color: #1e7e34; }
        .alert-danger { background: #fdecea; border-color: #dc3545; color: #a71d2a; }
        @media (max-width: 600px) { .navbar { flex-direction: column; align-items: flex-start; padding: 15px; } .navbar a { margin: 5px 0; } .dashboard-banner { padding: 20px 15px; } }
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="logo">🧪 <span>SISGALENO</span>2026</div>
        <div>
            {% if session.get('usuario') %}
                <span>👤 {{ session['usuario'] }}</span>
                <a href="{{ url_for('dashboard') }}">Inicio</a>
                {% if session['rol'] in ['administrador', 'medico', 'laboratorista'] %}
                    <a href="{{ url_for('pacientes') }}">Pacientes</a>
                {% endif %}
                {% if session['rol'] in ['administrador', 'laboratorista'] %}
                    <a href="{{ url_for('laboratorio') }}">Laboratorio</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="btn btn-danger" style="padding: 5px 15px;">Salir</a>
            {% endif %}
        </div>
    </nav>
    <div class="container">
        <!-- CONTENIDO_DINAMICO -->
    </div>
</body>
</html>
"""

# ==========================================
# LÓGICA PARA GENERAR EL TICKET EN PDF
# ==========================================
def generar_ticket_pdf(paciente_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, dni, nombre, apellido FROM pacientes WHERE id=?", (paciente_id,))
    paciente = cursor.fetchone()
    conn.close()
    if not paciente: return None
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica-Bold", 18)
    c.drawString(30, height - 40, "SISGALENO2026")
    c.setFont("Helvetica", 10)
    c.drawString(30, height - 60, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    c.line(30, height - 85, width - 30, height - 85)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(30, height - 110, "TICKET DE REGISTRO DE PACIENTE")
    c.setFont("Helvetica", 12)
    c.drawString(30, height - 140, f"ID: {paciente[0]}")
    c.drawString(30, height - 160, f"DNI: {paciente[1]}")
    c.drawString(30, height - 180, f"Nombre: {paciente[2]}")
    c.drawString(30, height - 200, f"Apellido: {paciente[3]}")
    c.line(30, height - 220, width - 30, height - 220)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(30, height - 250, "Ticket generado automáticamente por SISGALENO2026.")
    c.save()
    buffer.seek(0)
    return buffer

# ==========================================
# LÓGICA DE LABORATORIO
# ==========================================
def buscar_atenciones_web(dni, fecha_desde, fecha_hasta):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Elegir la función de agregación correcta para SQLite o PostgreSQL
    concat_func = "STRING_AGG" if IS_POSTGRES else "GROUP_CONCAT"
    
    sql = f"""
        SELECT a.id, p.nombre, p.apellido, 
               {concat_func}(e.nombre_examen, ', ') as examenes, 
               a.fecha_atencion,
               COUNT(CASE WHEN o.estado = 'Completado' THEN 1 END) as completados,
               COUNT(*) as total_examenes
        FROM atenciones a
        JOIN pacientes p ON a.id_paciente = p.id
        JOIN ordenes_laboratorio o ON o.id_atencion = a.id
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE 1=1
    """
    params = []
    if dni and dni.strip() != "":
        sql += " AND p.dni LIKE ?"
        params.append(f'%{dni.strip()}%')
    if fecha_desde and fecha_desde.strip() != "":
        sql += " AND date(a.fecha_atencion) >= ?"
        params.append(fecha_desde.strip())
    if fecha_hasta and fecha_hasta.strip() != "":
        sql += " AND date(a.fecha_atencion) <= ?"
        params.append(fecha_hasta.strip())
    if not dni and not fecha_desde and not fecha_hasta:
        sql += " AND (SELECT count(*) FROM ordenes_laboratorio sub WHERE sub.id_atencion = a.id AND sub.estado = 'Pendiente') > 0"
    
    sql += " GROUP BY a.id ORDER BY a.fecha_atencion DESC"
    cursor.execute(sql, params)
    datos = cursor.fetchall()
    conn.close()
    return datos

def obtener_examenes_por_atencion(id_atencion):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT o.id, e.nombre_examen, o.estado, o.resultado
        FROM ordenes_laboratorio o
        JOIN examenes_catalogo e ON o.id_examen = e.id
        WHERE o.id_atencion = ?
    """, (id_atencion,))
    examenes = cursor.fetchall()
    conn.close()
    return examenes

# ==========================================
# RUTAS DE LA WEB
# ==========================================
@app.route('/')
def index():
    if 'usuario' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    mensaje = ""; tipo_mensaje = ""
    if request.method == 'POST':
        user = request.form['usuario']; pwd = request.form['password']
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT rol FROM usuarios WHERE usuario=? AND password=?", (user, pwd))
        data = cursor.fetchone(); conn.close()
        if data:
            session['usuario'] = user; session['rol'] = data[0]
            return redirect(url_for('dashboard'))
        else:
            mensaje = "Credenciales incorrectas."; tipo_mensaje = "alert-danger"
    
    contenido_login = """
    <h2 style="text-align: center; color: #0d2b45;">Inicio de Sesión</h2>
    {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
    <form method="POST"><label>Usuario</label><input type="text" name="usuario" required><label>Contraseña</label><input type="password" name="password" required><button type="submit" class="btn btn-primary" style="width: 100%; margin-top: 10px;">Acceder</button></form>
    """
    html_login = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_login)
    return render_template_string(html_login, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'usuario' not in session: return redirect(url_for('login'))
    
    contenido_dashboard = """
    <div class="dashboard-banner"><div><h2>🔬 Bienvenido, {{ session['usuario'] }}</h2><p>Laboratorio Clínico SISGALENO2026</p></div><div style="font-size: 30px;">🧪💉</div></div>
    <div class="menu-grid">
    {% if session['rol'] in ['administrador', 'medico', 'laboratorista'] %}<a href="{{ url_for('pacientes') }}" class="menu-item">👤 Pacientes</a>{% endif %}
    {% if session['rol'] in ['administrador', 'laboratorista'] %}<a href="{{ url_for('laboratorio') }}" class="menu-item">🧪 Laboratorio</a>{% endif %}
    {% if session['rol'] == 'administrador' %}<a href="#" class="menu-item">📦 Inventario</a>{% endif %}
    </div>
    """
    html_dashboard = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_dashboard)
    return render_template_string(html_dashboard)

@app.route('/pacientes', methods=['GET', 'POST'])
def pacientes():
    if 'usuario' not in session: return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""; nuevo_paciente_id = None
    if request.method == 'POST':
        dni = request.form['dni']; nombre = request.form['nombre']; apellido = request.form['apellido']
        fecha_nac = request.form.get('fecha_nacimiento', '')
        telefono = request.form.get('telefono', '')
        direccion = request.form.get('direccion', '')
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO pacientes (dni, nombre, apellido, fecha_nacimiento, telefono, direccion) VALUES (?, ?, ?, ?, ?, ?)", 
                           (dni, nombre, apellido, fecha_nac, telefono, direccion))
            nuevo_paciente_id = cursor.lastrowid; conn.commit(); conn.close()
            mensaje = f"Paciente {nombre} {apellido} registrado."; tipo_mensaje = "alert-success"
        except sqlite3.IntegrityError:
            mensaje = "Error: El DNI ya está registrado."; tipo_mensaje = "alert-danger"
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, dni, nombre, apellido FROM pacientes ORDER BY id DESC")
    lista_pacientes = cursor.fetchall(); conn.close()
    
    contenido_pacientes = """
    <div style="display: flex; justify-content: space-between; align-items: center;"><h2 style="color: #0d2b45;">Gestión de Pacientes</h2><button onclick="toggleForm()" class="btn btn-success">+ Nuevo Paciente</button></div>
    <div id="form_registro" style="display: none; background: #f8f9fa; padding: 20px; border-radius: 12px; margin-bottom: 20px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <label>DNI</label><input type="text" name="dni" required>
            <label>Nombre</label><input type="text" name="nombre" required>
            <label>Apellido</label><input type="text" name="apellido" required>
            <label>Fecha Nacimiento</label><input type="date" name="fecha_nacimiento">
            <label>Teléfono</label><input type="text" name="telefono">
            <label>Dirección</label><input type="text" name="direccion">
            <button type="submit" class="btn btn-primary">Guardar Paciente</button>
        </form>
        {% if nuevo_paciente_id %}
            <div style="margin-top: 15px; border-top: 1px solid #ddd; padding-top: 15px;">
                <a href="{{ url_for('descargar_ticket', paciente_id=nuevo_paciente_id) }}" target="_blank" class="btn btn-primary">📥 Ticket PDF</a>
                <a href="https://wa.me/?text=Hola%2C%20se%20ha%20generado%20el%20ticket%20del%20paciente%20{{ nombre }}%20{{ apellido }}%20para%20su%20atenci%C3%B3n.%20Desc%C3%A1rgalo%20aqu%C3%AD%3A%20{{ url_for('descargar_ticket', paciente_id=nuevo_paciente_id, _external=True) }}" target="_blank" class="btn btn-whatsapp">📱 Enviar por WhatsApp</a>
            </div>
        {% endif %}
    </div>
    
    <div style="overflow-x: auto;">
        <table>
            <thead><tr><th>ID</th><th>DNI</th><th>Nombre</th><th>Apellido</th><th>Acciones</th></tr></thead>
            <tbody>
                {% for p in pacientes %}
                <tr>
                    <td>{{ p[0] }}</td>
                    <td>{{ p[1] }}</td>
                    <td>{{ p[2] }}</td>
                    <td>{{ p[3] }}</td>
                    <td>
                        <a href="{{ url_for('editar_paciente', id=p[0]) }}" class="btn btn-warning" style="padding: 4px 10px; font-size: 12px;">✏️ Editar</a>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    <script>function toggleForm(){ var x = document.getElementById("form_registro"); if (x.style.display === "none") { x.style.display = "block"; } else { x.style.display = "none"; } }</script>
    """
    html_pacientes = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_pacientes)
    return render_template_string(html_pacientes, pacientes=lista_pacientes, mensaje=mensaje, tipo_mensaje=tipo_mensaje, nuevo_paciente_id=nuevo_paciente_id, nombre=request.form.get('nombre', ''), apellido=request.form.get('apellido', ''))

@app.route('/ticket/<int:paciente_id>')
def descargar_ticket(paciente_id):
    if 'usuario' not in session: return redirect(url_for('login'))
    pdf_buffer = generar_ticket_pdf(paciente_id)
    if not pdf_buffer: return "Paciente no encontrado", 404
    return send_file(pdf_buffer, as_attachment=True, download_name=f"Ticket_Paciente_{paciente_id}.pdf", mimetype='application/pdf')

@app.route('/pacientes/editar/<int:id>', methods=['GET', 'POST'])
def editar_paciente(id):
    if 'usuario' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        dni = request.form['dni']
        nombre = request.form['nombre']
        apellido = request.form['apellido']
        fecha_nac = request.form.get('fecha_nacimiento', '')
        telefono = request.form.get('telefono', '')
        direccion = request.form.get('direccion', '')
        
        try:
            cursor.execute("""
                UPDATE pacientes 
                SET dni=?, nombre=?, apellido=?, fecha_nacimiento=?, telefono=?, direccion=?
                WHERE id=?
            """, (dni, nombre, apellido, fecha_nac, telefono, direccion, id))
            conn.commit()
            conn.close()
            return redirect(url_for('pacientes'))
        except Exception as e:
            conn.close()
            mensaje = f"Error al actualizar: {str(e)}"
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pacientes WHERE id=?", (id,))
            paciente = cursor.fetchone()
            conn.close()
            return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar), paciente=paciente, mensaje=mensaje, tipo_mensaje="alert-danger")
    
    cursor.execute("SELECT * FROM pacientes WHERE id=?", (id,))
    paciente = cursor.fetchone()
    conn.close()
    
    if not paciente:
        return "Paciente no encontrado", 404
        
    contenido_editar = """
    <div style="display: flex; justify-content: space-between; align-items: center;"><h2 style="color: #0d2b45;">Editar Paciente</h2><a href="{{ url_for('pacientes') }}" class="btn btn-danger">Cancelar</a></div>
    <div style="background: #f8f9fa; padding: 20px; border-radius: 12px; margin-bottom: 20px;">
        {% if mensaje %}<div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>{% endif %}
        <form method="POST">
            <label>DNI</label><input type="text" name="dni" value="{{ paciente[1] }}" required>
            <label>Nombre</label><input type="text" name="nombre" value="{{ paciente[2] }}" required>
            <label>Apellido</label><input type="text" name="apellido" value="{{ paciente[3] }}" required>
            <label>Fecha Nacimiento</label><input type="date" name="fecha_nacimiento" value="{{ paciente[4] if paciente[4] else '' }}">
            <label>Teléfono</label><input type="text" name="telefono" value="{{ paciente[5] if paciente[5] else '' }}">
            <label>Dirección</label><input type="text" name="direccion" value="{{ paciente[6] if paciente[6] else '' }}">
            <button type="submit" class="btn btn-primary">Guardar Cambios</button>
        </form>
    </div>
    """
    return render_template_string(LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_editar), paciente=paciente, mensaje="", tipo_mensaje="")

@app.route('/laboratorio', methods=['GET'])
def laboratorio():
    if 'usuario' not in session or session['rol'] not in ['administrador', 'laboratorista']:
        return redirect(url_for('login'))
    
    dni = request.args.get('dni', '')
    fecha_desde = request.args.get('fecha_desde', '')
    fecha_hasta = request.args.get('fecha_hasta', '')
    
    atenciones = buscar_atenciones_web(dni, fecha_desde, fecha_hasta)
    
    contenido_lab = """
    <h2 style="color: #0d2b45;">Órdenes de Laboratorio</h2>
    <div style="background: #f8f9fa; padding: 15px; border-radius: 12px; margin-bottom: 20px;">
        <form method="GET" style="display: flex; flex-wrap: wrap; gap: 10px; align-items: end;">
            <div><label>DNI</label><input type="text" name="dni" value="{{ dni }}" style="width: 150px; margin:0;"></div>
            <div><label>Desde</label><input type="date" name="fecha_desde" value="{{ fecha_desde }}" style="width: 150px; margin:0;"></div>
            <div><label>Hasta</label><input type="date" name="fecha_hasta" value="{{ fecha_hasta }}" style="width: 150px; margin:0;"></div>
            <div><button type="submit" class="btn btn-primary">🔍 Buscar</button></div>
            <div><a href="{{ url_for('laboratorio') }}" class="btn btn-warning">Limpiar</a></div>
        </form>
    </div>
    <div style="overflow-x: auto;">
        <table>
            <thead><tr><th>ID</th><th>Paciente</th><th>Exámenes</th><th>Fecha</th><th>Progreso</th><th>Estado</th><th>Acciones</th></tr></thead>
            <tbody>
                {% for a in atenciones %}
                    {% set completados = a[5] %}
                    {% set total = a[6] %}
                    {% set clase_fila = '' %}{% set clase_badge = '' %}{% set texto_estado = '' %}
                    {% if completados == total and total > 0 %}
                        {% set clase_fila = 'estado-verde' %}{% set clase_badge = 'badge-verde' %}{% set texto_estado = 'Completado' %}
                    {% elif completados > 0 %}
                        {% set clase_fila = 'estado-rojo' %}{% set clase_badge = 'badge-rojo' %}{% set texto_estado = 'Incompleto' %}
                    {% else %}
                        {% set clase_fila = 'estado-amarillo' %}{% set clase_badge = 'badge-amarillo' %}{% set texto_estado = 'Pendiente' %}
                    {% endif %}
                    <tr class="{{ clase_fila }}"><td><b>#{{ a[0] }}</b></td><td><b>{{ a[1] }} {{ a[2] }}</b></td><td>{{ a[3] }}</td><td>{{ a[4] }}</td><td>{{ completados }}/{{ total }}</td><td><span class="badge {{ clase_badge }}">{{ texto_estado }}</span></td><td><a href="{{ url_for('procesar_atencion', id_atencion=a[0]) }}" class="btn btn-success" style="padding: 6px 16px; font-size: 14px;">📝 Ingresar</a></td></tr>
                {% else %}
                    <tr><td colspan="7" style="text-align:center; padding: 30px;">🔬 No se encontraron atenciones registradas.</td></tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    """
    html_lab = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_lab)
    return render_template_string(html_lab, atenciones=atenciones, dni=dni, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)

@app.route('/laboratorio/procesar/<int:id_atencion>', methods=['GET', 'POST'])
def procesar_atencion(id_atencion):
    if 'usuario' not in session or session['rol'] not in ['administrador', 'laboratorista']:
        return redirect(url_for('login'))
    mensaje = ""; tipo_mensaje = ""
    if request.method == 'POST':
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            for key, valor in request.form.items():
                if key.startswith('resultado_'):
                    id_orden = key.split('_')[1]
                    resultado_texto = valor.strip()
                    if resultado_texto:
                        cursor.execute("UPDATE ordenes_laboratorio SET estado='Completado', resultado=? WHERE id=?", (resultado_texto, id_orden))
            conn.commit(); conn.close()
            mensaje = "Resultados guardados exitosamente."; tipo_mensaje = "alert-success"
        except Exception as e:
            mensaje = f"Error: {str(e)}"; tipo_mensaje = "alert-danger"
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT p.nombre, p.apellido, p.dni FROM atenciones a JOIN pacientes p ON a.id_paciente = p.id WHERE a.id = ?", (id_atencion,))
    paciente = cursor.fetchone(); examenes = obtener_examenes_por_atencion(id_atencion); conn.close()
    if not paciente: return "Atención no encontrada", 404
    
    contenido_procesar = """
    <h2 style="color: #0d2b45;">Ingresar Resultados</h2>
    <div style="background: #e9ecef; padding: 15px; border-radius: 12px; margin-bottom: 20px;">
        <p><b>👤 Paciente:</b> {{ paciente[0] }} {{ paciente[1] }} (DNI: {{ paciente[2] }})</p>
        <p><b>📄 Atención ID:</b> #{{ id_atencion }}</p>
    </div>
    {% if mensaje %}
        <div class="alert {{ tipo_mensaje }}">{{ mensaje }}</div>
    {% endif %}
    <form method="POST">
        <table><thead><tr><th>Examen</th><th>Estado</th><th>Resultado</th></tr></thead><tbody>
            {% for e in examenes %}
            <tr><td><b>{{ e[1] }}</b></td><td>
                {% if e[2] == 'Completado' %}<span class="badge badge-verde">Completado</span>
                {% else %}<span class="badge badge-amarillo">Pendiente</span>{% endif %}</td>
                <td>{% if e[2] == 'Completado' %}<span style="font-style: italic; color: #28a745;">{{ e[3] }}</span><input type="hidden" name="resultado_{{ e[0] }}" value="{{ e[3] }}">
                {% else %}<textarea name="resultado_{{ e[0] }}" rows="2" placeholder="Ingrese el valor..." required style="width:100%;">{{ e[3] }}</textarea>{% endif %}</td>
            </tr>
            {% endfor %}
        </tbody></table>
        <div style="margin-top: 15px;"><button type="submit" class="btn btn-success">Guardar Todos</button><a href="{{ url_for('laboratorio') }}" class="btn btn-danger">Cancelar</a></div>
    </form>
    """
    html_procesar = LAYOUT_BASE.replace('<!-- CONTENIDO_DINAMICO -->', contenido_procesar)
    return render_template_string(html_procesar, id_atencion=id_atencion, paciente=paciente, examenes=examenes, mensaje=mensaje, tipo_mensaje=tipo_mensaje)

# ==========================================
# EJECUCIÓN
# ==========================================
if __name__ == '__main__':
    # Si estás probando localmente, usa esto:
    app.run(host='0.0.0.0', port=8080, debug=True)