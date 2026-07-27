import re
import asyncio
import hashlib
from difflib import SequenceMatcher
from typing import List, Dict, Any, Union

try:
    from deep_translator import GoogleTranslator
    HAS_TRANSLATOR = True
except ImportError:
    HAS_TRANSLATOR = False

from .models import PublicationRecord, ValidationResult, AIValidationReport
from .api_clients import MetadataFetcher

class SimilarityEngine:
    """
    Motor matemático local para comparar cadenas de texto ignorando mayúsculas, signos y acentos.
    """
    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        # Convertir a minúsculas
        text = str(text).lower()
        # Eliminar tildes básicas (simplificado para no depender de librerías extra como unidecode)
        replacements = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ü", "u"), ("ñ", "n"))
        for a, b in replacements:
            text = text.replace(a, b)
        # Eliminar todo lo que no sea letra o número
        text = re.sub(r'[^\w\s]', '', text)
        # Eliminar espacios dobles
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    async def compare_titles_async(self, title1: str, title2: str, threshold: float = 0.65) -> dict:
        if not title1 or not title2:
            return {"coincide": "No", "similitud": 0.0, "observaciones": "Uno de los títulos está en blanco."}
            
        # Lógica para títulos bilingües separados por punto y coma (ej: "Title English; Título Español")
        def get_best_ratio(t1: str, t2: str) -> float:
            parts1 = [p.strip() for p in t1.split(';')] if ';' in t1 else [t1]
            parts2 = [p.strip() for p in t2.split(';')] if ';' in t2 else [t2]
            best_r = 0.0
            for p1 in parts1:
                n1 = self._normalize_text(p1)
                for p2 in parts2:
                    n2 = self._normalize_text(p2)
                    if n1 and n2:
                        r = SequenceMatcher(None, n1, n2).ratio()
                        if r > best_r:
                            best_r = r
            return best_r

        ratio = get_best_ratio(title1, title2)
        similitud_porcentaje = round(ratio * 100, 2)
        
        if ratio >= threshold:
            return {"coincide": "Sí", "similitud": similitud_porcentaje, "observaciones": f"Aprobado: El título coincide matemáticamente al {similitud_porcentaje}%."}
            
        # Intento de Traducción Automática si falla la primera pasada matemática
        if HAS_TRANSLATOR:
            try:
                # Traducir ambos títulos al inglés para nivelar el idioma (se ejecuta en un hilo separado para no bloquear)
                trans1 = await asyncio.to_thread(GoogleTranslator(source='auto', target='en').translate, title1)
                trans2 = await asyncio.to_thread(GoogleTranslator(source='auto', target='en').translate, title2)
                
                ratio_trans = get_best_ratio(trans1, trans2)
                similitud_trans = round(ratio_trans * 100, 2)
                
                if ratio_trans >= threshold:
                    return {"coincide": "Sí", "similitud": similitud_trans, "observaciones": f"Aprobado (Vía Traducción): El título traducido coincide matemáticamente al {similitud_trans}%."}
                else:
                    best_score = max(similitud_porcentaje, similitud_trans)
                    return {"coincide": "No", "similitud": best_score, "observaciones": f"Rechazado: Título muy distinto incluso tras intentar traducción automática (Similitud Máxima: {best_score}%)."}
            except Exception:
                # Si Google bloquea o falla, pasa silenciosamente y devuelve el puntaje original
                pass
                
        return {"coincide": "No", "similitud": similitud_porcentaje, "observaciones": f"Rechazado: El título registrado es muy distinto al oficial (Similitud: {similitud_porcentaje}%)."}


