from abc import ABCMeta, abstractproperty
from . import server as _server
import functools


class SObjectMeta(ABCMeta):

    subclasses = dict()

    def __new__(mcls, name, bases, namespace):
        cls = super(SObjectMeta, mcls).__new__(mcls, name, bases, namespace)
        _server.TacticObjectServer.register_sobject_class(cls)
        return cls


class FuncOverride(object):
    func = None

    def __init__(self, func):
        self.func = func
        self.__doc__ = func.__doc__

    def __get__(self, obj, cls):
        if obj:
            _func = functools.partial(self.func, obj.conn, obj.search_key)
            _func.__doc__ = self.func.__doc__
            _func.__name__ = self.func.__name__
            return _func
        else:
            return self


class RelatedSObject(object):
    __stype__ = ''
    __show_retired__ = False

    def __init__(self, stype, show_retired=False):
        self.__stype__ = stype
        self.__show_retired__ = show_retired

    def __get__(self, obj, cls):
        return obj.conn.eval('@SOBJECT(%s[id, %s].%s)' % (
            obj.__stype__, obj.id, self.__stype__))


class ParentSObject(RelatedSObject):
    __key__ = None

    def __init__(self, stype, key, show_retired=False):
        super(ParentSObject, self).__init__(stype, show_retired=show_retired)
        self.__key__ = key

    def __get__(self, obj, cls):
        return obj.conn.query(
                self.__stype__,
                filters=[('code', obj.get_field(self.__key__))],
                show_retired=self.__show_retired__, single=True)

    def __set__(self, obj, value):
        if isinstance(value, SObject):
            value = value.code
        obj.__data__[self.__key__] = value


class ChildSObject(RelatedSObject):

    def __get__(self, obj, cls):
        return obj.conn.eval('@SOBJECT(%s)' % self.__stype__, obj.search_key)


class ChildSnapshot(ChildSObject):

    def __init__(self, show_retired=False):
        super(ChildSnapshot, self).__init__(
                'sthpw/snapshot', show_retired=show_retired)

    def __get__(self, obj, cls):
        search_key = obj.search_key
        stype, params = search_key.split('?')
        params = dict([tuple(x.split('=')) for x in params.split('&')])

        if stype == 'sthpw/project':
            filters = [('project_code', params.get('code'))]
        else:
            if 'project' in params:
                project = params.get('project')
                filters = [('search_type', '%s?project=%s' % (stype, project))]
                filters.append(('project_code', params.get('project')))
            else:
                filters = [('search_type', obj.__stype__)]
            if 'code' in params:
                filters.append(('search_code', params.get('code')))
            if 'id' in params:
                filters.append(('search_id', 'id'))

        if not filters:
            return []

        return obj.conn.query_snapshots(
                filters=filters, include_paths=True, include_paths_dict=True,
                include_parent=True, include_files=True)


class SObjectField(object):
    __key__ = None
    __force__ = False

    def __init__(self, key, force=True):
        self.__key__ = key
        self.__force__ = force

    def __get__(self, obj, cls):
        ''':retval: str'''
        if self.__key__ in obj.__data__:
            return obj.__data__[self.__key__]
        elif self.__force__ and self.__key__ in obj.conn.get_column_names(
                cls.__stype__):
            self.__data__ = obj.conn.get_by_search_key(obj.search_key).__data__
            return obj.__data__[self.__key__]
        else:
            raise AttributeError('%s is not a valid key for %s object' % (
                self.__key__, obj.__stype__))

    def __set__(self, obj, value):
        if self.__key__ in obj.__data__:
            obj.__data__[self.__key__] = value
        elif self.__force__ and self.__key__ in obj.conn.get_column_names(
                obj.__stype__):
            self.__data__[self.__key__] = value
        else:
            raise AttributeError('%s is not a valid key for %s object' % (
                self.__key__, obj.__stype__))


class CachedObjectField(object):
    __stype__ = ''
    __key__ = None

    def __init__(self, key, stype=''):
        self.__key__ = key
        self.__type__ = stype

    def __get__(self, obj, cls):
        value = obj.data[self.__key__]
        if obj.conn.is_sobj_dict(value):
            value = obj.conn.wrap_sobject_class(
                    value, obj.conn)
        elif isinstance(value, list) and all(
                (True if obj.conn.is_sobj_dict(member)
                    else False for member in value)):
            value = [obj.conn.wrap_sobject_class(member, obj.conn)
                     for member in value]
        return value


class Context(object):
    _sobject = None
    _context = ''

    def __init__(self, context, sobject=None, conn=None):
        self._sobject = sobject
        self._context = context

    def __repr__(self):
        return "Context(%s, %r)" % (self._context, self._sobject)

    def get_latest(self, versionless=False):
        return self._sobject.get_snapshot(
                context=self.context, version=-1, versionless=versionless,
                include_paths=False, include_full_xml=False,
                include_paths_dict=False, include_web_paths_dict=False)

    def get_current(self, versionless=False):
        return self._sobject.get_snapshot(
                context=self.context, version=0, versionless=versionless,
                include_paths=False, include_full_xml=False,
                include_paths_dict=False, include_web_paths_dict=False)

    def has_versionless(self):
        pass

    def process(self, context):
        context = context or self._context
        return context.split('/')[0]

    def get_process(self):
        return Context(self.process(), self.sobject)

    @property
    def snapshots(self):
        self._sobject.query_snapshots(context=self._context)


