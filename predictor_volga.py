from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pandas as pd
import xgboost as xgb
import psycopg2
import os
from dotenv import load_dotenv
from sklearn.metrics import mean_squared_error
import numpy as np
from sqlalchemy import create_engine

load_dotenv()

router = APIRouter()

# Config DB
DB_CONFIG = {
    "host": "localhost",
    "port": "5433",
    "dbname": "volga_sanitizada",
    "user": "juanjrz",
    "password": os.getenv("PASWORD_DB")
}

db_url = f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"
engine = create_engine(db_url)

class PrediccionRequest(BaseModel):
    cc: str
    fecha: str  # formato YYYY-MM-DD

@router.post("/predecir")
async def predecir_visitas(data: PrediccionRequest):
    try:
        # Conectar y obtener datos del CC
        # conn = psycopg2.connect(**DB_CONFIG)
        query = f"""
        SELECT d.fecha_cms_data_camara, d.hour_data_camara, d.data_in_real
        FROM cms_data_camara d
        JOIN cms_camaras c ON d.id_cms_camaras_data_fk = c.id_cms_camaras
        JOIN cms_sucursales s ON c.id_cms_sucursales_camaras_fk = s.id_cms_sucursales
        WHERE s.nombre_cms_sucursales = %s
        ORDER BY d.fecha_cms_data_camara, d.hour_data_camara
        """
        df = pd.read_sql_query(query, engine, params=(data.cc,))

        if df.empty:
            raise HTTPException(status_code=404, detail="No hay datos para ese centro comercial.")

        # Preprocesamiento básico
        df['fecha'] = pd.to_datetime(df['fecha_cms_data_camara'], errors='coerce')
        df = df.dropna(subset=['fecha'])
        df['dia'] = df['fecha'].dt.dayofyear
        df['hora'] = df['hour_data_camara']
        df = df[['dia', 'hora', 'data_in_real']]

        # Dividir en train y test por fecha (ejemplo básico)
        X = df[['dia', 'hora']]
        y = df['data_in_real']

        modelo = xgb.XGBRegressor(objective='reg:squarederror')
        modelo.fit(X, y)

        # Preparar entrada para predicción
        dia_pred = pd.to_datetime(data.fecha).dayofyear
        horas = list(range(24))
        X_pred = pd.DataFrame({'dia': [dia_pred]*24, 'hora': horas})

        predicciones = modelo.predict(X_pred)
        total_predicho = int(np.sum(predicciones))

        # Métrica MSE y RMSE (para info)
        y_pred = modelo.predict(X)
        mse = mean_squared_error(y, y_pred)
        rmse = np.sqrt(mse)

        return {
            "cc": data.cc,
            "fecha": data.fecha,
            "prediccion_total": total_predicho,
            "detalle_por_hora": list(map(int, predicciones)),
            "mse": round(mse, 2),
            "rmse": round(rmse, 2)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))