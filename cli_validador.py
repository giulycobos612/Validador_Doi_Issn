import pandas as pd
import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from src.validator import PublicationValidator

async def main():
    print("=" * 60)
    print("INICIANDO VALIDADOR MATEMÁTICO DUAL (DOI + ISSN)")
    print("=" * 60)
    
    input_file = "registros_entrada.xlsx"
    output_file = "resultados_validacion_matematica.xlsx"
    
    if not os.path.exists(input_file):
        print(f"\nERROR: No se encontró el archivo '{input_file}' en esta carpeta.")
        print("Asegúrate de que tenga las columnas: CODIGO_PUBLICACION, TITULO_PUBLICACION, CODIGO_ISSN, NOMBRE_REVISTA")
        return
        
    print(f"\nLeyendo '{input_file}'...")
    df = pd.read_excel(input_file, dtype=str)
    print(f"Se encontraron {len(df)} filas para analizar.")
    
    # Restaurar ceros a la izquierda para los ISSN que fueron ingresados como números en Excel
    def pad_issn(val):
        val = str(val).strip()
        if val and val.lower() != "nan" and len(val) < 8 and "," not in val and " " not in val:
            # Rellenar con ceros a la izquierda hasta llegar a 8 caracteres (asumiendo que no tiene guión)
            if "-" not in val:
                return val.zfill(8)
            # Si tiene guión y mide menos de 9, rellenar asumiendo formato XXXX-XXXX
            elif len(val) < 9:
                val = val.replace("-", "").zfill(8)
                return f"{val[:4]}-{val[4:]}"
        return val

    if "CODIGO_ISSN" in df.columns:
        df["CODIGO_ISSN"] = df["CODIGO_ISSN"].apply(pad_issn)
    
    registros_doi = []
    registros_issn = []
    
    for idx, row in df.iterrows():
        # Extracción segura de datos
        doi = str(row.get("CODIGO_PUBLICACION", "")).strip() if pd.notna(row.get("CODIGO_PUBLICACION", "")) else ""
        titulo_doi = str(row.get("TITULO_PUBLICACION", "")).strip() if pd.notna(row.get("TITULO_PUBLICACION", "")) else ""
        
        issn = str(row.get("CODIGO_ISSN", "")).strip() if pd.notna(row.get("CODIGO_ISSN", "")) else ""
        titulo_issn = str(row.get("NOMBRE_REVISTA", "")).strip() if pd.notna(row.get("NOMBRE_REVISTA", "")) else ""
        
        # Extraer solo el primer código ISSN si el usuario pegó varios (separados por coma, punto y coma, o salto de línea)
        issn_para_validar = issn
        for separador in [",", ";", "\n"]:
            if separador in issn_para_validar:
                issn_para_validar = issn_para_validar.split(separador)[0].strip()
                
        # También manejar espacios si parece haber dos códigos ISSN pegados
        if " " in issn_para_validar:
            partes = issn_para_validar.split()
            if len(partes) > 1 and len(partes[0]) >= 7:
                issn_para_validar = partes[0].strip()
        
        # Preparar registro para validar DOI
        registros_doi.append({
            "id_registro": f"Fila_DOI_{idx+2}",
            "tipo_publicacion": "ARTICULO",
            "codigo_doi": doi if doi and doi != "nan" else "",
            "codigo_issn": "",
            "titulo": titulo_doi if titulo_doi and titulo_doi != "nan" else "",
        })
        
        # Preparar registro para validar ISSN
        registros_issn.append({
            "id_registro": f"Fila_ISSN_{idx+2}",
            "tipo_publicacion": "REVISTA",
            "codigo_doi": "",
            "codigo_issn": issn_para_validar if issn_para_validar and issn_para_validar != "nan" else "",
            "titulo": titulo_issn if titulo_issn and titulo_issn != "nan" else "",
        })
        
    print("\nConsultando Bases de Datos Oficiales y Calculando Similitud (Procesamiento Paralelo)...")
    print("Por favor, espera unos segundos...\n")
    
    validador = PublicationValidator(threshold=0.65)
    
    # Validar ambos lotes en paralelo
    report_doi = await validador.validate_batch(registros_doi)
    
    # Para la segunda llamada, creamos un nuevo validador para renovar la sesión HTTP
    validador_issn = PublicationValidator(threshold=0.65)
    report_issn = await validador_issn.validate_batch(registros_issn)
    
    resultados_doi = report_doi.resultados
    resultados_issn = report_issn.resultados
    
    print("\n¡Validación completada con éxito!")
    
    # Preparar el DataFrame de salida y asegurar que todo sea texto para evitar que Excel borre ceros
    df_out = df.copy().astype(str)
    df_out.replace("nan", "", inplace=True)
    
    # Inyectar resultados DOI
    df_out["%_SIMILITUD_DOI"] = [res.similitud for res in resultados_doi]
    df_out["COINCIDE_DOI"] = [res.coincide for res in resultados_doi]
    df_out["TITULO_OFICIAL_DOI"] = [res.publicacion_encontrada for res in resultados_doi]
    
    # Inyectar resultados ISSN
    df_out["%_SIMILITUD_ISSN"] = [res.similitud for res in resultados_issn]
    df_out["COINCIDE_ISSN"] = [res.coincide for res in resultados_issn]
    df_out["NOMBRE_OFICIAL_REVISTA"] = [res.publicacion_encontrada for res in resultados_issn]
    
    print(f"\nGuardando resultados en '{output_file}'...")
    with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
        df_out.to_excel(writer, index=False, sheet_name='Resultados')
        workbook = writer.book
        worksheet = writer.sheets['Resultados']
        
        formato_celda = workbook.add_format({'text_wrap': True, 'valign': 'top', 'num_format': '@'})
        
        # Formatos de color
        formato_rojo = workbook.add_format({'bg_color': '#FFC7CE', 'font_color': '#9C0006', 'text_wrap': True, 'valign': 'top'})
        formato_verde = workbook.add_format({'bg_color': '#C6EFCE', 'font_color': '#006100', 'text_wrap': True, 'valign': 'top'})
        
        # Obtener última letra de columna
        def get_col_letter(col_idx):
            return chr(65 + col_idx) if col_idx < 26 else chr(64 + col_idx // 26) + chr(65 + col_idx % 26)
            
        last_col = get_col_letter(len(df_out.columns) - 1)
        table_range = f"A1:{last_col}{len(df_out) + 1}"
        
        # Crear tabla oficial de Excel
        column_settings = [{'header': col} for col in df_out.columns]
        worksheet.add_table(table_range, {
            'columns': column_settings,
            'style': 'Table Style Light 15' # Estilo en tonos grises
        })
        
        for idx, col in enumerate(df_out.columns):
            # Calcular longitud máxima de forma segura incluso con celdas vacías (NaN/float)
            col_max_len = df_out[col].map(lambda x: len(str(x))).max()
            max_len = max(col_max_len, len(str(col))) + 2
            max_len = min(max_len, 50)
            worksheet.set_column(idx, idx, max_len, formato_celda)
            
            # Aplicar formato condicional a las columnas COINCIDE
            if col in ["COINCIDE_DOI", "COINCIDE_ISSN"]:
                col_letter = get_col_letter(idx)
                range_str = f"{col_letter}2:{col_letter}{len(df_out) + 1}"
                
                worksheet.conditional_format(range_str, {
                    'type': 'cell',
                    'criteria': '==',
                    'value': '"No"',
                    'format': formato_rojo
                })
                worksheet.conditional_format(range_str, {
                    'type': 'cell',
                    'criteria': '==',
                    'value': '"Sí"',
                    'format': formato_verde
                })
            
    print("¡Proceso finalizado! Ya puedes abrir el archivo de resultados.")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