class PublicationValidator:
    """
    Main Orchestrator for validating DOIs and ISSNs using Mathematical Similarity (No AI).
    """
    def __init__(self, fetcher: MetadataFetcher = None, threshold: float = 0.65):
        self.fetcher = fetcher or MetadataFetcher()
        self.engine = SimilarityEngine()
        self.threshold = threshold
        self._cache: Dict[str, ValidationResult] = {}
        
    def _clean_code(self, code: Union[str, None]) -> str:
        return code.strip() if code else ""

    def _limpiar_codigo(self, raw: str) -> str:
        if not raw: return ""
        cleaned = str(raw).strip()
        if "doi.org/" in cleaned:
            cleaned = cleaned.split("doi.org/")[-1]
        
        cleaned = re.sub(r'(?i)^doi:\s*', '', cleaned)
        cleaned = re.sub(r'(?i)^issn:\s*', '', cleaned)
        
        if ":" in cleaned and not cleaned.startswith("10."):
            partes = cleaned.split(":", 1)
            if len(partes) > 1 and (partes[1].strip().startswith("10.") or re.match(r'^\d', partes[1].strip())):
                cleaned = partes[1].strip()
                
        cleaned = re.sub(r'[^a-zA-Z0-9.\-/:_;()]', '', cleaned)
        return cleaned

    def _is_valid_doi_format(self, doi: str) -> bool:
        if not doi:
            return False
        return bool(re.match(r'^10\.\d{4,9}/[-._;()/:A-Z0-9]+$', doi, re.IGNORECASE))
        
    def _is_valid_issn_format(self, issn: str) -> bool:
        if not issn:
            return False
        return bool(re.match(r'^\d{4}-?\d{3}[\dxX]$', issn, re.IGNORECASE))

    async def validate_record(self, record: Union[Dict[str, Any], PublicationRecord]) -> ValidationResult:
        if isinstance(record, dict):
            record = PublicationRecord(**record)
            
        doi_limpio = self._limpiar_codigo(record.codigo_doi)
        issn_limpio = self._limpiar_codigo(record.codigo_issn)
        
        # Corregir formato de ISSN si falta el guión (las APIs lo exigen estrictamente)
        if issn_limpio and len(issn_limpio) == 8 and "-" not in issn_limpio:
            issn_limpio = f"{issn_limpio[:4]}-{issn_limpio[4:]}"
        
        record_str = f"{record.titulo}_{doi_limpio}_{issn_limpio}"
        record_hash = hashlib.md5(record_str.encode('utf-8')).hexdigest()
        cache_key = f"code_{record_hash}"
        
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        if doi_limpio:
            if self._is_valid_doi_format(doi_limpio):
                record.codigo_doi = doi_limpio
                try:
                    official_data = await self.fetcher.fetch_doi_metadata(doi_limpio)
                except Exception as e:
                    official_data = {"error": f"Error API/Bloqueo: {type(e).__name__}"}
                    
                if not official_data:
                    result = ValidationResult(
                        codigo=doi_limpio,
                        publicacion_encontrada="REVISAR: No encontrado",
                        datos_registrados=record.titulo or "Desconocido",
                        coincide="No",
                        observaciones="REVISAR: El código ingresado no se encontró en la base de datos oficial o es inválido.",
                        similitud=0.0
                    )
                elif "error" in official_data:
                    result = ValidationResult(
                        codigo=doi_limpio,
                        publicacion_encontrada=f"REVISAR: {official_data['error']}",
                        datos_registrados=record.titulo or "Desconocido",
                        coincide="No",
                        observaciones=f"REVISAR: Hubo un error de conexión con la API de validación.",
                        similitud=0.0
                    )
                else:
                    official_title = official_data.get("titulo", "")
                    user_title = record.titulo or ""
                    
                    comp = await self.engine.compare_titles_async(user_title, official_title, self.threshold)
                    
                    result = ValidationResult(
                        codigo=doi_limpio,
                        publicacion_encontrada=official_title,
                        datos_registrados=user_title,
                        coincide=comp["coincide"],
                        observaciones=comp["observaciones"],
                        similitud=comp["similitud"]
                    )
                    
                self._cache[cache_key] = result
                return result
            else:
                return ValidationResult(
                    codigo=doi_limpio,
                    publicacion_encontrada="REVISAR: No se consultó",
                    datos_registrados=record.titulo or "Desconocido",
                    coincide="No",
                    observaciones="REVISAR: Error de formato. El DOI es incorrecto.",
                    similitud=0.0
                )
                
        elif issn_limpio:
            if self._is_valid_issn_format(issn_limpio):
                record.codigo_issn = issn_limpio
                try:
                    official_data = await self.fetcher.fetch_issn_metadata(issn_limpio)
                except Exception as e:
                    official_data = {"error": f"Error API/Bloqueo: {type(e).__name__}"}
                    
                if not official_data:
                    result = ValidationResult(
                        codigo=issn_limpio,
                        publicacion_encontrada="REVISAR: No encontrado",
                        datos_registrados=record.titulo or "Desconocido",
                        coincide="No",
                        observaciones="REVISAR: El código ingresado no se encontró en la base de datos oficial o es inválido.",
                        similitud=0.0
                    )
                elif "error" in official_data:
                    result = ValidationResult(
                        codigo=issn_limpio,
                        publicacion_encontrada=f"REVISAR: {official_data['error']}",
                        datos_registrados=record.titulo or "Desconocido",
                        coincide="No",
                        observaciones=f"REVISAR: Hubo un error de conexión con la API de validación.",
                        similitud=0.0
                    )
                else:
                    official_title = official_data.get("titulo", "")
                    user_title = record.titulo or ""
                    
                    comp = await self.engine.compare_titles_async(user_title, official_title, self.threshold)
                    
                    result = ValidationResult(
                        codigo=issn_limpio,
                        publicacion_encontrada=official_title,
                        datos_registrados=user_title,
                        coincide=comp["coincide"],
                        observaciones=comp["observaciones"],
                        similitud=comp["similitud"]
                    )
                    
                self._cache[cache_key] = result
                return result
            else:
                return ValidationResult(
                    codigo=issn_limpio,
                    publicacion_encontrada="REVISAR: No se consultó",
                    datos_registrados=record.titulo or "Desconocido",
                    coincide="No",
                    observaciones="REVISAR: Error de formato. El ISSN es incorrecto.",
                    similitud=0.0
                )
                
        else:
            return ValidationResult(
                codigo="N/A",
                publicacion_encontrada="REVISAR: No se consultó",
                datos_registrados=record.titulo or "Desconocido",
                coincide="No",
                observaciones="REVISAR: No se proporcionó ningún DOI o ISSN.",
                similitud=0.0
            )

    async def validate_batch(self, records: List[Union[Dict[str, Any], PublicationRecord]]) -> AIValidationReport:
        # Ya no requerimos un semáforo artificial (ai_sem) porque ya no usamos Groq.
        # Enviaremos las peticiones concurrentemente de a 5 a la vez para cuidar OpenAlex/Crossref.
        sem = asyncio.Semaphore(5)
        
        async def wrap_validate(rec):
            async with sem:
                return await self.validate_record(rec)
                
        tasks = [wrap_validate(rec) for rec in records]
        results = await asyncio.gather(*tasks)
        
        if hasattr(self.fetcher, 'close'):
            await self.fetcher.close()
            
        return AIValidationReport(resultados=list(results))
