# -*- coding: utf-8 -*-
#
#    This file is part of Librarian.
#
#    Copyright © 2008,2009,2010 Fundacja Nowoczesna Polska <fundacja@nowoczesnapolska.org.pl>
#    
#    For full list of contributors see AUTHORS file. 
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import os
import cStringIO
import re
import copy

from lxml import etree
from librarian.parser import WLDocument
from librarian import XHTMLNS, ParseError

from lxml.etree import XMLSyntaxError, XSLTApplyError

ENTITY_SUBSTITUTIONS = [
    (u'---', u'—'),
    (u'--', u'–'),
    (u'...', u'…'),
    (u',,', u'„'),
    (u'"', u'”'),
]

STYLESHEETS = {
    'legacy': 'xslt/book2html.xslt',
    'full': 'xslt/wl2html_full.xslt',
    'partial': 'xslt/wl2html_partial.xslt'
}

def get_stylesheet(name):
    return os.path.join(os.path.dirname(__file__), STYLESHEETS[name])

def substitute_entities(context, text):
    """XPath extension function converting all entites in passed text."""
    if isinstance(text, list):
        text = ''.join(text)
    for entity, substitutution in ENTITY_SUBSTITUTIONS:
        text = text.replace(entity, substitutution)
    return text

# Register substitute_entities function with lxml
ns = etree.FunctionNamespace('http://wolnelektury.pl/functions')
ns['substitute_entities'] = substitute_entities

def transform(input, output_filename=None, is_file=True, \
    parse_dublincore=True, stylesheet='legacy', options={}):
    """Transforms file input_filename in XML to output_filename in XHTML."""
    # Parse XSLT
    try:
        style_filename = get_stylesheet(stylesheet)
        style = etree.parse(style_filename)

        if is_file:
            document = WLDocument.from_file(input, True, \
                parse_dublincore=parse_dublincore)
        else:
            document = WLDocument.from_string(input, True, \
                parse_dublincore=parse_dublincore)

        result = document.transform(style, **options)
        del document # no longer needed large object :)        
        
        if etree.ETXPath('//p|//{%s}p' % str(XHTMLNS))(result) is not None:
            add_anchors(result.getroot())
            add_table_of_contents(result.getroot())
        
            if output_filename is not None:
                result.write(output_filename, xml_declaration=False, pretty_print=True, encoding='utf-8')
            else:
                return result
            return True
        else:
            return "<empty />"
    except KeyError:
        raise ValueError("'%s' is not a valid stylesheet.")
    except (XMLSyntaxError, XSLTApplyError), e:
        raise ParseError(e)

class Fragment(object):
    def __init__(self, id, themes):
        super(Fragment, self).__init__()
        self.id = id
        self.themes = themes
        self.events = []

    def append(self, event, element):
        self.events.append((event, element))

    def closed_events(self):
        stack = []
        for event, element in self.events:
            if event == 'start':
                stack.append(('end', element))
            elif event == 'end':
                try:
                    stack.pop()
                except IndexError:
                    print 'CLOSED NON-OPEN TAG:', element

        stack.reverse()
        return self.events + stack

    def to_string(self):
        result = []
        for event, element in self.closed_events():
            if event == 'start':
                result.append(u'<%s %s>' % (element.tag, ' '.join('%s="%s"' % (k, v) for k, v in element.attrib.items())))
                if element.text:
                    result.append(element.text)
            elif event == 'end':
                result.append(u'</%s>' % element.tag)
                if element.tail:
                    result.append(element.tail)
            else:
                result.append(element)

        return ''.join(result)

    def __unicode__(self):
        return self.to_string()