class SObject(object):
    __metaclass__ = SObjectMeta
    __data__ = None

    conn = _server.Connection()

    search_key = SObjectField('__search_key__')
    code = SObjectField('code', False)
    description = SObjectField('description', True)
    id = SObjectField('id', True)
    name = SObjectField('name', True)
    retire_status = SObjectField('retire_status', True)
    status = SObjectField('status', True)
    timestamp = SObjectField('timestamp', True)

    snapshots = ChildSnapshot()
    tasks = ChildSObject('sthpw/task')

    def __init__(self, data, conn=None):
        self.conn = conn
        stype, code = self.conn.split_search_key(data['__search_key__'])
        stype = stype.split('?')[0]
        self.__data__ = data
        if stype == self.__stype__:
            self.__data__ = {
                    key: value if value is not None else ''
                    for key, value in data.items()}
        else:
            raise TypeError(
                    'provided data does not refer to an %s object' %
                    self.__stype__)

    @abstractproperty
    def __stype__(self):
        pass

    def __repr__(self):
        skey = self.__data__['__search_key__']
        return self.__class__.__name__ + "({'__search_key__': '%s'})" % skey

    @property
    def data(self):
        return self.__data__

    def get_field(self, key):
        return self.__data__[key]

    def set_field(self, key, value):
        self.__data__[key] = value

    @classmethod
    def query(cls, filters=[], columns=[], order_bys=[], show_retired=False,
              limit=None, offset=None, single=False):
        return cls.conn.query(
                cls.__stype__, filters=filters, columns=columns,
                order_bys=order_bys, show_retired=show_retired, limit=limit,
                offset=offset, single=single)

    @classmethod
    def fast_query(cls, filters=[], limit=None):
        return cls.conn.fast_query(cls.__stype__, filters=filters, limit=None)

    @classmethod
    def get_by_code(cls, code):
        return cls.conn.get_by_code(cls.__stype__, code)

    @classmethod
    def get_unique_sobject(cls):
        return cls.conn.get_unique_sobject(cls.__stype__)

    @classmethod
    def get_column_names(cls):
        return cls.conn.get_column_names(cls.__stype__)

    def get_by_search_key(self):
        obj = self.conn.get_by_search_key(self.search_key)
        self.__data__.update(obj.__data__)
        return obj
    get_by_search_key.__doc__ = \
        _server.TacticObjectServer.get_by_search_key.__doc__

    def connect_sobject(self, dest, context='default'):
        return self.conn.connect_sobjects(
                self.search_key, dest.search_key, context)
    connect_sobject.__doc__ = \
        _server.TacticObjectServer.connect_sobjects.__doc__

    def insert_update(self, metadata={}, parent_key=None, info={},
                      use_id=False, triggers=False):
        obj = self.conn.insert_update(
                self.search_key, self.__data__, metadata=metadata,
                parent_key=None, info=info, use_id=use_id, triggers=False)
        self.__data__ = obj.__data__
        return obj
    insert_update.__doc__ = _server.TacticObjectServer.insert_update.__doc__

    def insert(self, *args, **kwargs):
        return self.conn.insert(
                self.search_key, self.__data__, *args, **kwargs)
    insert.__doc__ = _server.TacticObjectServer.insert.__doc__

    def update(self, *args, **kwargs):
        return self.conn.update(
                self.search_key, self.__data__, *args, **kwargs)
    update.__doc__ = _server.TacticObjectServer.update.__doc__

    def get_context(self, context):
        return Context(context, self)

    reactivate = FuncOverride(
            _server.TacticObjectServer.reactivate_sobject)
    retire = FuncOverride(
            _server.TacticObjectServer.retire_sobject)
    update = FuncOverride(
            _server.TacticObjectServer.update)
    delete = FuncOverride(
            _server.TacticObjectServer.delete_sobject)
    simple_checkin = FuncOverride(
            _server.TacticObjectServer.simple_checkin)
    group_checkin = FuncOverride(
            _server.TacticObjectServer.group_checkin)
    directory_checkin = FuncOverride(
            _server.TacticObjectServer.directory_checkin)
    checkout = FuncOverride(
            _server.TacticObjectServer.checkout)
    get_snapshot = FuncOverride(
            _server.TacticObjectServer.get_snapshot)
    get_parent = FuncOverride(
            _server.TacticObjectServer.get_parent)
    get_all_children = FuncOverride(
            _server.TacticObjectServer.get_all_children)
    get_connected_sobject = FuncOverride(
            _server.TacticObjectServer.get_connected_sobject)
    get_connected_sobjects = FuncOverride(
            _server.TacticObjectServer.get_connected_sobjects)


class UnknownSObject(SObject):
    __stype__ = '*'

    def __new__(cls, data, conn=None):
        self = super(UnknownSObject, cls).__new__(cls, data, conn)
        self.__stype__ = cls.conn.get_stype(data)
        return self
