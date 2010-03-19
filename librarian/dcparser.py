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
from xml.parsers.expat import ExpatError
from datetime import date
import time

from librarian import ValidationError, NoDublinCore, ParseError, DCNS, RDFNS

import lxml.etree as etree # ElementTree API using libxml2
from lxml.etree import XMLSyntaxError


# ==============
# = Converters =
# ==============
class Person(object):
    """Single person with last name and a list of first names."""
    def __init__(self, last_name, *first_names):
        self.last_name = last_name
        self.first_names = first_names
        
    @classmethod
    def from_text(cls, text):
        parts = [ token.strip() for token in text.split(',') ]
        if len(parts) == 1:
            surname = parts[0]
            names = []
        elif len(parts) != 2:
            raise ValueError("Invalid person name. There should be at most one comma: \"%s\"." % text)
        else:
            surname = parts[0]
            if len(parts[1]) == 0:
                # there is no non-whitespace data after the comma
                raise ValueError("Found a comma, but no names given: \"%s\" -> %r." % (text, parts))
            names = [ name for name in parts[1].split() if len(name) ] # all non-whitespace tokens
        return cls(surname, *names)
    
    def __eq__(self, right):
        return self.last_name == right.last_name and self.first_names == right.first_names
    
    
    def __unicode__(self):
        if len(self.first_names) > 0:
            return '%s, %s' % (self.last_name, ' '.join(self.first_names))
        else:
            return self.last_name
        
    def __repr__(self):
        return 'Person(last_name=%r, first_names=*%r)' % (self.last_name, self.first_names)

def as_date(text):
    try:
        try:
            t = time.strptime(text, '%Y-%m-%d')
        except ValueError:
            t = time.strptime(text, '%Y')
        return date(t[0], t[1], t[2])
    except ValueError, e:
        raise ValueError("Unrecognized date format. Try YYYY-MM-DD or YYYY.")

def as_person(text):
    return Person.from_text(text)

def as_unicode(text):
    if isinstance(text, unicode):
        return text
    else:
        return text.decode('utf-8')

class Field(object):
    def __init__(self, uri, attr_name, type=as_unicode, multiple=False, salias=None, **kwargs):
        self.uri = uri
        self.name = attr_name
        self.validator = type
        self.multiple = multiple
        self.salias = salias

        self.required = kwargs.get('required', True) and not kwargs.has_key('default')
        self.default = kwargs.get('default', [] if multiple else [None])

    def validate_value(self, val):
        try:
            if self.multiple:
                if self.validator is None:
                    return val
                return [ self.validator(v) if v is not None else v for v in val ]
            elif len(val) > 1:
                raise ValidationError("Mulitply values not allowed for field '%s'" % self.uri)
            elif len(val) == 0:
                raise ValidationError("Field %s has no value to assign. Check your defaults." % self.uri)
            else:
                if self.validator is None or val[0] is None:
                    return val[0]
                return self.validator(val[0])
        except ValueError, e:
            raise ValidationError("Field '%s' - invald value: %s" % (self.uri, e.message))

    def validate(self, fdict):
        if not fdict.has_key(self.uri):
            if not self.required:
                f = self.default
            else:
                raise ValidationError("Required field %s not found" % self.uri)
        else:
            f = fdict[self.uri]

        return self.validate_value(f)




