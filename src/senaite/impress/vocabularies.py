# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.IMPRESS.
#
# SENAITE.IMPRESS is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

from senaite.impress import config
from senaite.impress import senaiteMessageFactory as _
from senaite.impress.interfaces import ITemplateFinder
from zope.component import getUtility
from zope.interface import implementer
from zope.schema.interfaces import IVocabularyFactory
from zope.schema.vocabulary import SimpleTerm
from zope.schema.vocabulary import SimpleVocabulary


@implementer(IVocabularyFactory)
class TemplateVocabulary(object):
    def __call__(self, context):
        finder = getUtility(ITemplateFinder)
        templates = finder.get_templates()

        # --- AJUSTE: remapear SOLO el título visible para la plantilla INFOLABSA ---
        # Mantiene el token/valor interno (no rompe configuraciones existentes).
        remap_display = {
            u"senaite.impress:Infolabsa.pt": u"infolabsa.pdf:INFOLABSA.pt",
            u"senaite.impress:INFOLABSA.pt": u"infolabsa.pdf:INFOLABSA.pt",
        }
        items = []
        for t in templates:
            # Por compatibilidad con versiones, asumimos que t[0] es el token/nombre canónico
            token = t[0]
            title = remap_display.get(token, token)  # cambiar solo lo que se muestra
            items.append(SimpleTerm(token, token, title))
        # --- FIN AJUSTE ---

        return SimpleVocabulary(items)

TemplateVocabularyFactory = TemplateVocabulary()  # noqa


@implementer(IVocabularyFactory)
class PaperformatVocabulary(object):
    def __call__(self, context):
        # XXX make paperformats configurable
        items = []
        for k, v in config.PAPERFORMATS.items():
            title = "{} {}x{}mm".format(k, v["page_width"], v["page_height"])
            items.append(SimpleTerm(k, v, title))
        return SimpleVocabulary(items)

PaperformatVocabularyFactory = PaperformatVocabulary()  # noqa


@implementer(IVocabularyFactory)
class OrientationVocabulary(object):
    def __call__(self, context):
        items = []
        for format in ["portrait", "landscape"]:
            items.append(SimpleTerm(format, format, _(format)))
        return SimpleVocabulary(items)

OrientationVocabularyFactory = OrientationVocabulary()  # noqa
