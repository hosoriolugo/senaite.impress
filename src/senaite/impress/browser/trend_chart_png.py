# -*- coding: utf-8 -*-
from Products.Five import BrowserView
from zope.component.hooks import getSite
from ZODB.POSException import POSKeyError
from StringIO import StringIO
import math
import datetime
import colorsys  # NUEVO

try:
    # Pillow (normalmente ya viene en la build de Plone/SENAITE)
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except Exception:
    PIL_OK = False


def _to_int(s, default):
    try:
        return int(s)
    except Exception:
        return default


def _parse_ms(x):
    # x puede venir como int ms, int sec, ISO string
    try:
        # número
        n = long(x)
        # si parece segundos (10 dígitos): a ms
        if len(str(abs(n))) <= 10:
            return n * 1000
        return n
    except Exception:
        # cadena
        try:
            # reemplazo para "YYYY-MM-DD HH:MM" sin "T"
            if ' ' in x and 'T' not in x:
                x = x.replace(' ', 'T')
            # Date.parse equivalente simple:
            from DateTime import DateTime
            # DateTime parsea muchos formatos y da epoch en s
            return long(DateTime(x).timeTime() * 1000.0)
        except Exception:
            return None


def _safe_text(val):
    try:
        if isinstance(val, unicode):
            return val
        if isinstance(val, str):
            return val.decode('utf-8', 'ignore')
        return unicode(val)
    except Exception:
        return u''


def _hsv_palette(n):
    """Genera n colores distinguibles en RGB (0..255) usando HSV."""
    out = []
    for i in range(max(1, n)):
        h = float(i) / float(max(1, n))
        s, v = 0.70, 0.85
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


