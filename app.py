# =============================
# CONFIGURACIÓN Y DEPENDENCIAS
# =============================
from flask import Flask, render_template, request, session
import os
from dotenv import load_dotenv
import json
import time
import threading
import queue
from google import genai

# Carga variables de entorno desde .env
load_dotenv()
GENAI_API_KEY = os.getenv("GENAI_API_KEY")

# =============================
# PROMPT PARA GEMINI
# =============================
PROMT = """
Eres un generador experto de ejercicios de análisis de código en Python para estudiantes universitarios. Tu tarea es crear preguntas de opción múltiple, cada una con un enunciado claro, un fragmento de código Python bien formateado y cuatro opciones de respuesta, siguiendo estas reglas estrictas:

1. El código debe estar escrito en Python válido, usando la sintaxis de la última versión disponible.
2. Usa variables y nombres de funciones en español, en notación camelCase, para facilitar el entendimiento de los estudiantes hispanohablantes.
3. Los ejercicios deben integrar y combinar estos temas:
   - Estructuras secuenciales y condicionales (if, elif, else)
   - Estructuras repetitivas (for, while)
   - Funciones (def, argumentos, return)
   - Estructuras de datos: listas, tuplas, diccionarios y sets
   - Recursividad (cuando corresponda)
4. El código debe estar correctamente indentado y formateado, usando espacios estándar de Python (4 por nivel).
5. No incluyas comentarios, explicaciones ni texto adicional fuera del objeto JSON.
6. El fragmento de código debe ser autocontenible y ejecutable en Python, sin dependencias externas.
7. Las variables deben estar correctamente declaradas y usadas según las reglas de Python.
8. El código debe ser claro, didáctico y relevante para estudiantes de nivel universitario, evitando ambigüedades.
9. Cada pregunta debe ser única en su enunciado, código y opciones respecto a las anteriores de la misma sesión.
10. Si la pregunta requiere entrada de datos, usa la función input() y aclara los valores de entrada en el enunciado.
11. El código debe estar presentado en un bloque bien formateado, respetando la indentación y la sintaxis de Python.
12. Antes de decidir cuál es la respuesta correcta, debes ejecutar mentalmente el código y comprobar realmente cuál es la salida. No inventes ni asumas: si tienes dudas, investiga o analiza el código paso a paso.
13. Una vez que determines la respuesta correcta, vuelve a comprobar el código y verifica que la respuesta realmente corresponde a la salida esperada.
14. Bajo ninguna circunstancia inventes resultados o expliques sin haber comprobado el código.

Genera preguntas variadas de estos dos tipos (elige aleatoriamente en cada generación):
- ¿Qué salida tendrá el siguiente código?
- ¿Qué salida tendrá el siguiente código si se ingresan los siguientes valores? (en este caso, incluye en la pregunta los valores de entrada y asegúrate de que el código use input())

El formato de la respuesta debe ser un ÚNICO objeto JSON, SIN ningún texto antes o después, ni bloques de código Markdown. El objeto debe tener exactamente estas claves:

{
  "Pregunta": "Texto de la pregunta clara y concisa.",
  "Codigo": "Fragmento de código Python válido, autocontenible y bien formateado.",
  "Respuestas": ["Respuesta A", "Respuesta B", "Respuesta C", "Respuesta D"],
  "Respuesta correcta": "Respuesta correcta exactamente igual a una de las opciones",
  "Explicacion": "Explicación breve y genérica de por qué la respuesta correcta es la correcta, sin hacer referencia a la opción elegida por el usuario, sino explicando el razonamiento o el resultado del código."
}

Recuerda: responde SOLO con el objeto JSON, sin bloques de código, sin explicaciones y sin texto adicional. El código debe ser válido y ejecutable en Python, usando la sintaxis de la última versión y buenas prácticas de programación. Si tienes dudas sobre la salida, analiza el código paso a paso antes de responder.
"""

# =============================
# CLIENTE GENAI
# =============================
client = genai.Client(api_key=GENAI_API_KEY)

# =============================
# CACHE DE PREGUNTAS (COLA)
# =============================
CACHE_SIZE = 200  # Máximo de preguntas en cache
CACHE_MIN = 100   # Umbral mínimo para reponer el cache
pregunta_cache = queue.Queue(maxsize=CACHE_SIZE)

# =============================
# GENERACIÓN Y OBTENCIÓN DE PREGUNTAS
# =============================
def generar_pregunta():
    """
    Llama a Gemini para generar una pregunta nueva.
    Limpia el texto y lo convierte a un diccionario Python.
    """
    response = client.models.generate_content(
        model="gemini-2.0-flash", 
        contents=PROMT
    )
    try:
        text = response.text.strip()
        # Limpia el texto de bloques de código Markdown
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        pregunta_json = json.loads(text)
        # Validación de la estructura del JSON
        respuestas = pregunta_json.get("Respuestas")
        if isinstance(respuestas, str):
            respuestas = [r.strip() for r in respuestas.split(",")]
        elif not isinstance(respuestas, list):
            respuestas = []
        pregunta = {
            "pregunta": pregunta_json.get("Pregunta"),
            "codigo": pregunta_json.get("Codigo"),
            "respuestas": respuestas,
            "respuesta_correcta": pregunta_json.get("Respuesta correcta"),
            "explicacion": pregunta_json.get("Explicacion", "")
        }
        return pregunta
    except Exception as e:
        return {"error": "No se pudo extraer el JSON", "detalle": str(e), "texto": response.text}

