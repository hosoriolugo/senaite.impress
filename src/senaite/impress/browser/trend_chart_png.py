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

    def __call__(self):
        # --- Parámetros ---
        request = self.request
        uid = request.get('uid') or request.form.get('uid')

        # Lee params pero NO fija aún w/h si no vienen
        W_param = request.get('w')
        H_param = request.get('h')
        dpi = max(96, _to_int(request.get('dpi', 300), 300))  # 300 por defecto

        # Paddings base (se podrán ajustar más abajo)
        pad_l = 68
        pad_r = 24
        pad_t = 34
        pad_b = 56  # más espacio para etiquetas X
        bg = (255, 255, 255)
        grid = (230, 236, 240)
        axis = (173, 181, 189)

        if not PIL_OK:
            # Respuesta de texto clara si Pillow no está
            self.request.response.setHeader('Content-Type', 'text/plain; charset=utf-8')
            return u'Pillow no disponible: no se puede generar el PNG en el servidor.'

        # --- Datos ---
        ar = self._get_ar(uid)
        chart = self._get_chartdata(ar)
        series = chart.get('series') or []

        # Canvas: W/H adaptativos si no llegan
        pts_per_series = [len(s['data']) for s in series] or [0]
        pts_max = max(pts_per_series)
        n_series = len(series)

        if W_param is None or H_param is None:
            # ancho por punto (más puntos => menos px por punto)
            if pts_max <= 10:
                px_per_point = 70
            elif pts_max <= 16:
                px_per_point = 56
            elif pts_max <= 24:
                px_per_point = 48
            else:
                px_per_point = 40
            W = max(1200, min(2400, max(1000, pts_max * px_per_point)))
            # Alto base + filas extra de leyenda si muchos analitos (6 por fila aprox.)
            legend_rows = int(math.ceil(max(1, n_series) / 6.0))
            extra_h = max(0, (legend_rows - 1) * 26)
            H = max(360, min(800, 380 + extra_h))
        else:
            W = max(600, _to_int(W_param, 1000))
            H = max(240, _to_int(H_param, 360))

        # Canvas
        im = Image.new('RGB', (W, H), bg)
        dr = ImageDraw.Draw(im)

        # Fuentes (usa la por defecto para compatibilidad)
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()

        # Mensaje si no hay datos
        if not series:
            msg = u'Sin datos para gráfico'
            tw, th = dr.textsize(msg, font=font)
            dr.text(((W - tw) / 2, (H - th) / 2), msg, fill=(100, 100, 100), font=font)
            out = StringIO()
            im.save(out, format='PNG', dpi=(dpi, dpi))
            self.request.response.setHeader('Content-Type', 'image/png')
            return out.getvalue()

        # Extremos globales
        xs, ys = [], []
        for s in series:
            for ms, y in s['data']:
                xs.append(ms); ys.append(y)
        xmin = min(xs); xmax = max(xs)
        ymin = min(ys); ymax = max(ys)
        ymin, ymax, yticks = self._nice_range(ymin, ymax)
        if xmin == xmax:
            xmin -= 1000; xmax += 1000

        # Funciones de escala
        plot_w = W - pad_l - pad_r
        plot_h = H - pad_t - pad_b

        def sx(ms):
            return pad_l + int((ms - xmin) * 1.0 * plot_w / (xmax - xmin))

        def sy(y):
            return pad_t + int((ymax - y) * 1.0 * plot_h / (ymax - ymin))

        # Grid Y + eje Y/X + marco del área de trazado
        for t in yticks:
            ypix = sy(t)
            dr.line([(pad_l, ypix), (W - pad_r, ypix)], fill=grid)
            lab = (u'%0.2f' % t).rstrip('0').rstrip('.')
            tw, th = dr.textsize(lab, font=font)
            dr.text((pad_l - 8 - tw, ypix - th / 2), lab, fill=(108, 117, 125), font=font)
        # Ejes
        dr.line([(pad_l, pad_t), (pad_l, H - pad_b)], fill=axis)
        dr.line([(pad_l, H - pad_b), (W - pad_r, H - pad_b)], fill=axis)
        # Marco
        dr.rectangle([(pad_l, pad_t), (W - pad_r, H - pad_b)], outline=(222, 226, 230))

        # Etiquetas X (máx 6)
        slots = 6
        if slots > len(xs):
            slots = len(xs)
        if slots > 0:
            step_ms = (xmax - xmin) / float(slots)
            for i in range(slots + 1):
                ms = xmin + int(i * step_ms)
                xpix = sx(ms)
                try:
                    dt = datetime.datetime.utcfromtimestamp(ms / 1000.0)
                    lab = dt.strftime('%d/%m %H:%M')
                except Exception:
                    lab = unicode(ms)
                tw, th = dr.textsize(lab, font=font)
                dr.text((xpix - tw / 2, H - pad_b + 4), lab, fill=(108, 117, 125), font=font)

        # Paleta de colores
        if n_series <= len(self.PALETTE):
            palette = list(self.PALETTE)
        else:
            palette = _hsv_palette(n_series)

        # Dibuja series + etiquetas de valor
        for idx, s in enumerate(series):
            color = palette[idx % len(palette)]
            pts = s['data']

            # Línea
            last = None
            for (ms, y) in pts:
                xpix = sx(ms)
                ypix = sy(y)
                if last is not None:
                    dr.line([last, (xpix, ypix)], fill=color, width=2)
                last = (xpix, ypix)

            # último punto
            if last:
                r = 3
                dr.ellipse([last[0] - r, last[1] - r, last[0] + r, last[1] + r], fill=color, outline=color)

            # Etiquetas de valor (muestradas si hay muchos puntos)
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
                    xpix = sx(ms)
                    ypix = sy(y)
                    txt = (u'%0.2f' % y).rstrip('0').rstrip('.')
                    # halo blanco para legibilidad
                    for dx, dy in ((-1,0),(1,0),(0,-1),(0,1)):
                        dr.text((xpix + dx + 4, ypix - 12 + dy), txt, fill=(255,255,255), font=font_small)
                    dr.text((xpix + 4, ypix - 12), txt, fill=(0,0,0), font=font_small)

        # Leyenda (varias filas si hace falta)
        leg_x = pad_l
        leg_y = 6
        space_x = 24
        for idx, s in enumerate(series):
            color = palette[idx % len(palette)]
            name = s['name']
            unit = s.get('unit') or u''
            label = name + (unit and (u' (' + unit.strip() + u')') or u'')
            dr.rectangle([leg_x, leg_y + 3, leg_x + 14, leg_y + 9], fill=color, outline=color)
            dr.text((leg_x + 18, leg_y), label, fill=(0, 0, 0), font=font)
            tw, th = dr.textsize(label, font=font)
            leg_x += 18 + tw + space_x
            if leg_x > W - pad_r - 120:
                leg_x = pad_l
                leg_y += th + 6

        # Título con margen suficiente bajo la leyenda
        title = u'Tendencias de Resultados'
        tw, th = dr.textsize(title, font=font)
        title_y = max(0, leg_y + th + 4)
        dr.text((pad_l, title_y), title, fill=(0, 0, 0), font=font)

        # Output
        out = StringIO()
        im.save(out, format='PNG', dpi=(dpi, dpi))
        self.request.response.setHeader('Content-Type', 'image/png')
        return out.getvalue()