class TrendChartPNG(BrowserView):
    """Devuelve un PNG con líneas de tendencia a partir de @@infolabsa-delta-check"""

    PALETTE = [
        (52, 58, 64),    # gris oscuro
        (13, 110, 253),  # azul
        (253, 126, 20),  # naranja
        (25, 135, 84),   # verde
        (220, 53, 69),   # rojo
        (111, 66, 193),  # púrpura
    ]

    def _get_ar(self, uid):
        # Si llega con context=AR ya sirve; si no, intenta buscar por catálogo
        ar = getattr(self, 'context', None)
        if uid:
            try:
                site = getSite()
                catalog = site.portal_catalog
                brains = catalog(UID=uid)
                if brains:
                    ar = brains[0].getObject()
            except Exception:
                pass
        return ar

    def _get_chartdata(self, ar):
        # Usa el flow ya existente
        if not ar:
            return {}
        trv = getattr(ar, 'restrictedTraverse', None)
        if not trv:
            return {}
        try:
            delta = ar.restrictedTraverse('@@infolabsa-delta-check')() or {}
        except (POSKeyError, Exception):
            return {}
        chart = delta.get('chart_v2') or delta.get('chart') or {}
        # Normaliza a: [{'name':u'...', 'unit':u'..', 'data':[[ms, y], ...]}, ...]
        series = []
        for s in (chart.get('series') or []):
            name = _safe_text(s.get('name') or u'Serie')
            unit = _safe_text(s.get('unit') or u'')
            data = []
            for p in (s.get('data') or []):
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    ms, y = p[0], p[1]
                elif isinstance(p, dict) and 'x' in p and 'y' in p:
                    ms, y = p.get('x'), p.get('y')
                else:
                    continue
                ms = _parse_ms(ms)
                try:
                    y = float(y)
                except Exception:
                    continue
                if ms is not None:
                    data.append([ms, y])
            if data:
                # ordena por X por si acaso
                data.sort(key=lambda r: r[0])
                series.append({'name': name, 'unit': unit, 'data': data})
        return {'series': series}

    def _nice_range(self, vmin, vmax):
        """Redondea mín/máx a números 'bonitos' y devuelve ticks"""
        if vmin == vmax:
            vmin -= 1.0
            vmax += 1.0
        rng = vmax - vmin
        if rng <= 0:
            rng = 1.0
        raw_step = rng / 5.0
        mag = 10 ** int(math.floor(math.log10(raw_step)))
        norm = raw_step / mag
        if   norm < 1.5: step = 1 * mag
        elif norm < 3:   step = 2 * mag
        elif norm < 7:   step = 5 * mag
        else:            step = 10 * mag
        ymin = math.floor(vmin / step) * step
        ymax = math.ceil(vmax / step) * step
        ticks = []
        t = ymin
        i = 0
        while t <= ymax and i < 15:
            ticks.append(t)
            t += step
            i += 1
        return ymin, ymax, ticks

    # === Timestamp del AR actual (ms) para recorte por "hasta este análisis"
    def _ar_cutoff_ms(self, ar):
        get = getattr
        dt = (
            (get(ar, 'getDateVerified', None) and ar.getDateVerified()) or
            (get(ar, 'getDatePublished', None) and ar.getDatePublished()) or
            (get(ar, 'getDateReceived', None) and ar.getDateReceived()) or
            getattr(ar, 'created', None)
        )
        if not dt:
            return None
        try:
            from DateTime import DateTime as ZDT
            if isinstance(dt, ZDT):
                return long(dt.timeTime() * 1000.0)
        except Exception:
            pass
        try:
            if hasattr(dt, 'timetuple'):
                return long((dt - datetime.datetime(1970, 1, 1)).total_seconds() * 1000.0)
        except Exception:
            pass
        try:
            if isinstance(dt, tuple) and len(dt) >= 6:
                return long(datetime.datetime(dt[0], dt[1], dt[2], dt[3], dt[4], dt[5]).strftime('%s')) * 1000
        except Exception:
            pass
        return None

    def __call__(self):
        # --- Parámetros ---
        request = self.request
        uid = request.get('uid') or request.form.get('uid')
        rid = request.get('rid') or request.form.get('rid')  # aceptado, opcional
        max_points = max(1, _to_int(request.get('max_points', request.form.get('max_points', 6)), 6))
        days = max(1, _to_int(request.get('days', request.form.get('days', 365)), 365))
        show_note = request.get('note', request.form.get('note', '1'))
        show_note = False if str(show_note) in ('0', 'false', 'False') else True

        # Lee params (w/h pueden venir o ser adaptativos)
        W_param = request.get('w')
        H_param = request.get('h')
        dpi = max(96, _to_int(request.get('dpi', 300), 300))  # 300 por defecto
        scale = max(1, min(4, _to_int(request.get('scale', 3), 3)))  # supermuestreo x3 por defecto

        # Paddings base (se ajustarán dinámicamente)
        base_pad_l = 68
        base_pad_r = 24
        base_pad_t = 12   # más chico: el padding real se recalcula con leyenda/título/nota
        base_pad_b = 56   # más espacio para etiquetas X
        bg = (255, 255, 255)
        grid = (230, 236, 240)
        axis = (173, 181, 189)

        if not PIL_OK:
            self.request.response.setHeader('Content-Type', 'text/plain; charset=utf-8')
            return u'Pillow no disponible: no se puede generar el PNG en el servidor.'

        # --- Datos ---
        ar = self._get_ar(uid)
        chart = self._get_chartdata(ar)
        series = chart.get('series') or []

        # Recorte por cutoff (hasta AR actual) + ventana + límite de puntos
        cutoff_ms = self._ar_cutoff_ms(ar)
        if cutoff_ms is None:
            try:
                cutoff_ms = max([pt[0] for s in series for pt in (s.get('data') or [])])
            except Exception:
                cutoff_ms = None
        if series:
            millis_window = days * 24 * 60 * 60 * 1000L
            new_series = []
            for s in series:
                pts = list(s.get('data') or [])
                if cutoff_ms is not None:
                    pts = [p for p in pts if p[0] <= cutoff_ms and p[0] >= (cutoff_ms - millis_window)]
                if len(pts) > max_points:
                    pts = pts[-max_points:]
                if pts:
                    s2 = dict(s); s2['data'] = pts
                    new_series.append(s2)
            series = new_series

        # Canvas: W/H adaptativos si no llegan
        pts_per_series = [len(s['data']) for s in series] or [0]
        pts_max = max(pts_per_series)
        n_series = len(series)

        if W_param is None or H_param is None:
            if pts_max <= 10:
                px_per_point = 70
            elif pts_max <= 16:
                px_per_point = 56
            elif pts_max <= 24:
                px_per_point = 48
            else:
                px_per_point = 40
            W = max(1200, min(2400, max(1000, pts_max * px_per_point)))
            legend_rows_est = int(math.ceil(max(1, n_series) / 6.0))
            extra_h = max(0, (legend_rows_est - 1) * 26)
            H = max(360, min(820, 380 + extra_h))
        else:
            W = max(600, _to_int(W_param, 1000))
            H = max(240, _to_int(H_param, 360))

        # === Supermuestreo
        W2, H2 = W * scale, H * scale
        pad_l2, pad_r2 = base_pad_l * scale, base_pad_r * scale
        pad_b2 = base_pad_b * scale  # top dinámico

        im = Image.new('RGB', (W2, H2), bg)
        dr = ImageDraw.Draw(im)

        # Fuentes
        def _load_font(sz):
            try:
                return ImageFont.truetype('DejaVuSans.ttf', sz)
            except Exception:
                try:
                    return ImageFont.truetype('arial.ttf', sz)
                except Exception:
                    return ImageFont.load_default()

        font       = _load_font(int(11 * scale))
        font_small = _load_font(int(10 * scale))
        font_title = _load_font(int(12 * scale))

        # Mensaje si no hay datos
        if not series or not any(s.get('data') for s in series):
            msg = u'Sin datos para gráfico'
            tw, th = dr.textsize(msg, font=font)
            dr.text(((W2 - tw) / 2, (H2 - th) / 2), msg, fill=(100, 100, 100), font=font)
            out = StringIO()
            (im.resize((W, H), Image.LANCZOS) if scale > 1 else im).save(out, format='PNG', dpi=(dpi, dpi))
            self.request.response.setHeader('Content-Type', 'image/png')
            return out.getvalue()

        # Extremos globales (tras recorte)
        xs, ys = [], []
        for s in series:
            for ms, y in s['data']:
                xs.append(ms); ys.append(y)
        xmin = min(xs); xmax = max(xs)
        ymin = min(ys); ymax = max(ys)
        ymin, ymax, yticks = self._nice_range(ymin, ymax)
        if xmin == xmax:
            xmin -= 1000; xmax += 1000

        # --- CALCULO DE LAYOUT SUPERIOR (leyenda + título + nota) PARA EVITAR SOLAPES ---
        # Paleta
        palette = list(self.PALETTE) if n_series <= len(self.PALETTE) else _hsv_palette(n_series)

        # Simulación de leyenda para obtener alto total
        leg_x = pad_l2
        leg_y = int(6 * scale)
        space_x = int(24 * scale)
        box_w = int(14 * scale)
        box_h = int(6 * scale)
        gap   = int(6  * scale)

        # Medimos sin dibujar
        sim_leg_x, sim_leg_y, last_lth = leg_x, leg_y, int(12*scale)
        for idx, s in enumerate(series):
            name = s['name']; unit = s.get('unit') or u''
            label = name + (unit and (u' (' + unit.strip() + u')') or u'')
            ltw, lth = dr.textsize(label, font=font); last_lth = lth
            sim_leg_x += box_w + gap + ltw + space_x
            if sim_leg_x > W2 - pad_r2 - int(120*scale):
                sim_leg_x = pad_l2
                sim_leg_y += lth + int(6 * scale)
        legend_total_h = (sim_leg_y - leg_y) + last_lth + int(6 * scale)

        # Título y nota
        title = u'Tendencias de Resultados'
        ttw, tth = dr.textsize(title, font=font_title)
        note_txt = u'Se muestran hasta 6 puntos dentro de los últimos 365 días (máximo).'
        ntw, nth = dr.textsize(note_txt, font=font_small) if show_note else (0, 0)

        # Padding superior real
        pad_t2 = (base_pad_t * scale) + legend_total_h + tth + (nth if show_note else 0) + int(14 * scale)

        # Ahora sí: área de trazado
        plot_w2 = W2 - pad_l2 - pad_r2
        plot_h2 = H2 - pad_t2 - pad_b2

        # --- DIBUJO DEL HEADER DEL GRÁFICO (leyenda + título + nota) ---
        # Leyenda (pintamos)
        leg_x = pad_l2
        leg_y = int(6 * scale)
        for idx, s in enumerate(series):
            color = palette[idx % len(palette)]
            name = s['name']; unit = s.get('unit') or u''
            label = name + (unit and (u' (' + unit.strip() + u')') or u'')
            dr.rectangle([leg_x, leg_y + int(3*scale), leg_x + box_w, leg_y + int(3*scale) + box_h], fill=color, outline=color)
            dr.text((leg_x + box_w + gap, leg_y), label, fill=(0, 0, 0), font=font)
            ltw, lth = dr.textsize(label, font=font)
            leg_x += box_w + gap + ltw + space_x
            if leg_x > W2 - pad_r2 - int(120*scale):
                leg_x = pad_l2
                leg_y += lth + int(6 * scale)

        # Título
        title_y = leg_y + int(6 * scale) + last_lth
        dr.text((pad_l2, title_y), title, fill=(0, 0, 0), font=font_title)

        # Nota
        if show_note:
            dr.text((pad_l2, title_y + tth + int(2 * scale)), note_txt, fill=(108, 117, 125), font=font_small)

        # --- Ejes y grilla (ahora debajo del header calculado) ---
        def sx(ms):
            return pad_l2 + int((ms - xmin) * 1.0 * plot_w2 / (xmax - xmin))

        def sy(y):
            return pad_t2 + int((ymax - y) * 1.0 * plot_h2 / (ymax - ymin))

        lw = max(1, int(2 * scale))
        # Grid Y + etiquetas Y
        for t in yticks:
            ypix = sy(t)
            dr.line([(pad_l2, ypix), (W2 - pad_r2, ypix)], fill=grid)
            lab = (u'%0.2f' % t).rstrip('0').rstrip('.')
            tw, th = dr.textsize(lab, font=font_small)
            dr.text((pad_l2 - int(8*scale) - tw, ypix - th / 2), lab, fill=(108, 117, 125), font=font_small)
        # Ejes y marco
        dr.line([(pad_l2, pad_t2), (pad_l2, H2 - pad_b2)], fill=axis, width=lw)
        dr.line([(pad_l2, H2 - pad_b2), (W2 - pad_r2, H2 - pad_b2)], fill=axis, width=lw)
        dr.rectangle([(pad_l2, pad_t2), (W2 - pad_r2, H2 - pad_b2)], outline=(222, 226, 230))

        # Etiquetas X: bajo cada punto real si ≤6, si no, muestrear a 6
        xs_unique = sorted(set(xs))
        if len(xs_unique) <= 6:
            xticks = xs_unique
        else:
            step = float(len(xs_unique) - 1) / 5.0
            xticks = [xs_unique[int(round(i * step))] for i in range(6)]
        for ms in xticks:
            xpix = sx(ms)
            try:
                dt = datetime.datetime.utcfromtimestamp(ms / 1000.0)
                lab = dt.strftime('%d/%m %H:%M')
            except Exception:
                lab = unicode(ms)
            tw, th = dr.textsize(lab, font=font_small)
            dr.text((xpix - tw / 2, H2 - pad_b2 + int(4*scale)), lab, fill=(108, 117, 125), font=font_small)

        # Dibujo de series + etiquetas de valor (con unidad)
        for idx, s in enumerate(series):
            color = palette[idx % len(palette)]
            pts = s['data']
            unit = s.get('unit') or u''

            last = None
            for (ms, y) in pts:
                xpix = sx(ms); ypix = sy(y)
                if last is not None:
                    dr.line([last, (xpix, ypix)], fill=color, width=lw)
                last = (xpix, ypix)

            if last:
                r = max(2, int(3 * scale))
                dr.ellipse([last[0] - r, last[1] - r, last[0] + r, last[1] + r], fill=color, outline=color)

            if pts:
                if len(pts) <= 12:
                    label_every = 1
                elif len(pts) <= 24:
                    label_every = 2
                else:
                    label_every = 3
                for i, (ms, y) in enumerate(pts):
                    if (i % label_every != 0) and (i != len(pts) - 1):
                        continue
                    xpix = sx(ms); ypix = sy(y)
                    val_txt = (u'%0.2f' % y).rstrip('0').rstrip('.')
                    txt = val_txt + (unit and (u' ' + unit) or u'')
                    for dx, dy in ((-1,0),(1,0),(0,-1),(0,1)):
                        dr.text((xpix + dx + int(4*scale), ypix - int(12*scale) + dy), txt, fill=(255,255,255), font=font_small)
                    dr.text((xpix + int(4*scale), ypix - int(12*scale)), txt, fill=(0,0,0), font=font_small)

        # Salida con reducción LANCZOS para máxima nitidez
        out = StringIO()
        (im.resize((W, H), Image.LANCZOS) if scale > 1 else im).save(out, format='PNG', dpi=(dpi, dpi))
        self.request.response.setHeader('Content-Type', 'image/png')
        return out.getvalue()