# =============================
# HILO DE PRECARGA DE PREGUNTAS
# =============================
def precargar_preguntas():
    """
    Hilo en segundo plano que mantiene el cache de preguntas lleno.
    Solo consulta la API si el cache baja del umbral.
    """
    while True:
        if pregunta_cache.qsize() < CACHE_MIN:
            try:
                pregunta = generar_pregunta()
                # Solo la guarda si es válida
                if isinstance(pregunta, dict) and 'pregunta' in pregunta and 'codigo' in pregunta:
                    pregunta_cache.put(pregunta)
                time.sleep(1)  # Espera un segundo antes de volver a intentar
            except Exception as e:
                # Si es un error de cuota, espera más tiempo
                if "RESOURCE_EXHAUSTED" in str(e):
                    time.sleep(35)
                else:
                    time.sleep(5)
        else:
            time.sleep(2)  # Espera antes de volver a chequear

# Inicia el hilo de precarga al arrancar la app
threading.Thread(target=precargar_preguntas, daemon=True).start()

def obtener_pregunta_cache():
    """
    Obtiene una pregunta del cache (espera hasta 10s).
    Si el cache está vacío, genera una pregunta en caliente.
    """
    try:
        return pregunta_cache.get(timeout=10)
    except Exception:
        pregunta = generar_pregunta()
        if "error" in pregunta and "RESOURCE_EXHAUSTED" in pregunta.get("detalle", ""):
            return {
                "pregunta": "¡Límite de uso alcanzado!",
                "codigo": "",
                "respuestas": [],
                "respuesta_correcta": "",
                "explicacion": "Se ha superado el límite de uso de la API. Por favor, espera un minuto y vuelve a intentarlo."
            }
        return pregunta

# =============================
# FLASK APP Y RUTAS
# =============================
app = Flask(__name__)
app.secret_key = os.urandom(24)  # Necesario para usar sesiones

@app.route('/')
def inicio():
    """
    Ruta de inicio: muestra la presentación y botón para comenzar el quiz.
    """
    return render_template('inicio.html')

@app.route('/quiz', methods=['GET', 'POST'])
def quiz():
    """
    Ruta principal del quiz: muestra la pregunta actual y procesa la respuesta.
    """
    # Si falta cualquier variable de sesión, inicializa todo
    if not all(k in session for k in ['puntaje', 'total', 'inicio', 'pregunta_actual', 'errores']):
        session['puntaje'] = 0
        session['total'] = 0
        session['inicio'] = time.time()
        session['pregunta_actual'] = obtener_pregunta_cache()
        session['errores'] = []

    if request.method == 'POST':
        seleccion = request.form.get('respuesta')
        correcta = session['pregunta_actual']['respuesta_correcta']
        explicacion = session['pregunta_actual'].get('explicacion', '')
        session['total'] += 1
        if seleccion and seleccion.strip() == correcta.strip():
            session['puntaje'] += 1
        else:
            errores = session.get('errores', [])
            errores.append({
                'pregunta': session['pregunta_actual']['pregunta'],
                'codigo': session['pregunta_actual']['codigo'],
                'respuestas': session['pregunta_actual']['respuestas'],
                'respuesta_correcta': correcta,
                'explicacion': explicacion,
                'respuesta_usuario': seleccion
            })
            session['errores'] = errores

        if session['total'] >= 10:
            tiempo = int(time.time() - session['inicio'])
            puntaje = session['puntaje']
            errores = session.get('errores', [])
            session.clear()  # Limpia la sesión para un nuevo intento
            return render_template('resultado.html', correctas=puntaje, tiempo=tiempo, errores=errores)

        session['pregunta_actual'] = obtener_pregunta_cache()

    pregunta = session['pregunta_actual']
    num_pregunta = session.get('total', 0) + 1
    return render_template('quiz.html', pregunta=pregunta, num_pregunta=num_pregunta)

@app.route('/resultado')
def resultado():
    """
    Ruta para mostrar el resultado final (permite refrescar la página de resultado).
    """
    # Si se accede directamente, intenta recuperar errores de la sesión
    errores = session.get('errores', [])
    return render_template('resultado.html', 
                           correctas=request.args.get('correctas', 0), 
                           tiempo=request.args.get('tiempo', 0), 
                           errores=errores)

@app.route('/error')
def error():
    """
    Ruta para mostrar errores personalizados.
    """
    detalle = request.args.get('detalle', 'Error desconocido')
    texto = request.args.get('texto', '')
    return render_template('error.html', detalle=detalle, texto=texto), 500

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)