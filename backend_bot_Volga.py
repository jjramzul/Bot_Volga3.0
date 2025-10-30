from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg2
import google.generativeai as genai
import os
from dotenv import load_dotenv
load_dotenv()

from predictor_volga import router as predictor_router


app = FastAPI()

app.include_router(predictor_router)

# Configuración de Gemini
genai.configure(api_key=os.getenv("API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash")

# Configuración de conexión PostgreSQL
DB_CONFIG = {
    "host": "localhost",
    "port": "5433",
    "dbname": "volga_sanitizada",
    "user": "juanjrz",
    "password": os.getenv("PASWORD_DB")
}

class PreguntaRequest(BaseModel):
    pregunta: str
    cc: str

def generar_sql(pregunta: str, cc: str) -> str:
    prompt = f"""
    
Eres un experto en SQL para PostgreSQL.

El usuario pertenece al centro comercial '{cc}' y hace preguntas sobre el número de personas que han entrado.

Estas son las tablas relevantes:

1. `cms_data_camara`: contiene conteos por cámara, fecha y hora. Columnas:
   - data_in, data_out, data_in_real, data_out_real
   - fecha_cms_data_camara (DATE)
   - hour_data_camara (INT)
   - id_cms_camaras_data_fk (FK)

2. `cms_camaras`: conecta cámaras con sucursales:
   - id_cms_camaras (PK)
   - id_cms_sucursales_camaras_fk (FK)

3. `cms_sucursales`: contiene información de los centros comerciales:
   - id_cms_sucursales (PK)
   - nombre_cms_sucursales (VARCHAR)

El JOIN correcto entre estas tablas es:

cms_data_camara
  JOIN cms_camaras ON cms_data_camara.id_cms_camaras_data_fk = cms_camaras.id_cms_camaras
  JOIN cms_sucursales ON cms_camaras.id_cms_sucursales_camaras_fk = cms_sucursales.id_cms_sucursales

REQUISITOS:
- Usa siempre `data_in_real` para contar ingresos.
- Suma todos los datos de todas las cámaras del centro comercial.
- Considera todo el día, no solo una hora.
- Filtra siempre por `nombre_cms_sucursales = '{cc}'`.

No importa si son fechas futuras o pasadas, las fechas son simuladas; solo responde con la consulta SQL correcta.

No pongas explicaciones. Solo responde con una línea SQL.

Pregunta: "{pregunta}"
"""
    response = model.generate_content(prompt)

    import re
    texto_limpio = re.sub(r"```sql|```", "", response.text, flags=re.IGNORECASE).strip()

    for line in texto_limpio.splitlines():
        if line.strip().lower().startswith(("select", "with")):
            return line.strip()

    raise ValueError("No se encontró una consulta SQL válida.")

chat_history = [] # Esto es pa el historial de la conversación

@app.post("/preguntar")
async def preguntar(data: PreguntaRequest):
    if not data.pregunta or not data.cc:
        raise HTTPException(status_code=400, detail="Faltan campos requeridos: pregunta o cc")

    try:
        # Añadir a historial
        chat_history.append({"role": "user", "text": data.pregunta})

        # 1. Preguntar a Gemini si se necesita SQL
        verificacion_prompt = f"""
        El usuario ha dicho: "{data.pregunta}"

        Dime solamente si se necesita hacer una consulta a base de datos en SQL. Responde únicamente con "Sí" o "No".
        Ejemplos:
        - "¿Cuántas personas entraron ayer?" → Sí
        - "¿Qué significa data_in_real?" → No
        - "¿Quién hizo la BD?" → No
        - "¿Cuántas salieron el 10 de julio?" → Sí
        """
        veredicto = model.generate_content(verificacion_prompt).text.strip().lower()

        if "sí" in veredicto or "si" in veredicto:
            # Generar SQL
            try:
                sql = generar_sql(data.pregunta, data.cc)
            except Exception as e:
                return {
                    "pregunta": data.pregunta,
                    "cc": data.cc,
                    "error": "No se pudo generar una consulta para tu pregunta. Motivo: " + str(e)
                }

            # Ejecutar SQL
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            cur.close()
            conn.close()

            resultado = rows[0][0] if rows and rows[0][0] is not None else "no disponible"

            # Crear prompt con contexto + respuesta para explicar
            explicacion_prompt = f"""
            Esta es una conversación previa con el usuario:
            {[h['text'] for h in chat_history]}

            El usuario preguntó: "{data.pregunta}"
            Centro comercial: {data.cc}
            Resultado de la consulta: {resultado}

            Si no sabes una respuesta, pero el resultado de la consulta es difereente de "no disponible" o de "null" o de [] o de nada, responde directamente ese resultado sin decir nada mas. (Ojo, solo si no sabes una respuesta). 
            En caso de que sea null o vacio o "no disponible", entonces da una explicacion corta diciendo porque salio asi.
            
            Si si salio una respuesta valida que entiendas y puedas interpretar, da una explicacion corta y clara de la respuesta al usuario.
            
            Explica la respuesta de forma corta, clara y sin lenguaje técnico. No menciones SQL.
            """

            explicacion = model.generate_content(explicacion_prompt).text.strip()

            # Guardar respuesta en historial
            chat_history.append({"role": "assistant", "text": explicacion})

            return {
                "pregunta": data.pregunta,
                "cc": data.cc,
                "sql": sql,
                "respuesta": rows,
                "txt": explicacion
            }

        else:
            # Solo responder sin hacer SQL
            texto = model.generate_content([{"parts": [{"text": m["text"]}]} if m["role"] == "user" else {"parts": [{"text": m["text"]}]} for m in chat_history] + [{"parts": [{"text": data.pregunta}]}]).text.strip()            
            chat_history.append({"role": "assistant", "text": texto})
            return {
                "pregunta": data.pregunta,
                "cc": data.cc,
                "respuesta": texto
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