class BookInfo(object):    
    FIELDS = (
        Field( DCNS('creator'), 'author', as_person),
        Field( DCNS('title'), 'title'),
        Field( DCNS('subject.period'), 'epochs', salias='epoch', multiple=True),
        Field( DCNS('subject.type'), 'kinds', salias='kind', multiple=True),
        Field( DCNS('subject.genre'), 'genres', salias='genre', multiple=True),
        Field( DCNS('date'), 'created_at', as_date),
        Field( DCNS('date.pd'), 'released_to_public_domain_at', as_date, required=False),
        Field( DCNS('contributor.editor'), 'editors', \
            as_person, salias='editor', multiple=True, default=[]),
        Field( DCNS('contributor.translator'), 'translators', \
            as_person,  salias='translator', multiple=True, default=[]),
        Field( DCNS('contributor.technical_editor'), 'technical_editors',
            as_person, salias='technical_editor', multiple=True, default=[]),
        Field( DCNS('publisher'), 'publisher'),
        Field( DCNS('source'), 'source_name', required=False),
        Field( DCNS('source.URL'), 'source_url', required=False),
        Field( DCNS('identifier.url'), 'url'),
        Field( DCNS('relation.hasPart'), 'parts', multiple=True, required=False),
        Field( DCNS('rights.license'), 'license', required=False),
        Field( DCNS('rights'), 'license_description'),
    )

    @classmethod
    def from_string(cls, xml):
        from StringIO import StringIO
        return cls.from_file(StringIO(xml))
   
    @classmethod
    def from_file(cls, xmlfile):
        desc_tag = None        
        try:
            iter = etree.iterparse(xmlfile, ['start', 'end'])            
            for (event, element) in iter:
                if element.tag == RDFNS('RDF') and event == 'start':
                    desc_tag = element
                    break

            if desc_tag is None:
                raise NoDublinCore("DublinCore section not found. \
                    Check if there are rdf:RDF and rdf:Description tags.")

            # continue 'till the end of RDF section
            for (event, element) in iter:
                if element.tag == RDFNS('RDF') and event == 'end':
                    break

            # if there is no end, Expat should yell at us with an ExpatError
            
            # extract data from the element and make the info
            return cls.from_element(desc_tag)
        except XMLSyntaxError, e:
            raise ParseError(e)
        except ExpatError, e:
            raise ParseError(e)

    @classmethod
    def from_element(cls, rdf_tag):
        # the tree is already parsed, so we don't need to worry about Expat errors
        field_dict = {}
        desc = rdf_tag.find(".//" + RDFNS('Description') )
        
        if desc is None:
            raise NoDublinCore("No DublinCore section found.")

        for e in desc.getchildren():
            fv = field_dict.get(e.tag, [])
            fv.append(e.text)
            field_dict[e.tag] = fv
                
        return cls( desc.attrib, field_dict )

    def __init__(self, rdf_attrs, dc_fields):
        """rdf_attrs should be a dictionary-like object with any attributes of the RDF:Description.
        dc_fields - dictionary mapping DC fields (with namespace) to list of text values for the 
        given field. """

        self.about = rdf_attrs.get(RDFNS('about'))
        self.fmap = {}

        for field in self.FIELDS:
            value = field.validate( dc_fields )
            setattr(self, 'prop_' + field.name, value)
            self.fmap[field.name] = field
            if field.salias: self.fmap[field.salias] = field

    def __getattribute__(self, name):
        try:
            field = object.__getattribute__(self, 'fmap')[name]
            value = object.__getattribute__(self, 'prop_'+field.name)
            if field.name == name:
                return value
            else: # singular alias
                if not field.multiple:
                    raise "OUCH!! for field %s" % name
                
                return value[0]
        except (KeyError, AttributeError):
            return object.__getattribute__(self, name)

    def __setattr__(self, name, newvalue):
        try:
            field = object.__getattribute__(self, 'fmap')[name]
            if field.name == name:
                object.__setattr__(self, 'prop_'+field.name, newvalue)
            else: # singular alias
                if not field.multiple:
                    raise "OUCH! while setting field %s" % name

                object.__setattr__(self, 'prop_'+field.name, [newvalue])
        except (KeyError, AttributeError):
            return object.__setattr__(self, name, newvalue)

    def update(self, field_dict):
        """Update using field_dict. Verify correctness, but don't check if all 
        required fields are present."""
        for field in self.FIELDS:
            if field_dict.has_key(field.name):
                setattr(self, field.name, field_dict[field.name])

    def to_etree(self, parent = None):
        """XML representation of this object."""
        #etree._namespace_map[str(self.RDF)] = 'rdf'
        #etree._namespace_map[str(self.DC)] = 'dc'
        
        if parent is None:
            root = etree.Element(RDFNS('RDF'))
        else:
            root = parent.makeelement(RDFNS('RDF'))

        description = etree.SubElement(root, RDFNS('Description'))
        
        if self.about:
            description.set(RDFNS('about'), self.about)
        
        for field in self.FIELDS:
            v = getattr(self, field.name, None)
            if v is not None:
                if field.multiple:
                    if len(v) == 0: continue
                    for x in v:
                        e = etree.Element(field.uri)
                        e.text = unicode(x)
                        description.append(e)
                else:
                    e = etree.Element(field.uri)
                    e.text = unicode(v)
                    description.append(e)
        
        return root


    def serialize(self):
        rdf = {}
        rdf['about'] = { 'uri': RDFNS('about'), 'value': self.about }

        dc = {}
        for field in self.FIELDS:
            v = getattr(self, field.name, None)
            if v is not None:
                if field.multiple:
                    if len(v) == 0: continue
                    v = [ unicode(x) for x in v if v is not None ]
                else:
                    v = unicode(v)
                    
                dc[field.name] = {'uri': field.uri, 'value': v}
        rdf['fields'] = dc
        return rdf

    def to_dict(self):
        result = {'about': self.about}
        for field in self.FIELDS:
            v = getattr(self, field.name, None)

            if v is not None:
                if field.multiple:
                    if len(v) == 0: continue
                    v = [ unicode(x) for x in v if v is not None ]
                else:
                    v = unicode(v)
                result[field.name] = v

            if field.salias:
                v = getattr(self, field.salias)
                if v is not None: result[field.salias] = unicode(v)
        
        return result

def parse(file_name):
    return BookInfo.from_file(file_name)