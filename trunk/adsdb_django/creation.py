import sys, traceback, time, re
import os
from django.conf import settings
from django.db.backends.creation import BaseDatabaseCreation, TEST_DATABASE_PREFIX

try:
    import adsdb as Database
except ImportError, e:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured("Error loading adsdb module: %s" % e)

class DatabaseCreation(BaseDatabaseCreation):
    # This dictionary maps Field objects to their associated Advantage column
    # types, as strings. Column-type strings can contain format strings; they'll
    # be interpolated against the values of Field.__dict__ before being output.
    # If a column type is set to None, it won't be included in the output.
    # NOTE: All char type fields use varchar so that ADS automatically trims trailing spaces
    data_types = {
        'AutoField':         'autoinc',
        'BooleanField':      'logical',
        'NullBooleanField':  'logical',
        'CharField':         'nvarchar(%(max_length)s)',
        'CommaSeparatedIntegerField': 'nvarchar(%(max_length)s)',
        'DateField':         'date',
        'DateTimeField':     'timestamp',
        'DecimalField':      'numeric(%(max_digits)s, %(decimal_places)s)',
        'FileField':         'nvarchar(%(max_length)s)',
        'FilePathField':     'nvarchar(%(max_length)s)',
        'FloatField':        'double',
        'IntegerField':      'integer',
        'BigIntegerField':   'integer',
        'IPAddressField':    'nvarchar(15)',
        'OneToOneField':     'integer',
        'PhoneNubmerField':  'nvarchar(20)',
        'PositiveIntegerField': 'integer',
        'PositiveSmallIntegerField': 'short',
        'SlugField':         'nvarchar(%(max_length)s)',
        'SmallIntegerField': 'short',
        'TextField':         'nmemo',
        'TimeField':         'time',
        'USStateField':      'nvarchar(2)',
    }

    def sql_for_inline_foreign_key_references(self, field, known_models, style):
        """Don't use inline references for Advantage. This makes it
        easier to deal with conditionally creating UNIQUE constraints
        and UNIQUE indexes"""
        return [], True

    def sql_for_inline_many_to_many_references(self, model, field, style):
        from django.db import models
        opts = model._meta
        qn = self.connection.ops.quote_name

        table_output = [
            '    %s %s,' %
                (style.SQL_FIELD(qn(field.m2m_column_name())),
                style.SQL_COLTYPE(models.ForeignKey(model).db_type())),
            '    %s %s,' %
            (style.SQL_FIELD(qn(field.m2m_reverse_name())),
            style.SQL_COLTYPE(models.ForeignKey(field.rel.to).db_type()))
        ]
        deferred = [
            (field.m2m_db_table(), field.m2m_column_name(), opts.db_table,
                opts.pk.column),
            (field.m2m_db_table(), field.m2m_reverse_name(),
                field.rel.to._meta.db_table, field.rel.to._meta.pk.column)
            ]
        return table_output, deferred

    def sql_for_pending_references(self, model, style, pending_references):
        "ADSDB doesn't support constraints"
        return []

    def sql_remove_table_constraints(self, model, references_to_delete, style):
        "ADSDB doesn't support constraints"
        return []

    def _create_test_db(self, verbosity, autoclobber):
        "Internal implementation - creates the test db tables."

        test_database_name = self.connection.settings_dict['TEST_NAME']
        if test_database_name == None :
            test_database_name = 'test_ads.add'

        test_database_path = self.connection.settings_dict['NAME']

        for file in os.listdir( test_database_path ):
            if file.endswith( 'adt' ):
                os.remove( file )
            elif file.endswith( 'adm' ):
                os.remove( file )
            elif file.endswith( 'adi' ):
                os.remove( file )
            elif file.endswith( 'add' ):
                os.remove( file )
            elif file.endswith( 'ai' ):
                os.remove( file )
            elif file.endswith( 'am' ):
                os.remove( file )

        cursor = self.connection.cursor()

        try:
            cursor.execute( "CREATE DATABASE [%s];" % test_database_name )
        except Exception, e:
            traceback.print_exc()
            sys.stderr.write("Got an error creating the test database: %s\n" % e)
            sys.exit(2)

        cursor.close()
        test_database_path = self.connection.settings_dict['NAME']
        self.connection.settings_dict['NAME'] = '%s/%s' % (test_database_path, test_database_name)
        return test_database_name

    def _destroy_test_db(self, test_database_name, verbosity):
        "Internal implementation - remove the test db tables."
        self.connection.close()


    def _rollback_works(self):
        "Test to determine if rollbacks work" # needed by the django test suite
        cursor = self.connection.cursor()
        cursor.execute('CREATE TABLE ROLLBACK_TEST (X INTEGER)')
        self.connection._commit()
        cursor.execute('BEGIN TRANSACTION')
        cursor.execute('INSERT INTO ROLLBACK_TEST (X) VALUES (8)')
        self.connection._rollback()
        cursor.execute('SELECT COUNT(X) FROM ROLLBACK_TEST')
        count, = cursor.fetchone()
        cursor.execute('DROP TABLE ROLLBACK_TEST')
        self.connection._commit()
        return count == 0

    def _unique_swap(self, query, fields, model, style, table=None):
        """
        Fix unique constraints on multiple fields
        Build unique indexes instead of unique constraints

        Follows SQL generation from
        django.db.creation.BaseDatabaseCreation.sql_create_model
        """
        opts = model._meta
        qn = self.connection.ops.quote_name

        if table == None:
            table = opts.db_table

        fields_str = ", ".join([style.SQL_FIELD(qn(f)) for f in fields])
        multi_name = style.SQL_FIELD(qn("_".join(f for f in fields)))
        unique_str = 'UNIQUE (%s)' % fields_str
        unique_re_str = re.escape(unique_str) + '[,]?\n'
        query = re.sub(unique_re_str, '', query)

        idx_query = 'CREATE UNIQUE INDEX %s ON %s (%s);' % \
            (multi_name, style.SQL_FIELD(qn(table)), fields_str)
        return [query, idx_query]

    def _unique_swap_many(self, queries, fields, model, style, table=None):
        for i, query in enumerate(queries):
            changes = self._unique_swap(query, fields, model, style, table=table)
            if changes[0] != query:
                queries[i] = changes[0]
                queries.append(changes[1])
        
        return queries

    def sql_create_model(self, model, style, known_models=set()):
        """
        Returns the SQL required to create a single model, as a tuple of:
            (list_of_sql, pending_references_dict)
        """

        # Let BaseDatabaseCreation do most of the work
        opts = model._meta

        unique_nullable_fields = []

        for f in opts.local_fields:
            if self.connection.ops.ads_table_type == 'ADT':
                # Set all ADT fields to be nullable so we don't get NOT NULL in the SQL
                f.null = True
            if f.unique and f.null:
                unique_nullable_fields.append(f)
                f._unique = False

        outputs, pending = super(DatabaseCreation,self).sql_create_model(model,style,known_models)

        # Set null to False for BooleanFields so they don't fail validation later
        from django.db import models
        for f in opts.local_fields:
            if isinstance( f, models.BooleanField ):
                f.null = False

        qn = self.connection.ops.quote_name
        for f in unique_nullable_fields:
            f._unique = True
            # Primary key fields automatically get a UNIQUE index, so no need to create another one here
            if not f.primary_key:
                outputs.append("CREATE UNIQUE INDEX %s on %s(%s);" % ("%s_%s_UNIQUE" % (opts.db_table, f.column), qn(opts.db_table), qn(f.column)))

        for field_constraints in opts.unique_together:
            fields = [opts.get_field(f).column for f in field_constraints]
            outputs = self._unique_swap_many(outputs, fields, model, style)

        return outputs, pending

    def sql_for_many_to_many_field(self, model, f, style):
        "ADS doesn't support relations with django"
        return []
