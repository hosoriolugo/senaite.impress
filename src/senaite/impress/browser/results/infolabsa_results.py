# -*- coding: utf-8 -*-
hi = u'' if high is None else unicode(high)
ref_range = (lo + u' – ' + hi).strip()
else:
ref_range = u''


estado_symbol = u'—'
estado_text = u'—'
estado_class = u''
alert_text = u''
alert_title = u''
alert_classes = u''


rnum = self._num(result)
lnum = self._num(low)
hnum = self._num(high)


if rnum is not None and (lnum is not None or hnum is not None):
in_low = (lnum is None) or (rnum >= lnum)
in_high = (hnum is None) or (rnum <= hnum)
if in_low and in_high:
estado_symbol = u'✓'
estado_text = u'En rango'
estado_class = u'estado-ok'
else:
estado_symbol = u'⚠'
estado_text = u'Fuera de rango'
estado_class = u'estado-fr'
alert_text = u'⚠'
alert_title = u'Resultado fuera de rango de referencia'
alert_classes = u'fr-alert'


critical = (self._get(a, 'getCritical') or self._get(a, 'isCritical') or False)
if critical:
estado_symbol = u'❗'
estado_text = u'Crítico'
estado_class = u'estado-critical'
alert_text = u'❗'
alert_title = (alert_title + u' | ' if alert_title else u'') + u'Valor crítico'
alert_classes = (alert_classes + u' ' if alert_classes else u'') + u'al-critical'


return {
'name': title or u'—',
'result': result,
'unit': unit or u'',
'ref_range': ref_range or u'—',
'estado_symbol': estado_symbol,
'estado_text': estado_text,
'estado_class': estado_class,
'alert_text': alert_text or u'—',
'alert_title': alert_title,
'alert_classes': alert_classes,
}