# ===== View que devuelve Data-URI para WeasyPrint =====
class InfolabsaTrendChartDataURI(BrowserView):
    """Devuelve data:image/png;base64,... invocando el view PNG internamente (sin HTTP)."""

    def __call__(self, uid=None, rid=None, w=None, h=None, dpi=None, scale=None, max_points=None, days=None, note=None):
        request = self.request
        # Recuperar parámetros
        uid = uid or request.get('uid') or request.form.get('uid')
        rid = rid or request.get('rid') or request.form.get('rid')
        w = w or request.get('w') or request.form.get('w')
        h = h or request.get('h') or request.form.get('h')
        dpi = dpi or request.get('dpi') or request.form.get('dpi')
        scale = scale or request.get('scale') or request.form.get('scale')
        max_points = max_points or request.get('max_points') or request.form.get('max_points')
        days = days or request.get('days') or request.form.get('days')
        note = note or request.get('note') or request.form.get('note')

        # Guardar cabeceras previas para restaurar luego
        resp = request.response
        prev_ct = resp.getHeader('Content-Type')
        prev_cd = resp.getHeader('Content-Disposition')

        # Inyectar parámetros al request.form para el sub-view
        if uid is not None:   request.form['uid'] = uid
        if rid is not None:   request.form['rid'] = rid
        if w is not None:     request.form['w'] = str(w)
        if h is not None:     request.form['h'] = str(h)
        if dpi is not None:   request.form['dpi'] = str(dpi)
        if scale is not None: request.form['scale'] = str(scale)
        if max_points is not None: request.form['max_points'] = str(max_points)
        if days is not None:       request.form['days'] = str(days)
        if note is not None:       request.form['note'] = str(note)

        try:
            png_view = self.context.restrictedTraverse('@@infolabsa-trend-chart.png')
            data = png_view()
        finally:
            if prev_ct: resp.setHeader('Content-Type', prev_ct)
            else:       resp.setHeader('Content-Type', None)
            if prev_cd: resp.setHeader('Content-Disposition', prev_cd)
            else:       resp.setHeader('Content-Disposition', None)

        if not data:
            resp.setHeader('Content-Type', 'text/plain; charset=utf-8')
            return u''

        # Asegurar bytes y construir Data-URI
        try:
            from base64 import b64encode
            if isinstance(data, unicode):
                data = data.encode('utf-8', 'ignore')
            b64 = b64encode(data)
            datauri = 'data:image/png;base64,' + b64
        except Exception:
            resp.setHeader('Content-Type', 'text/plain; charset=utf-8')
            return u''

        resp.setHeader('Content-Type', 'text/plain; charset=utf-8')
        return datauri
