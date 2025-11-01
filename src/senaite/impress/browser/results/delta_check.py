# -*- coding: utf-8 -*- 
from Products.Five import BrowserView
from Products.CMFCore.utils import getToolByName

try:
    from bika.lims import api, logger
except Exception:
    api = None
    import logging
    logger = logging.getLogger("senaite.impress")

import json
import re
import unicodedata
import datetime
import time
from collections import defaultdict


def _u(v):
    try:
        return unicode(v)
    except Exception:
        try:
            return u"%s" % v
        except Exception:
            return u""


def _strip_accents(s):
    try:
        s = _u(s)
        return u"".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != "Mn"
        )
    except Exception:
        return _u(s)


def _to_num(x):
    """Convierte x a float si es posible. Acepta '1,23', '<1', '> 3.5'."""
    try:
        if x in (None, u"", ""):
            return None
        try:
            num_types = (int, long, float)  # noqa
        except NameError:
            num_types = (int, float)
        if isinstance(x, num_types):
            return float(x)
        s = _u(x)
        s = s.replace("<", "").replace(">", "")
        s = s.replace(",", ".")
        return float(s)
    except Exception:
        return None


def _norm(s):
    return u" ".join(_u(s).strip().lower().split()) if s else u""


class InfolabsaDeltaCheck(BrowserView):
    """Delta check optimizado con soporte para múltiples estudios del mismo día."""

    PERIOD_DAYS = 365
    MAX_POINTS = 20
    MAX_POINTS_PER_DAY = 3  # Máximo de puntos por día para evitar saturación
    STATES_OK = set(("verified", "to_be_published", "published", "verified_duplicate"))

    # ------------------ CACHE Y METADATOS ------------------
    def __init__(self, context, request):
        super(InfolabsaDeltaCheck, self).__init__(context, request)
        self._service_cache = {}
        self._ar_metadata_cache = {}
        self._patient_cache = {}

    def _get_service_info(self, service_uid):
        """Cache de información de servicios de análisis."""
        if service_uid not in self._service_cache:
            service = self._get_obj_by_uid(service_uid)
            if service:
                self._service_cache[service_uid] = {
                    'keyword': self._get(service, 'getKeyword'),
                    'title': self._title_of(service),
                    'code': self._service_code(service),
                    'unit': self._get(service, 'getUnit')
                }
            else:
                self._service_cache[service_uid] = {}
        return self._service_cache[service_uid]

    def _batch_get_service_info(self, service_uids):
        """Obtiene información de múltiples servicios en lote."""
        missing = [uid for uid in service_uids if uid not in self._service_cache]
        if missing:
            for uid in missing:
                self._get_service_info(uid)

    def _batch_ar_metadata(self, ar_list):
        """Extrae metadatos de múltiples ARs eficientemente."""
        metadata = {}
        for ar in ar_list:
            ar_uid = self._get(ar, "UID")
            if not ar_uid or ar_uid in metadata:
                continue
                
            metadata[ar_uid] = {
                "obj": ar,
                "rid": self._get(ar, "getRequestID") or self._get(ar, "getId"),
                "date_received": self._received_date_of_ar(ar),
                "date_verified": self._date_of_ar(ar),
                "state": self._state_of(ar),
                "patient_keys": self._patient_keys(ar, self._patient_obj(ar))
            }
        self._ar_metadata_cache.update(metadata)
        return metadata

    # ------------------ UTILS BASE OPTIMIZADAS ------------------
    def _get(self, obj, name, default=None):
        if not obj:
            return default
        attr = getattr(obj, name, None)
        if callable(attr):
            try:
                return attr()
            except Exception:
                return default
        return attr if attr is not None else default

    def _title_of(self, obj):
        for g in ("Title", "title_or_id"):
            if hasattr(obj, g):
                try:
                    t = getattr(obj, g)()
                    if t:
                        return _u(t)
                except Exception:
                    pass
        return _u(getattr(obj, "id", ""))

    def _get_obj_by_uid(self, uid):
        """Obtiene objeto por UID usando catálogo."""
        try:
            cat = self._cat()
            brains = cat(UID=uid)
            if brains:
                return brains[0].getObject()
        except Exception:
            pass
        return None

    def _cat(self):
        portal = self.context.portal_url.getPortalObject()
        cat = getToolByName(portal, "senaite_catalog_sample", None)
        if cat:
            return cat
        return getToolByName(portal, "portal_catalog")

    def _acat(self):
        portal = self.context.portal_url.getPortalObject()
        acat = getToolByName(portal, "senaite_catalog_analysis", None)
        if acat:
            return acat
        return getToolByName(portal, "portal_catalog")

    def _catalog_has_index(self, name, analyses=False):
        try:
            cat = self._acat() if analyses else self._cat()
            return name in cat.indexes()
        except Exception:
            return False

    def _wftool(self):
        try:
            portal = self.context.portal_url.getPortalObject()
            return getToolByName(portal, "portal_workflow")
        except Exception:
            return None

    # ------------------ ESTADO ------------------
    def _state_of(self, obj, brain=None):
        try:
            if brain is not None:
                rs = getattr(brain, "review_state", None)
                if rs:
                    return _u(rs)
        except Exception:
            pass
        
        # Cache simple para estados
        obj_uid = self._get(obj, "UID")
        if hasattr(obj, '_cached_state') and obj_uid:
            return obj._cached_state
            
        for g in ("getReviewState", "review_state", "state"):
            try:
                v = getattr(obj, g, None)
                v = v() if callable(v) else v
                if v:
                    if obj_uid:
                        obj._cached_state = _u(v)
                    return _u(v)
            except Exception:
                continue
        try:
            wftool = self._wftool()
            if wftool:
                v = wftool.getInfoFor(obj, "review_state", default=None)
                if v:
                    if obj_uid:
                        obj._cached_state = _u(v)
                    return _u(v)
        except Exception:
            pass
        return None

    # ------------------ FECHA / FORMATO OPTIMIZADOS ------------------
    def _iso(self, dt):
        if not dt:
            return u""
        if api:
            try:
                zdt = api.to_datetime(dt)
                return zdt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass
        try:
            return dt.ISO8601()
        except Exception:
            return _u(dt)

    def _fmt_local(self, dt):
        if not dt:
            return u"—"
        if api:
            try:
                return api.to_localized_time(dt)
            except Exception:
                pass
        try:
            return self.context.toLocalizedTime(dt)
        except Exception:
            pass
        try:
            return _u(dt)
        except Exception:
            return u"—"

    def _fmt_ddmmyy(self, iso):
        """Devuelve DDMMYY sin hora, a partir de ISO-8601."""
        try:
            s = _u(iso).replace("Z", "")
            fmt = "%Y-%m-%dT%H:%M:%S" if "T" in s else "%Y-%m-%d"
            dt = datetime.datetime.strptime(s, fmt)
            return dt.strftime("%d%m%y")
        except Exception:
            return re.sub(r"\D+", "", _u(iso))[:6] or _u(iso)

    def _to_epoch_ms(self, iso):
        """Convierte ISO-8601 a epoch en milisegundos optimizado."""
        try:
            clean_iso = _u(iso).replace("Z", "").split('+')[0]
            
            if 'T' in clean_iso:
                fmt = "%Y-%m-%dT%H:%M:%S"
                dt = datetime.datetime.strptime(clean_iso, fmt)
            else:
                fmt = "%Y-%m-%d"
                dt = datetime.datetime.strptime(clean_iso, fmt)
            
            timestamp = time.mktime(dt.timetuple()) * 1000
            return int(timestamp)
            
        except Exception as e:
            logger.error("Error convirtiendo fecha %s a epoch: %s" % (iso, str(e)))
            return None

    def _date_of_ar(self, ar):
        for g in ("getDateVerified", "getDatePublished", "getDateReceived", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    def _received_date_of_ar(self, ar):
        for g in ("getDateReceived", "getReceptionDate", "getSamplingDate", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    # ------------------ AR / PACIENTE OPTIMIZADO ------------------
    def _patient_obj(self, ar):
        if ar in self._patient_cache:
            return self._patient_cache[ar]
            
        for pa in ("getPatient", "Patient", "getRelatedPatient"):
            if hasattr(ar, pa):
                try:
                    p = getattr(ar, pa)()
                    if p:
                        self._patient_cache[ar] = p
                        return p
                except Exception:
                    pass
        return None

    def _mrn_of_ar(self, ar, patient):
        for obj in (ar, patient):
            if not obj:
                continue
            for name in (
                "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
                "getClientPatientID", "getPatientID", "getIdentifier",
            ):
                v = self._get(obj, name)
                if v:
                    return _u(v).strip()
        return None

    def _patient_uid_of(self, patient):
        for name in ("UID", "getUID", "getId"):
            v = getattr(patient, name, None)
            try:
                v = v() if callable(v) else v
            except Exception:
                pass
            if v:
                return _u(v)
        return None

    def _patient_keys(self, ar, patient):
        keys = {}
        mrn = self._mrn_of_ar(ar, patient)
        if mrn:
            keys["mrn"] = mrn
        if patient:
            puid = self._patient_uid_of(patient)
            if puid:
                keys["patient_uid"] = puid
        full = None
        for obj in (ar, patient):
            if not obj:
                continue
            for name in ("getPatientFullName", "getFullname", "Title"):
                v = self._get(obj, name)
                if v:
                    full = _u(v).strip()
                    break
            if full:
                break
        if full:
            keys["fullname"] = _norm(full)
        return keys

    def _analyses_of(self, ar):
        for g in ("getAnalyses", "analyses", "getAnalysis"):
            v = self._get(ar, g)
            if v:
                try:
                    return list(v)
                except Exception:
                    return v
        return []

    # ------------------ ANALITO KEYS OPTIMIZADO ------------------
    def _service_of(self, a):
        try:
            return getattr(a, "getAnalysisService", lambda: None)()
        except Exception:
            return None

    def _service_uid_of(self, a):
        for g in ("getServiceUID", "ServiceUID"):
            v = self._get(a, g)
            if v:
                return _u(v)
        svc = self._service_of(a)
        return self._get(svc, "UID")

    def _service_code(self, svc):
        for g in ("getAnalysisCode", "getCode", "getServiceID", "getId", "id"):
            v = self._get(svc, g)
            if v:
                return _u(v)
        return None

    def _unit_of(self, a):
        for g in ("getUnit", "Unit", "getUnitAbbreviation"):
            v = self._get(a, g)
            if v:
                return _u(v)
        return u""

    def _analysis_keys(self, a):
        svc = self._service_of(a)
        svc_uid = self._get(svc, "UID") or self._service_uid_of(a)
        
        # Usar cache de servicio si está disponible
        if svc_uid and svc_uid in self._service_cache:
            svc_info = self._service_cache[svc_uid]
            kw = svc_info.get('keyword')
            title = svc_info.get('title')
            code = svc_info.get('code')
        else:
            kw = self._get(svc, "getKeyword") if svc else None
            if not kw:
                kw = self._get(a, "getKeyword")
            title = self._title_of(svc) if svc else (self._get(a, "Title") or u"")
            code = self._service_code(svc) if svc else None

        uid = None
        if svc_uid:
            uid = _u(svc_uid)
        elif kw:
            uid = u"kw:" + _u(kw).strip().lower()
        elif title:
            uid = u"title:" + _u(title).strip().lower()

        return {
            "svc_uid": _u(svc_uid) if svc_uid else None,
            "keyword": _u(kw).strip() if kw else None,
            "title": _u(title).strip() if title else None,
            "code": _u(code).strip() if code else None,
            "uid": uid,
            "unit": self._unit_of(a),
            "name": _u(title or kw or self._get(a, "Title") or u""),
        }

    # ===== EXTRACCIÓN NUMÉRICA ROBUSTA =====
    def _result_value(self, a):
        # 1) Intento directo
        for g in ("getResult", "Result", "result", "getValue"):
            v = self._get(a, g)
            if v not in (None, u"", ""):
                num = _to_num(v)
                if num is not None:
                    fr = self._get(a, "getFormattedResult")
                    return (fr if fr not in (None, u"", "") else _u(v)), float(num)
        # 2) FormattedResult con número
        fr = self._get(a, "getFormattedResult")
        if fr not in (None, u"", ""):
            s = _u(fr)
            m = re.search(r'[-<>]?\s*\d+(?:[.,]\d+)?', s)
            if m:
                num = _to_num(m.group(0))
                if num is not None:
                    return (s, float(num))
            # 3) Cualitativos
            sn = _norm(_strip_accents(s))
            neg = (u"ausente", u"no detectado", u"no-detectado", u"nd",
                   u"negativo", u"sin crecimiento", u"no growth",
                   u"absent", u"none detected", u"not detected", u"no se detecta")
            pos = (u"presente", u"detectado", u"positivo", u"con crecimiento",
                   u"present", u"detected", u"growth")
            if any(k in sn for k in neg):
                return (s, 0.0)
            if any(k in sn for k in pos):
                return (s, 1.0)
        return u"—", None

    # ------------------ BÚSQUEDA OPTIMIZADA DE ARs PREVIOS ------------------
    def _optimized_patient_query(self, pkeys):
        """Consulta optimizada por paciente usando índices disponibles."""
        cat = self._cat()
        try:
            indexes = set(cat.indexes())
        except Exception:
            indexes = set()

        # 1. Búsqueda más específica primero - UID de paciente
        puid = pkeys.get("patient_uid")
        for idx in ("getPatientUID", "patient_uid", "PatientUID"):
            if puid and idx in indexes:
                return {idx: puid}

        # 2. MRN con índices disponibles
        mrn = pkeys.get("mrn")
        mrn_indexes = ["getMedicalRecordNumber", "medical_record_number", 
                      "getClientPatientID", "getPatientID", "getIdentifier"]
        for idx in mrn_indexes:
            if mrn and idx in indexes:
                return {idx: mrn}

        # 3. Fallback por nombre (menos eficiente)
        fullname = pkeys.get("fullname")
        if fullname and "getPatientFullName" in indexes:
            return {"getPatientFullName": fullname}

        return {}

    def _candidate_ars(self, current_ar, patient, pkeys):
        """Busca ARs candidatos optimizados, incluyendo mismo día."""
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")

        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS
        except Exception:
            cutoff = None

        # Query base optimizada
        query = {
            "portal_type": "AnalysisRequest",
            "sort_on": "created",
            "sort_order": "descending",
        }

        # Filtro de paciente optimizado
        patient_filter = self._optimized_patient_query(pkeys)
        query.update(patient_filter)

        # Filtro temporal optimizado
        try:
            idxs = set(cat.indexes())
        except Exception:
            idxs = set()
            
        if "getDateReceived" in idxs:
            if cutoff:
                query["getDateReceived"] = {"query": cutoff, "range": "min"}
        elif "created" in idxs:
            if cutoff:
                query["created"] = {"query": cutoff, "range": "min"}

        # Búsqueda optimizada
        try:
            if patient_filter:
                brains = cat.searchResults(**query)
            else:
                # Sin filtro de paciente, limitar resultados
                brains = cat.searchResults(
                    portal_type="AnalysisRequest",
                    sort_on="created",
                    sort_order="descending",
                )[:1000]  # Aumentado para capturar más datos del mismo día
        except Exception:
            brains = []

        out = []
        seen_uids = set([cur_uid])

        for b in brains:
            if getattr(b, "UID", None) in seen_uids:
                continue
                
            try:
                b_state = getattr(b, "review_state", None)
                if b_state and _u(b_state) not in self.STATES_OK:
                    continue
            except Exception:
                pass
                
            try:
                obj = b.getObject()
            except Exception:
                continue
                
            # Verificación adicional de paciente si no hay filtro
            if not patient_filter:
                if not self._same_patient(obj, pkeys):
                    continue
                    
            out.append(obj)
            seen_uids.add(b.UID)
            
        return out

    def _same_patient(self, other_ar, pkeys):
        """Verifica si es el mismo paciente de forma optimizada."""
        # Cache de verificación
        if hasattr(other_ar, '_patient_checked'):
            return other_ar._patient_checked
            
        found = set()
        for name in (
            "getMedicalRecordNumberValue", "getMedicalRecordNumber", "getMRN",
            "getClientPatientID", "getPatientID", "getIdentifier",
        ):
            v = self._get(other_ar, name)
            if v:
                found.add(_u(v).strip())
                
        if found and pkeys.get("mrn"):
            if pkeys["mrn"] in found:
                other_ar._patient_checked = True
                return True

        full = None
        for name in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(other_ar, name)
            if v:
                full = _norm(v)
                break
                
        result = bool(full and pkeys.get("fullname") and full == pkeys["fullname"])
        other_ar._patient_checked = result
        return result

    # ------------------ SERIE TEMPORAL OPTIMIZADA CON MISMO DÍA ------------------
    def _build_analyte_series(self, ars_list, target_analytes):
        """Construye series temporales para múltiples analitos optimizado."""
        if not ars_list:
            return {}

        # Metadatos batch de todos los ARs
        ar_metadata = self._batch_ar_metadata(ars_list)
        
        # Preparar datos para búsqueda masiva
        ar_ids = [meta["rid"] for meta in ar_metadata.values() if meta.get("rid")]
        service_uids = set(analyte.get("svc_uid") for analyte in target_analytes if analyte.get("svc_uid"))
        
        # Precargar cache de servicios
        self._batch_get_service_info(list(service_uids))

        # Búsqueda masiva de análisis
        analysis_brains = self._bulk_analyses_search(ar_ids, list(service_uids))
        
        # Procesamiento optimizado
        series_data = self._process_analysis_brains(analysis_brains, ar_metadata, target_analytes)
        
        return series_data

    def _bulk_analyses_search(self, ar_ids, service_uids):
        """Búsqueda masiva optimizada de análisis."""
        acat = self._acat()
        if not acat or not ar_ids:
            return []

        query = {
            "portal_type": "Analysis",
            "getRequestID": ar_ids,
            "review_state": list(self.STATES_OK)
        }

        # Filtro por servicios si es específico
        if service_uids and self._catalog_has_index("getServiceUID", analyses=True):
            query["getServiceUID"] = list(service_uids)

        try:
            return acat.searchResults(**query)
        except Exception:
            return []

    def _process_analysis_brains(self, analysis_brains, ar_metadata, target_analytes):
        """Procesa brains de análisis de forma optimizada."""
        series_by_analyte = defaultdict(list)
        
        # Mapa de analitos objetivo para matching rápido
        target_map = {}
        for analyte in target_analytes:
            key = self._get_analyte_key(analyte)
            target_map[key] = analyte

        for brain in analysis_brains:
            try:
                # Extraer datos del brain eficientemente
                analysis_data = self._extract_analysis_data(brain, ar_metadata)
                if not analysis_data:
                    continue

                # Matching con analitos objetivo
                analyte_key = self._get_analyte_key(analysis_data)
                if analyte_key in target_map:
                    series_by_analyte[analyte_key].append(analysis_data)
                    
            except Exception:
                continue

        # Aplicar estrategia de agrupamiento inteligente
        return self._apply_smart_grouping(series_by_analyte, target_analytes)

    def _extract_analysis_data(self, brain, ar_metadata, target_analytes=None):
        """Extrae datos de análisis de forma optimizada."""
        try:
            rid = getattr(brain, "getRequestID", None)
            rid = rid() if callable(rid) else rid
            if not rid:
                return None

            # Encontrar metadatos del AR
            ar_info = None
            for meta in ar_metadata.values():
                if meta.get("rid") == rid:
                    ar_info = meta
                    break
                    
            if not ar_info:
                return None

            # Extraer datos básicos
            service_uid = getattr(brain, "getServiceUID", None)
            if callable(service_uid):
                service_uid = service_uid()
                
            raw_result = getattr(brain, "getResult", None)
            if callable(raw_result):
                raw_result = raw_result()
                
            formatted_result = getattr(brain, "getFormattedResult", None)  
            if callable(formatted_result):
                formatted_result = formatted_result()

            # Obtener fecha
            date_field = getattr(brain, "getResultCaptureDate", None) or getattr(brain, "created", None)
            if callable(date_field):
                date_field = date_field()
                
            iso_date = self._iso(date_field).replace("Z", "") if date_field else None
            if not iso_date:
                return None

            # Convertir valor numérico
            raw_val, num_val = self._parse_result_value(raw_result, formatted_result)
            if num_val is None:
                return None

            # Si se pasó un filtro de analitos, aseguremos que coincide por UID si está presente
            if target_analytes:
                try:
                    target_uids = set([ta.get("svc_uid") for ta in target_analytes if ta.get("svc_uid")])
                except Exception:
                    target_uids = set()
                if target_uids and service_uid and service_uid not in target_uids:
                    return None

            return {
                "date": iso_date,
                "value": float(num_val),
                "raw": _u(raw_val),
                "ar": ar_info["obj"],
                "rid": rid,
                "service_uid": service_uid,
                "analyte_key": self._get_analyte_key_from_service(service_uid)
            }
            
        except Exception:
            return None

    def _parse_result_value(self, raw_result, formatted_result):
        """Parsea valor de resultado optimizado."""
        # Primero intentar con raw_result
        if raw_result not in (None, u"", ""):
            num = _to_num(raw_result)
            if num is not None:
                return (formatted_result if formatted_result not in (None, u"", "") else _u(raw_result)), float(num)
                
        # Luego con formatted_result
        if formatted_result not in (None, u"", ""):
            s = _u(formatted_result)
            m = re.search(r'[-<>]?\s*\d+(?:[.,]\d+)?', s)
            if m:
                num = _to_num(m.group(0))
                if num is not None:
                    return (s, float(num))
                    
            # Manejo cualitativo
            sn = _norm(_strip_accents(s))
            neg = (u"ausente", u"no detectado", u"no-detectado", u"nd",
                   u"negativo", u"sin crecimiento", u"no growth")
            pos = (u"presente", u"detectado", u"positivo", u"con crecimiento")
            
            if any(k in sn for k in neg):
                return (s, 0.0)
            if any(k in sn for k in pos):
                return (s, 1.0)
                
        return u"—", None

    def _get_analyte_key(self, analyte_data):
        """Genera clave única para analito (Py2.7-safe, sin f-strings)."""
        if analyte_data.get("svc_uid"):
            return u"uid:%s" % analyte_data['svc_uid']
        elif analyte_data.get("keyword"):
            try:
                return u"kw:%s" % analyte_data['keyword'].lower()
            except Exception:
                return u"kw:%s" % _u(analyte_data['keyword']).lower()
        elif analyte_data.get("title"):
            return u"title:%s" % _norm(analyte_data['title'])
        return None

    def _get_analyte_key_from_service(self, service_uid):
        """Obtiene clave de analito desde service UID (Py2.7-safe)."""
        if not service_uid:
            return None
        service_info = self._get_service_info(service_uid)
        if service_info.get('keyword'):
            try:
                return u"kw:%s" % service_info['keyword'].lower()
            except Exception:
                return u"kw:%s" % _u(service_info['keyword']).lower()
        elif service_info.get('title'):
            return u"title:%s" % _norm(service_info['title'])
        return u"uid:%s" % service_uid

    def _apply_smart_grouping(self, series_by_analyte, target_analytes):
        """Aplica agrupamiento inteligente manteniendo puntos del mismo día."""
        result_series = {}
        
        for analyte in target_analytes:
            analyte_key = self._get_analyte_key(analyte)
            points = series_by_analyte.get(analyte_key, [])
            
            if not points:
                result_series[analyte_key] = []
                continue

            # Agrupar por día
            daily_groups = defaultdict(list)
            for point in points:
                day_key = point['date'][:10]  # YYYY-MM-DD
                daily_groups[day_key].append(point)

            # Aplicar estrategia de selección por día
            selected_points = []
            for day, day_points in sorted(daily_groups.items()):
                if len(day_points) <= self.MAX_POINTS_PER_DAY:
                    selected_points.extend(day_points)
                else:
                    # Estrategia para días con muchos puntos
                    selected_points.extend(self._select_daily_points(day_points))

            # Ordenar por fecha y aplicar límite global
            selected_points.sort(key=lambda x: x['date'])
            if len(selected_points) > self.MAX_POINTS:
                selected_points = self._smart_limit_points(selected_points)
                
            result_series[analyte_key] = selected_points

        return result_series

    def _select_daily_points(self, day_points):
        """Selecciona puntos representativos para un día con muchos datos."""
        strategies = {
            'temporal': self._temporal_selection,
            'extreme_values': self._extreme_values_selection,
            'first_last': self._first_last_selection
        }
        
        # Usar estrategia temporal por defecto
        return strategies['temporal'](day_points)

    def _temporal_selection(self, day_points):
        """Selección basada en distribución temporal."""
        if len(day_points) <= self.MAX_POINTS_PER_DAY:
            return day_points
            
        # Ordenar por hora
        day_points.sort(key=lambda x: x['date'])
        
        # Tomar primero, último y puntos intermedios distribuidos
        selected = [day_points[0]]
        if len(day_points) > 1:
            selected.append(day_points[-1])
            
        # Puntos intermedios distribuidos
        step = max(1, len(day_points) // (self.MAX_POINTS_PER_DAY - 2))
        for i in range(step, len(day_points)-1, step):
            if len(selected) < self.MAX_POINTS_PER_DAY:
                selected.append(day_points[i])
                
        return selected

    def _extreme_values_selection(self, day_points):
        """Selección basada en valores mínimos y máximos."""
        if len(day_points) <= self.MAX_POINTS_PER_DAY:
            return day_points
            
        day_points.sort(key=lambda x: x['value'])
        selected = [day_points[0]]  # mínimo
        selected.append(day_points[-1])  # máximo
        
        # Si hay espacio, agregar mediana
        if len(selected) < self.MAX_POINTS_PER_DAY and len(day_points) > 2:
            median_idx = len(day_points) // 2
            selected.append(day_points[median_idx])
            
        return selected

    def _first_last_selection(self, day_points):
        """Selección de primero y último punto del día."""
        if len(day_points) <= self.MAX_POINTS_PER_DAY:
            return day_points
            
        day_points.sort(key=lambda x: x['date'])
        selected = [day_points[0], day_points[-1]]
        
        # Si hay espacio y puntos intermedios, agregar uno del medio
        if len(selected) < self.MAX_POINTS_PER_DAY and len(day_points) > 2:
            mid_idx = len(day_points) // 2
            selected.append(day_points[mid_idx])
            
        return selected

    def _smart_limit_points(self, points):
        """Limita puntos manteniendo distribución temporal."""
        if len(points) <= self.MAX_POINTS:
            return points
            
        # Estrategia: mantener densidad reciente + muestreo histórico
        recent_cutoff = self._get_recent_cutoff()
        recent_points = [p for p in points if p['date'] >= recent_cutoff]
        historical_points = [p for p in points if p['date'] < recent_cutoff]
        
        # Si hay muchos puntos recientes, limitarlos
        if len(recent_points) > self.MAX_POINTS * 0.6:
            # Mantener distribución temporal de puntos recientes
            step = max(1, int(len(recent_points) // (self.MAX_POINTS * 0.6)))
            recent_points = recent_points[::step]
            
        # Combinar con muestreo estratificado de históricos
        total_needed = self.MAX_POINTS - len(recent_points)
        if historical_points and total_needed > 0:
            step = max(1, len(historical_points) // total_needed)
            historical_points = historical_points[::step][:total_needed]
            
        result = recent_points + historical_points
        result.sort(key=lambda x: x['date'])
        return result[:self.MAX_POINTS]

    def _get_recent_cutoff(self):
        """Obtiene fecha de corte para puntos recientes."""
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=30)
        return cutoff_date.strftime("%Y-%m-%dT%H:%M:%S")

    # ------------------ CÁLCULO DE TENDENCIA MEJORADO ------------------
    def _calculate_trend(self, series_points):
        """Calcula tendencia mejorada con soporte para múltiples puntos por día."""
        try:
            n = len(series_points or [])
            if n < 2:
                return u"∙"
                
            # Agrupar por día y promediar para cálculo de tendencia
            daily_values = defaultdict(list)
            for point in series_points:
                day_key = point['date'][:10]
                daily_values[day_key].append(point['value'])
                
            # Promedio por día
            daily_avg = []
            for day, values in sorted(daily_values.items()):
                daily_avg.append(sum(values) / float(len(values)))
                
            if len(daily_avg) < 2:
                return u"∙"
                
            # Cálculo de pendiente con días promediados
            xs = list(range(len(daily_avg)))
            ys = daily_avg
            
            return self._compute_slope_direction(xs, ys)
            
        except Exception:
            return u"∙"

    def _compute_slope_direction(self, xs, ys):
        """Computa dirección de la pendiente."""
        xbar = sum(xs) / float(len(xs))
        ybar = sum(ys) / float(len(ys))
        sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
        sxx = sum((x - xbar) * (x - xbar) for x in xs) or 1.0
        slope = sxy / sxx

        # Determinar significancia
        y_span = max(ys) - min(ys) or 1.0
        rel_change = abs(slope) * len(xs) / y_span
        
        if rel_change < 0.01:  # Umbral de significancia
            return u"∙"
        return u"▲" if slope > 0 else u"▼"

    # ------------------ VISTA PRINCIPAL OPTIMIZADA ------------------
    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys = self._patient_keys(ar, patient)

        # Búsqueda optimizada de ARs
        prev_ars = self._candidate_ars(ar, patient, pkeys)
        all_ars = prev_ars + [ar]

        # Preparar analitos actuales para búsqueda masiva
        current_analyses = self._analyses_of(ar)
        target_analytes = [self._analysis_keys(a) for a in current_analyses]

        # Construir series temporales optimizadas
        series_data = self._build_analyte_series(all_ars, target_analytes)

        rows = []
        multi_series = []
        chart_v2_series = []

        for analysis in current_analyses:
            keys = self._analysis_keys(analysis)
            raw_now, val_now = self._result_value(analysis)
            analyte_key = self._get_analyte_key(keys)

            # Obtener puntos de la serie optimizada
            points = series_data.get(analyte_key, [])
            
            # Preparar puntos para visualización
            display_points = []
            for p in points:
                iso = p.get('date')
                val = p.get('value')
                if iso is None or val is None:
                    continue
                    
                ddmmyy = self._fmt_ddmmyy(iso)
                ms = self._to_epoch_ms(iso)
                display_points.append({
                    'date': iso,
                    'value': float(val),
                    'x': iso,
                    'y': float(val),
                    'ddmmyy': ddmmyy,
                    'ms': ms,
                    'raw': p.get('raw', '')
                })

            # Solo procesar si hay puntos válidos
            if not display_points or val_now is None:
                continue

            # Calcular deltas y tendencias
            delta_data = self._calculate_delta_data(display_points, val_now, keys)
            trend_dir = self._calculate_trend(display_points)

            # Construir fila de resultados
            row = {
                'uid': keys["uid"],
                'name': keys["name"],
                'unit': keys["unit"] or u'',
                'value_now': raw_now if raw_now not in (None, u"", "") else u'—',
                'delta_pct': delta_data['pct'],
                'delta_dir': delta_data['dir'],
                'delta_abs_fmt': delta_data['abs_fmt'],
                'delta_combo_fmt': delta_data['combo_fmt'],
                'delta_note': u'',
                'trend_dir': trend_dir,
                'series': [{'date': pt['date'], 'value': pt['value'], 'raw': pt.get('raw', '')} for pt in display_points],
                'trend_from_fmt': self._fmt_ddmmyy(display_points[0]['date']) if display_points else u"—",
                'trend_to_fmt': self._fmt_ddmmyy(display_points[-1]['date']) if display_points else u"—",
            }
            rows.append(row)

            # Preparar datos para gráficos
            if len(display_points) >= 2:
                multi_series.append({
                    "name": keys["name"],
                    "unit": keys["unit"] or u"",
                    "series": display_points,
                    "xy": [{'x': pt['x'], 'y': pt['y']} for pt in display_points],
                    "data": [[pt['ms'], pt['value']] for pt in display_points if pt.get('ms') is not None],
                    "categories_ddmmyy": [pt['ddmmyy'] for pt in display_points],
                })

                chart_v2_series.append({
                    "name": keys["name"],
                    "unit": keys["unit"] or u"",
                    "data": [[pt['ms'], pt['value']] for pt in display_points if pt.get('ms') is not None],
                    "categories_ddmmyy": [pt['ddmmyy'] for pt in display_points],
                })

        # Preparar payload final
        label = u'%d meses' % (self.PERIOD_DAYS // 30)
        has_chart = bool(multi_series)

        payload = {
            'period_label': label,
            'rows': rows,
            'chart': {
                'series': multi_series,
                'max_points': self.MAX_POINTS,
                'window_days': self.PERIOD_DAYS,
            },
            'chart_v2': {
                'x_mode': 'ms',
                'series': chart_v2_series,
                'max_points': self.MAX_POINTS,
                'window_days': self.PERIOD_DAYS,
            },
            'has_chart': has_chart,
        }

        # Debug opcional
        if self.request.form.get("debug") or self.request.get("debug"):
            payload["_debug"] = self._debug_summary(ar, patient, pkeys, prev_ars, current_analyses, multi_series)

        return payload

    def _calculate_delta_data(self, points, current_val, analyte_keys):
        """Calcula datos delta optimizados."""
        if not points or current_val is None:
            return {'pct': u"N/A", 'dir': u"∙", 'abs_fmt': u"—", 'combo_fmt': u"—"}

        # Encontrar punto anterior más relevante (excluyendo el actual si está)
        prev_point = None
        for point in reversed(points[:-1]):  # Excluir el último punto (podría ser el actual)
            if point.get('value') is not None:
                prev_point = point
                break

        if prev_point is None:
            return {'pct': u"N/A", 'dir': u"∙", 'abs_fmt': u"—", 'combo_fmt': u"—"}

        prev_val = prev_point['value']
        
        try:
            delta_abs = float(current_val) - float(prev_val)
            abs_fmt = u"{:+.2f}".format(delta_abs)
            
            if prev_val != 0:
                pct = (delta_abs / abs(float(prev_val))) * 100.0
                pct_fmt = u"%.1f%%" % pct
            else:
                pct_fmt = u"N/A"
                
            if float(current_val) > float(prev_val):
                direction = u"▲"
            elif float(current_val) < float(prev_val):
                direction = u"▼"
            else:
                direction = u"Δ"
                
            combo_fmt = u"%s (%s)" % (abs_fmt, pct_fmt) if pct_fmt != u"N/A" else abs_fmt
            
            return {
                'pct': pct_fmt,
                'dir': direction,
                'abs_fmt': abs_fmt,
                'combo_fmt': combo_fmt
            }
            
        except Exception:
            return {'pct': u"N/A", 'dir': u"∙", 'abs_fmt': u"—", 'combo_fmt': u"—"}

    def _debug_summary(self, ar, patient, pkeys, prev_ars, now_analyses, multi_series):
        """Resumen debug optimizado."""
        sample_indexes = self._list_indexes(analyses=False)
        analysis_indexes = self._list_indexes(analyses=True)

        prev_summary = []
        for x in prev_ars[:20]:  # Limitar para debug
            rid = self._get(x, "getRequestID") or self._get(x, "getId") or u""
            dt = self._received_date_of_ar(x) or self._date_of_ar(x)
            rs = self._state_of(x)
            prev_summary.append({
                "rid": _u(rid),
                "date": self._iso(dt) if dt else u"",
                "date_fmt": self._fmt_local(dt) if dt else u"",
                "state": rs or u"",
            })

        a_summ = []
        for a in now_analyses:
            k = self._analysis_keys(a)
            a_summ.append({
                "name": k["name"],
                "unit": k["unit"],
                "svc_uid": k["svc_uid"],
                "keyword": k["keyword"],
            })

        msum = []
        for s in multi_series:
            pts = s.get("series") or []
            msum.append({
                "name": s.get("name"),
                "unit": s.get("unit"),
                "points": len(pts),
                "from": (pts[0]["date"] if pts else ""),
                "to": (pts[-1]["date"] if pts else ""),
            })

        return {
            "patient_keys": pkeys,
            "sample_catalog_indexes": sample_indexes,
            "analysis_catalog_indexes": analysis_indexes,
            "period_days": self.PERIOD_DAYS,
            "max_points_per_analyte": self.MAX_POINTS,
            "candidate_ARs_found": len(prev_ars),
            "candidate_ARs_preview": prev_summary,
            "current_AR_id": self._get(ar, "getRequestID") or self._get(ar, "getId"),
            "current_AR_date": self._fmt_local(self._date_of_ar(ar)),
            "current_analyses_count": len(now_analyses),
            "multi_series_count": len(multi_series),
        }

    def _list_indexes(self, analyses=False):
        """Lista índices disponibles optimizado."""
        try:
            cat = self._acat() if analyses else self._cat()
            return sorted(list(cat.indexes()))
        except Exception:
            return []
