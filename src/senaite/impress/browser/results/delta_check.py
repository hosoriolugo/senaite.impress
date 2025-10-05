# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from Products.CMFCore.utils import getToolByName

try:
    from bika.lims import api, logger
except Exception:
    api = None
    import logging
    logger = logging.getLogger("senaite.impress")


def _u(v):
    try:
        return unicode(v)
    except Exception:
        try:
            return u"%s" % v
        except Exception:
            return u""


def _to_num(x):
    try:
        if x in (None, u"", ""):
            return None
        # Python 2 ints/longs/float (mantener compat)
        try:
            num_types = (int, long, float)  # noqa
        except NameError:
            num_types = (int, float)
        if isinstance(x, num_types):
            return float(x)
        s = _u(x).replace(",", ".")
        return float(s)
    except Exception:
        return None


def _norm(s):
    # normaliza para comparar nombres: minúsculas y colapsa espacios
    return u" ".join(_u(s).strip().lower().split()) if s else u""


class InfolabsaDeltaCheck(BrowserView):
    """Delta check robusto por paciente y analito con fechas ISO-8601."""

    PERIOD_DAYS = 365   # 12 meses (ventana)
    MAX_POINTS  = 8     # tope de puntos por analito (más recientes)
    # Estados considerados "ok" para el delta (>= verified)
    STATES_OK = set(("verified", "to_be_published", "published", "verified_duplicate"))

    # ------------------ utils base ------------------
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

    def _cat(self):
        """Catálogo de AnalysisRequest (samples)."""
        portal = self.context.portal_url.getPortalObject()
        cat = getToolByName(portal, "senaite_catalog_sample", None)
        if cat:
            return cat
        return getToolByName(portal, "portal_catalog")

    def _acat(self):
        """Catálogo de Analysis."""
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

    # ------------------ estado (review_state) ------------------
    def _state_of(self, obj, brain=None):
        # 1) si viene del brain, úsalo
        try:
            if brain is not None:
                rs = getattr(brain, "review_state", None)
                if rs:
                    return _u(rs)
        except Exception:
            pass
        # 2) atributo simple o getter en el objeto
        for g in ("getReviewState", "review_state", "state"):
            try:
                v = getattr(obj, g, None)
                v = v() if callable(v) else v
                if v:
                    return _u(v)
            except Exception:
                continue
        # 3) workflow tool
        try:
            wftool = self._wftool()
            if wftool:
                v = wftool.getInfoFor(obj, "review_state", default=None)
                if v:
                    return _u(v)
        except Exception:
            pass
        return None

    # ------------------ fecha / formato ------------------
    def _iso(self, dt):
        if not dt:
            return u""
        # api helper si existe
        if api:
            try:
                zdt = api.to_datetime(dt)
                # ISO con Z para JS Date.parse
                return zdt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
        # Zope DateTime tiene ISO8601()
        try:
            return dt.ISO8601()
        except Exception:
            return _u(dt)

    def _fmt_local(self, dt):
        if not dt:
            return u"—"
        # bika api
        if api:
            try:
                return api.to_localized_time(dt)
            except Exception:
                pass
        # Plone helper
        try:
            return self.context.toLocalizedTime(dt)
        except Exception:
            pass
        try:
            # último recurso
            return _u(dt)
        except Exception:
            return u"—"

    def _date_of_ar(self, ar):
        # PRIORIDAD: Verificado -> Publicado -> Recepción -> creado
        for g in ("getDateVerified", "getDatePublished", "getDateReceived", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    def _received_date_of_ar(self, ar):
        # Para definir el "previo global" por RECEPCIÓN
        for g in ("getDateReceived", "getReceptionDate", "getSamplingDate", "created"):
            v = self._get(ar, g)
            if v:
                return v
        return None

    # ------------------ AR / Paciente ------------------
    def _patient_obj(self, ar):
        for pa in ("getPatient", "Patient", "getRelatedPatient"):
            if hasattr(ar, pa):
                try:
                    p = getattr(ar, pa)()
                    if p:
                        return p
                except Exception:
                    pass
        return None

    def _mrn_of_ar(self, ar, patient):
        # Mismos nombres que el template INFOLABSA.pt
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

    def _patient_keys(self, ar, patient):
        """Llaves para identificar paciente (MRN preferente + nombre full)."""
        keys = {}
        mrn = self._mrn_of_ar(ar, patient)
        if mrn:
            keys["mrn"] = mrn

        # Nombre completo como fallback
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

    # ------------------ Analito keys ------------------
    def _service_of(self, a):
        try:
            return getattr(a, "getService", lambda: None)()
        except Exception:
            return None

    def _service_code(self, svc):
        # Campos comunes para identificar el "código" del servicio
        for g in ("getAnalysisCode", "getCode", "getServiceID", "getId", "id"):
            v = self._get(svc, g)
            if v:
                return _u(v)
        return None

    def _analysis_keys(self, a):
        svc = self._service_of(a)
        svc_uid = self._get(svc, "UID")
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

    def _unit_of(self, a):
        for g in ("getUnit", "Unit", "getUnitAbbreviation"):
            v = self._get(a, g)
            if v:
                return _u(v)
        return u""

    def _result_value(self, a):
        for g in ("getFormattedResult", "getResult", "Result", "result", "getValue"):
            v = self._get(a, g)
            # Mantener primero el formateado como "raw"; numérico para cálculo
            if v not in (None, u"", ""):
                return v, _to_num(v)
        return u"—", None

    # ------------------ Búsqueda de AR previos ------------------
    def _same_patient(self, other_ar, pkeys):
        """Fallback programático si falta el índice MRN."""
        # MRN-like en el AR other
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
                return True

        # Nombre completo como último recurso
        full = None
        for name in ("getPatientFullName", "getFullname", "Title"):
            v = self._get(other_ar, name)
            if v:
                full = _norm(v)
                break
        if full and pkeys.get("fullname") and full == pkeys["fullname"]:
            return True

        return False

    def _candidate_ars(self, current_ar, patient, pkeys):
        """
        Saca los AR del mismo paciente dentro del período:
        - Si existe índice 'medical_record_number', se usa directamente.
        - Si no, se hace fallback programático (últimos N) + filtro por paciente.
        - Se quedan sólo AR en estados >= verified.
        """
        cat = self._cat()
        cur_uid = self._get(current_ar, "UID")

        # Límite por periodo
        from DateTime import DateTime as ZDT
        try:
            cutoff = ZDT() - self.PERIOD_DAYS  # días atrás
        except Exception:
            cutoff = None

        brains = []
        mrn = pkeys.get("mrn")
        has_mrn_index = self._catalog_has_index("medical_record_number", analyses=False)

        try:
            if mrn and has_mrn_index:
                query = {
                    "portal_type": "AnalysisRequest",
                    "medical_record_number": mrn,
                    "sort_on": "created",
                    "sort_order": "descending",
                }
                if cutoff and self._catalog_has_index("created", analyses=False):
                    query["created"] = {"query": cutoff, "range": "min"}
                brains = cat.searchResults(**query)
            else:
                # Fallback: trae un conjunto amplio y filtra a mano
                brains = cat.searchResults(
                    portal_type="AnalysisRequest",
                    sort_on="created",
                    sort_order="descending",
                )[:500]
        except Exception:
            brains = []

        out = []
        seen = set([cur_uid])

        for b in brains:
            if getattr(b, "UID", None) in seen:
                continue

            # Estado según brain
            try:
                b_state = getattr(b, "review_state", None)
                if b_state and _u(b_state) not in self.STATES_OK:
                    continue
            except Exception:
                pass

            # Periodo en fallback (si no pudimos filtrar arriba)
            if not (mrn and has_mrn_index) and cutoff and getattr(b, "created", None):
                try:
                    if b.created < cutoff:
                        continue
                except Exception:
                    pass

            try:
                obj = b.getObject()
            except Exception:
                continue

            # Estado por objeto si no venía en brain
            if not getattr(b, "review_state", None):
                st = self._state_of(obj)
                if st and st not in self.STATES_OK:
                    continue

            # Si no tenemos índice MRN, filtra por paciente programáticamente
            if not (mrn and has_mrn_index):
                if not self._same_patient(obj, pkeys):
                    continue

            out.append(obj)
            seen.add(b.UID)

        return out

    # ------------------ helpers para previo global ------------------
    def _choose_prev_ar_global(self, current_ar, candidate_ars):
        """Devuelve el AR previo (mismo paciente) por FECHA DE RECEPCIÓN inmediatamente anterior al actual."""
        cur_recv = self._received_date_of_ar(current_ar)
        if not cur_recv:
            return None

        dated = []
        for ar in candidate_ars:
            dt = self._received_date_of_ar(ar)
            if not dt:
                continue
            try:
                if dt >= cur_recv:
                    continue
            except Exception:
                continue
            st = self._state_of(ar)
            if st and _u(st) not in self.STATES_OK:
                continue
            dated.append((dt, ar))

        if not dated:
            return None

        dated.sort(key=lambda x: x[0])
        return dated[-1][1]

    def _find_prev_value_in_ar(self, prev_ar, target_keys):
        """Busca el valor del analito en el AR previo global usando coincidencia robusta."""
        if not prev_ar:
            return (u"—", None)

        # claves del analito actual
        tgt_svc_uid = (target_keys or {}).get("svc_uid")
        tgt_kw      = (target_keys or {}).get("keyword")
        tgt_code    = (target_keys or {}).get("code")
        tgt_title_n = _norm((target_keys or {}).get("title"))
        tgt_name_n  = _norm((target_keys or {}).get("name"))
        tgt_unit_n  = _norm((target_keys or {}).get("unit"))

        analyses = self._analyses_of(prev_ar)

        def _service_code(svc):
            for g in ("getAnalysisCode", "getCode", "getServiceID", "getId", "id"):
                v = self._get(svc, g)
                if v:
                    return _u(v)
            return None

        def _kw_of(a):
            # keyword puede estar en el service o en el analysis
            svc = self._service_of(a)
            return (self._get(svc, "getKeyword") or self._get(a, "getKeyword"))

        def _match_and_return(a):
            raw, num = self._result_value(a)
            if (num is not None) or (raw not in (None, u"", "")):
                return (raw if raw not in (None, u"", "") else u"—", num)
            return None

        # 1) UID del servicio
        if tgt_svc_uid:
            for a in analyses:
                svc = self._service_of(a)
                svuid = self._get(svc, "UID")
                if svuid and _u(svuid) == tgt_svc_uid:
                    r = _match_and_return(a);  return r if r else (u"—", None)

        # 2) keyword (service o analysis)
        if tgt_kw:
            for a in analyses:
                kw_other = _kw_of(a)
                if kw_other and _u(kw_other).strip().lower() == _u(tgt_kw).strip().lower():
                    r = _match_and_return(a);  return r if r else (u"—", None)

        # 3) código del servicio
        if tgt_code:
            for a in analyses:
                code_other = _service_code(self._service_of(a))
                if code_other and _norm(code_other) == _norm(tgt_code):
                    r = _match_and_return(a);  return r if r else (u"—", None)

        # 4) título normalizado
        if tgt_title_n:
            for a in analyses:
                t_other = self._title_of(self._service_of(a)) if self._service_of(a) else (self._get(a, "Title") or u"")
                if _norm(t_other) == tgt_title_n:
                    r = _match_and_return(a);  return r if r else (u"—", None)

        # 5) nombre + unidad (fallback muy laxo)
        for a in analyses:
            unit_o = self._unit_of(a)
            name_o = self._title_of(self._service_of(a)) if self._service_of(a) else (self._get(a, "Title") or u"")
            if _norm(name_o) == tgt_name_n and _norm(unit_o) == tgt_unit_n:
                r = _match_and_return(a);  return r if r else (u"—", None)

        return (u"—", None)

    # ------------------ Serie por analito (para chispa) ------------------
    def _series_for_uid(self, ars, analito_uid, keyword, title):
        """
        Construye la serie (date,value,raw,ar,rid) del analito:
        - Filtra analyses por getRequestID ∈ IDs de AR candidatos
        - y por getKeyword (o getServiceUID si quisieras).
        - Usa fecha del AR (verificada->publicada->recepción->creado).
        - Deduplica por rid quedándote con el punto más reciente por AR.
        """
        acat = self._acat()

        # Mapa: RequestID -> (AR, fecha del AR)
        ar_by_id = {}
        ar_ids = []
        for ar in ars:
            rid = self._get(ar, "getRequestID") or self._get(ar, "getId")
            if not rid:
                continue
            ar_ids.append(rid)
            ar_by_id[rid] = (ar, self._date_of_ar(ar))

        if not ar_ids:
            return []

        query = {
            "portal_type": "Analysis",
            "getRequestID": ar_ids,
        }

        # Si hay índice de keyword, úsalo (más directo y robusto)
        if keyword and self._catalog_has_index("getKeyword", analyses=True):
            query["getKeyword"] = keyword
        # Alternativa opcional: por UID de servicio (descomentable si lo prefieres)
        # elif analito_uid and self._catalog_has_index("getServiceUID", analyses=True) and not analito_uid.startswith("kw:"):
        #     query["getServiceUID"] = analito_uid

        sort_on = "getResultCaptureDate" if self._catalog_has_index("getResultCaptureDate", analyses=True) else "created"

        try:
            abrains = acat.searchResults(sort_on=sort_on, sort_order="ascending", **query)
        except Exception:
            abrains = []

        pts = []
        ok_states = self.STATES_OK

        for ab in abrains:
            try:
                astate = getattr(ab, "review_state", None)
                if astate and _u(astate) not in ok_states:
                    continue
            except Exception:
                pass

            try:
                aobj = ab.getObject()
            except Exception:
                continue

            raw_val, fval = self._result_value(aobj)
            if fval is None:
                continue

            rid = getattr(ab, "getRequestID", None)
            rid = rid() if callable(rid) else rid
            ar_ref, ar_dt = ar_by_id.get(rid, (None, None))

            dt = ar_dt or getattr(ab, "getResultCaptureDate", None) or getattr(ab, "created", None)
            if not dt:
                continue

            iso = self._iso(dt)
            pts.append({"date": iso, "value": fval, "raw": raw_val, "ar": ar_ref or self.context, "rid": rid})

        if not pts:
            # fallback: inspección directa del AR actual
            cur_ar = self.context
            cur_rid = self._get(cur_ar, "getRequestID") or self._get(cur_ar, "getId")
            dt = self._date_of_ar(cur_ar)
            for a in self._analyses_of(cur_ar):
                keys = self._analysis_keys(a)
                ok = False
                if analito_uid and keys["uid"] == analito_uid:
                    ok = True
                elif keyword and keys["keyword"] and keys["keyword"].lower() == (keyword or "").lower():
                    ok = True
                elif title and keys["title"] and keys["title"].lower() == (title or "").lower():
                    ok = True
                if not ok:
                    continue
                raw, f = self._result_value(a)
                if f is None:
                    continue
                iso = self._iso(dt)
                pts.append({"date": iso, "value": f, "raw": raw, "ar": cur_ar, "rid": cur_rid})
                break

        # Deduplicar por rid conservando el punto más reciente por AR
        by_rid = {}
        for p in pts:
            rid = p.get("rid")
            if not rid:
                continue
            prev = by_rid.get(rid)
            if prev is None or p["date"] > prev["date"]:
                by_rid[rid] = p

        pts = list(by_rid.values())
        pts.sort(key=lambda p: p["date"])

        # Recorte a los últimos MAX_POINTS
        if len(pts) > self.MAX_POINTS:
            pts = pts[-self.MAX_POINTS:]

        return pts

    # ------------------ Tendencia (flecha) ------------------
    def _trend_dir(self, series_points):
        """
        Devuelve '▲', '▼' o '∙' según la tendencia de la serie.
        - Si hay >= 3 puntos: usa pendiente de regresión lineal (t vs y).
        - Si hay 2 puntos: compara primero vs último.
        - Umbral de "plano": |pendiente relativa| < 0.5% del rango por unidad de tiempo.
        """
        try:
            n = len(series_points or [])
            if n < 2:
                return u"∙"
            # convertir fechas ISO a t numérico (días)
            import datetime
            def _to_ts_days(iso):
                # Date.parse en JS usa ms; aquí usamos días (UTC) para estabilidad
                try:
                    dt = datetime.datetime.strptime(iso.replace("Z",""), "%Y-%m-%dT%H:%M:%S")
                except Exception:
                    # fallback: solo fecha
                    try:
                        dt = datetime.datetime.strptime(iso[:10], "%Y-%m-%d")
                    except Exception:
                        return None
                return (dt - datetime.datetime(1970,1,1)).total_seconds() / 86400.0

            pts = [( _to_ts_days(p["date"]), float(p["value"]) ) for p in series_points if p.get("date") and p.get("value") is not None]
            pts = [p for p in pts if p[0] is not None]
            if len(pts) < 2:
                return u"∙"

            if len(pts) == 2:
                return u"▲" if pts[-1][1] > pts[0][1] else (u"▼" if pts[-1][1] < pts[0][1] else u"∙")

            # regresión lineal simple
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            xbar = sum(xs) / float(len(xs))
            ybar = sum(ys) / float(len(ys))
            sxy = sum((x - xbar)*(y - ybar) for x,y in pts)
            sxx = sum((x - xbar)*(x - xbar) for x in xs) or 1.0
            slope = sxy / sxx  # unidades: valor / día

            # Normalizar: si el rango temporal es pequeño, ignora ruido
            t_span = max(xs) - min(xs) or 1.0
            y_span = max(ys) - min(ys) or 1.0
            rel = abs(slope) * t_span / y_span  # cuánto cambia relativo al rango en el periodo
            if rel < 0.005:  # <0.5% del rango en todo el periodo => plano
                return u"∙"
            return u"▲" if slope > 0 else u"▼"
        except Exception:
            return u"∙"

    # ------------------ Vista ------------------
    def __call__(self):
        ar = self.context
        patient = self._patient_obj(ar)
        pkeys = self._patient_keys(ar, patient)

        # AR previos del mismo paciente (por MRN si hay índice; si no, fallback)
        prev_ars = self._candidate_ars(ar, patient, pkeys)

        # Elegir el AR previo GLOBAL por FECHA DE RECEPCIÓN inmediatamente anterior
        prev_ar_global = self._choose_prev_ar_global(ar, prev_ars)

        # Serie (sparklines) se arma con [previos + actual] pero no define el "previo"
        ars_for_series = list(prev_ars) + [ar]

        rows = []
        multi_series = []  # para el gráfico grande opcional
        now_analyses = self._analyses_of(ar)
        for a in now_analyses:
            keys = self._analysis_keys(a)
            raw_now, val_now = self._result_value(a)

            # Serie histórica para chispa / tendencia
            series = self._series_for_uid(ars_for_series, keys["uid"], keys["keyword"], keys["title"])

            # Valor PREVIO tomado EXCLUSIVAMENTE del AR previo global
            prev_raw, prev_num = self._find_prev_value_in_ar(prev_ar_global, keys)

            # Reglas de visibilidad: solo filas con al menos 2 puntos y valor actual numérico
            if len(series) < 2 or val_now is None:
                # aun así, recolecta multi_series para el gráfico grande si quieres mostrar tendencias
                if len(series) >= 2:
                    multi_series.append({"name": keys["name"], "unit": keys["unit"] or u"", "series": [{'date': p['date'], 'value': p['value']} for p in series]})
                continue

            # Δ (absoluto y %)
            delta_abs = None
            delta_pct = u"N/A"
            delta_dir = u"∙"

            if prev_num is not None:
                try:
                    delta_abs = float(val_now) - float(prev_num)
                    if prev_num != 0:
                        pct = ((float(val_now) - float(prev_num)) / abs(float(prev_num))) * 100.0
                        delta_pct = u"%.1f%%" % pct
                    if float(val_now) > float(prev_num):
                        delta_dir = u"▲"
                    elif float(val_now) < float(prev_num):
                        delta_dir = u"▼"
                    else:
                        delta_dir = u"Δ"
                except Exception:
                    pass

            # Datos del AR previo global (ID/fecha) — si existe
            prev_id = u"—"
            prev_date = u"—"
            prev_date_fmt = u"—"
            if prev_ar_global:
                prev_id = (self._get(prev_ar_global, "getRequestID") or
                           self._get(prev_ar_global, "getId") or u"—")
                pdt = self._date_of_ar(prev_ar_global)
                prev_date = self._iso(pdt) if pdt else u"—"
                prev_date_fmt = self._fmt_local(pdt) if pdt else u"—"

            # Δ formateado
            delta_abs_fmt = u"—"
            try:
                if delta_abs is not None:
                    delta_abs_fmt = u"{:+.2f}".format(delta_abs)
            except Exception:
                pass
            delta_combo_fmt = (u"%s (%s)" % (delta_abs_fmt, delta_pct)
                               if delta_abs is not None
                               else u"—")

            # Tendencia por serie
            trend_dir = self._trend_dir(series)

            # Valor previo "raw" para mostrar encima de ID/fecha
            prev_value_raw = prev_raw if prev_raw not in (None, u"", "") else (u"%s" % prev_num if prev_num is not None else u"—")

            rows.append({
                'uid': keys["uid"],
                'name': keys["name"],
                'unit': keys["unit"] or u'',
                'value_now': (raw_now if raw_now not in (None, u"", "") else u'—'),

                'delta_pct': delta_pct,
                'delta_dir': delta_dir,
                'delta_abs_fmt': delta_abs_fmt,      # p.ej. +115.00
                'delta_combo_fmt': delta_combo_fmt,  # p.ej. +115.00 (85.2%)
                'delta_note': u'',

                'prev_sample_id': prev_id,
                'prev_date': prev_date,              # ISO (para debug/JSON)
                'prev_date_fmt': prev_date_fmt,      # Localizado (para el PDF)
                'prev_value': prev_value_raw,        # Valor previo (raw) para mostrar en la plantilla

                'rcv_pct': None,                     # Oculto por ahora en la plantilla

                'trend_dir': trend_dir,              # ▲ / ▼ / ∙

                'series': [{'date': p['date'], 'value': p['value']} for p in series if p.get('value') is not None],
            })

            # Acumular para gráfico grande
            multi_series.append({
                "name": keys["name"],
                "unit": keys["unit"] or u"",
                "series": [{'date': p['date'], 'value': p['value']} for p in series]
            })

        label = u'%d meses' % (self.PERIOD_DAYS // 30)

        # Señal para la plantilla: ¿hay material para gráfico grande?
        has_chart = bool([s for s in multi_series if len(s.get("series") or []) >= 2])

        return {
            'period_label': label,
            'rows': rows,
            # para el gráfico multi-analito opcional debajo del panel
            'chart': {
                'series': multi_series,
                'max_points': self.MAX_POINTS,
                'window_days': self.PERIOD_DAYS,
            },
            'has_chart': has_chart,
        }