def extract_fragments(input_filename):
    """Extracts theme fragments from input_filename."""
    open_fragments = {}
    closed_fragments = {}

    for event, element in etree.iterparse(input_filename, events=('start', 'end')):
        # Process begin and end elements
        if element.get('class', '') in ('theme-begin', 'theme-end'):
            if not event == 'end': continue # Process elements only once, on end event

            # Open new fragment
            if element.get('class', '') == 'theme-begin':
                fragment = Fragment(id=element.get('fid'), themes=element.text)

                # Append parents
                if element.getparent().get('id', None) != 'book-text':
                    parents = [element.getparent()]
                    while parents[-1].getparent().get('id', None) != 'book-text':
                        parents.append(parents[-1].getparent())

                    parents.reverse()
                    for parent in parents:
                        fragment.append('start', parent)

                open_fragments[fragment.id] = fragment

            # Close existing fragment
            else:
                try:
                    fragment = open_fragments[element.get('fid')]
                except KeyError:
                    print '%s:closed not open fragment #%s' % (input_filename, element.get('fid'))
                else:
                    closed_fragments[fragment.id] = fragment
                    del open_fragments[fragment.id]

            # Append element tail to lost_text (we don't want to lose any text)
            if element.tail:
                for fragment_id in open_fragments:
                    open_fragments[fragment_id].append('text', element.tail)


        # Process all elements except begin and end
        else:
            # Omit annotation tags
            if len(element.get('name', '')) or element.get('class', '') == 'annotation':
                if event == 'end' and element.tail:
                    for fragment_id in open_fragments:
                        open_fragments[fragment_id].append('text', element.tail)
            else:
                for fragment_id in open_fragments:
                    open_fragments[fragment_id].append(event, copy.copy(element))

    return closed_fragments, open_fragments


def add_anchor(element, prefix, with_link=True, with_target=True, link_text=None):
    if with_link:
        if link_text is None:
            link_text = prefix
        anchor = etree.Element('a', href='#%s' % prefix)
        anchor.set('class', 'anchor')
        anchor.text = unicode(link_text)
        if element.text:
            anchor.tail = element.text
            element.text = u''
        element.insert(0, anchor)
    
    if with_target:
        anchor_target = etree.Element('a', name='%s' % prefix)
        anchor_target.set('class', 'target')
        anchor_target.text = u' '
        if element.text:
            anchor_target.tail = element.text
            element.text = u''
        element.insert(0, anchor_target)


def any_ancestor(element, test):
    for ancestor in element.iterancestors():
        if test(ancestor):
            return True
    return False


def add_anchors(root):
    counter = 1
    for element in root.iterdescendants():
        if any_ancestor(element, lambda e: e.get('class') in ('note', 'motto', 'motto_podpis', 'dedication')
        or e.tag == 'blockquote'):
            continue
        
        if element.tag == 'p' and 'verse' in element.get('class', ''):
            if counter == 1 or counter % 5 == 0:
                add_anchor(element, "f%d" % counter, link_text=counter)
            counter += 1
        elif 'paragraph' in element.get('class', ''):
            add_anchor(element, "f%d" % counter, link_text=counter)
            counter += 1


def add_table_of_contents(root):
    sections = []
    counter = 1
    for element in root.iterdescendants():
        if element.tag in ('h2', 'h3'):
            if any_ancestor(element, lambda e: e.get('id') in ('footnotes',) or e.get('class') in ('person-list',)):
                continue
            
            if element.tag == 'h3' and len(sections) and sections[-1][1] == 'h2':
                sections[-1][3].append((counter, element.tag, ''.join(element.xpath('text()')), []))
            else:
                sections.append((counter, element.tag, ''.join(element.xpath('text()')), []))
            add_anchor(element, "s%d" % counter, with_link=False)
            counter += 1
    
    toc = etree.Element('div')
    toc.set('id', 'toc')
    toc_header = etree.SubElement(toc, 'h2')
    toc_header.text = u'Spis treści'
    toc_list = etree.SubElement(toc, 'ol')

    for n, section, text, subsections in sections:
        section_element = etree.SubElement(toc_list, 'li')
        add_anchor(section_element, "s%d" % n, with_target=False, link_text=text)
        
        if len(subsections):
            subsection_list = etree.SubElement(section_element, 'ol')
            for n, subsection, text, _ in subsections:
                subsection_element = etree.SubElement(subsection_list, 'li')
                add_anchor(subsection_element, "s%d" % n, with_target=False, link_text=text)
    
    root.insert(0, toc)