# ===== NUEVO: view que devuelve Data-URI para incrustar en WeasyPrint =====
class InfolabsaTrendChartDataURI(BrowserView):
    """Devuelve data:image/png;base64,... invocando el view PNG internamente (sin HTTP)."""

    def __call__(self, uid=None, rid=None, w=None, h=None, dpi=None):
        request = self.request
        # Recuperar parámetros (permite querystring o argumentos posicionales)
        uid = uid or request.get('uid') or request.form.get('uid')
        rid = rid or request.get('rid') or request.form.get('rid')
        w = w or request.get('w') or request.form.get('w')
        h = h or request.get('h') or request.form.get('h')
        dpi = dpi or request.get('dpi') or request.form.get('dpi')

        # Guardar cabeceras previas para restaurar luego
        resp = request.response
        prev_ct = resp.getHeader('Content-Type')
        prev_cd = resp.getHeader('Content-Disposition')

        # Inyectar parámetros al request.form para el sub-view
        if uid is not None:
            request.form['uid'] = uid
        if rid is not None:
            request.form['rid'] = rid
        if w is not None:
            request.form['w'] = str(w)
        if h is not None:
            request.form['h'] = str(h)
        if dpi is not None:
            request.form['dpi'] = str(dpi)

        try:
            # Llamar al view PNG de forma interna (sin HTTP/redirects)
            png_view = self.context.restrictedTraverse('@@infolabsa-trend-chart.png')
            data = png_view()  # bytes del PNG
        finally:
            # Restaurar cabeceras que el sub-view pudo haber tocado
            if prev_ct:
                resp.setHeader('Content-Type', prev_ct)
            else:
                resp.setHeader('Content-Type', None)
            if prev_cd:
                resp.setHeader('Content-Disposition', prev_cd)
            else:
                resp.setHeader('Content-Disposition', None)

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

        # Entregamos como texto para que el <img src="..."> lo pueda usar directamente
        resp.setHeader('Content-Type', 'text/plain; charset=utf-8')
        return datauri
